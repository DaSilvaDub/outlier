import csv
import json

import pytest
from google import genai

from outlier_scrapers import gemini_research, pack, pack_index, verdicts


@pytest.fixture
def gemini_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_research.paths, "PROJECT_ROOT", tmp_path)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "B.md").write_text("Prompt B body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-06-27\nbriefing content", encoding="utf-8")

    # Pass B now receives the authoritative candidates ledger, not just the
    # briefing: it cannot emit an outcome_id-keyed verdict envelope without it.
    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pack.CANDIDATES_HEADER)
        w.writeheader()
        row = {k: "" for k in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "market_id": "m1",
                "selection": "test",
                "team_name": "A",
                "opp_name": "B",
                "matchup": "A @ B",
                "_event_starts_at": "2099-01-01T12:00:00Z",
            }
        )
        w.writerow(row)

    return tmp_path, date_str, pack_dir


def _valid_envelope(pack_dir) -> str:
    """A schema-valid, gate-clean pass-B envelope for the pack as it stands now.

    `verdicts` is deliberately empty: these tests cover runner mechanics, not
    verdict content (tests/test_pass_b_d_publish.py covers that against a real
    indexed row). The three pack hashes must match the live pack or the gate
    rejects with pack_mismatch, so this is built at call time.
    """
    index = pack_index.build_pack_index(
        pack_dir, policy_path=pack_dir / "no-such-portfolio-policy.json"
    )
    return json.dumps(
        {
            "schema_version": verdicts.SCHEMA_VERSION,
            "pass": "B",
            "pack_date": pack_dir.name,
            "candidates_sha256": index.candidates_sha256,
            "game_totals_sha256": index.game_totals_sha256,
            "team_totals_sha256": index.team_totals_sha256,
            "verdicts": [],
            "slate_notes": [],
            "needs": [],
        }
    )


class MockResponse:
    def __init__(self, text):
        self.text = text


class MockModels:
    def __init__(self, pack_dir=None):
        self.called = False
        self.kwargs = {}
        # None => emit a valid envelope at call time. Tests that want a
        # specific (usually invalid) payload assign `response` directly.
        self.response = None
        self._pack_dir = pack_dir

    def generate_content(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        if self.response is None:
            return MockResponse(_valid_envelope(self._pack_dir))
        return self.response


class MockGenaiClient:
    def __init__(self, *args, pack_dir=None, **kwargs):
        self.client_kwargs = kwargs
        self.models = MockModels(pack_dir)


@pytest.fixture
def mock_genai(monkeypatch, gemini_env):
    _, _, pack_dir = gemini_env
    clients = []

    def mock_init(*args, **kwargs):
        c = MockGenaiClient(*args, pack_dir=pack_dir, **kwargs)
        clients.append(c)
        return c

    monkeypatch.setattr(genai, "Client", mock_init)
    return clients


def test_validation_missing_briefing(gemini_env):
    _, date_str, pack_dir = gemini_env
    (pack_dir / "briefing.md").unlink()
    assert gemini_research.main(["--date", date_str]) == 1


def test_success_writes_file_and_grounding_enabled(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    expected_hash = gemini_research.expected_request_sha256(pack_dir)
    config_definition = gemini_research.provider_configuration()
    assert config_definition["provider"] == "google_gemini"
    assert config_definition["grounding"] == "google_search"
    assert "key" not in json.dumps(config_definition).lower()
    assert gemini_research.main(["--date", date_str]) == 0
    assert len(mock_genai) == 1

    models = mock_genai[0].models
    assert models.called
    assert models.kwargs["model"] == "gemini-3.1-pro-preview"
    config = models.kwargs["config"]
    # Google Search grounding tool must be present (web allowed for Prompt B).
    assert config.tools, "grounding tool must be configured"
    assert "Prompt B body" in models.kwargs["contents"]
    assert "briefing content" in models.kwargs["contents"]

    out = pack_dir / "gemini_b.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "request_sha256:" in text
    assert f"request_sha256: {expected_hash}" in text
    # Body is the model's raw structured output, not prose.
    assert '"schema_version": "1.0"' in text
    assert '"pass": "B"' in text


def test_missing_key_on_cache_miss(monkeypatch, gemini_env):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _, date_str, _ = gemini_env
    assert gemini_research.main(["--date", date_str]) == 1


def test_noop_when_output_exists_no_key(monkeypatch, gemini_env, mock_genai):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _, date_str, pack_dir = gemini_env
    (pack_dir / "gemini_b.md").write_text("existing", encoding="utf-8")
    assert gemini_research.main(["--date", date_str]) == 0
    assert len(mock_genai) == 0


def test_force_overrides_existing(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    out = pack_dir / "gemini_b.md"
    out.write_text("existing", encoding="utf-8")
    assert gemini_research.main(["--date", date_str, "--force"]) == 0
    assert out.read_text(encoding="utf-8") != "existing"


def test_refresh_reruns_on_briefing_change(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    assert gemini_research.main(["--date", date_str]) == 0
    (pack_dir / "briefing.md").write_text("CHANGED briefing", encoding="utf-8")
    assert gemini_research.run_gemini_b(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_genai) == 2


def test_empty_response_rejected_no_partial(gemini_env, monkeypatch):
    _, date_str, pack_dir = gemini_env

    def mock_init(*args, **kwargs):
        c = MockGenaiClient(*args, **kwargs)
        c.models.response = MockResponse(text="")
        return c

    monkeypatch.setattr(genai, "Client", mock_init)
    assert gemini_research.main(["--date", date_str]) == 1
    assert not (pack_dir / "gemini_b.md").exists()
