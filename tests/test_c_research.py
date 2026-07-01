"""Focused Prompt C tests using the June 28 pack target."""

import csv

import pytest
from google import genai

from outlier_scrapers import c_research, pack


VALID_OUTPUT = (
    "FINDING | market_id=m1 | selection=OVER 8.5 | line=8.5 | price=-110 | "
    "verdict=CONFIRMS | claim=Starter confirmed | source_name=MLB | source_tier=1 | "
    "source_timestamp=2026-06-28T12:00:00Z"
)


@pytest.fixture
def c_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(c_research.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(c_research.gemini_research, "load_environment", lambda: None)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "C.md").write_text("Prompt C body", encoding="utf-8")

    date_str = "2026-06-28"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text(
        "SLATE: 2026-06-28\nas_of: 2026-06-28T10:00:00-04:00\n",
        encoding="utf-8",
    )
    _write_candidates(pack_dir)
    return date_str, pack_dir


def _write_candidates(pack_dir, *, line="8.5", price="-110"):
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "market_id": "m1",
                "market_type": "TOTAL",
                "selection": "OVER 8.5",
                "line": line,
                "price": price,
            }
        )
        writer.writerow(row)


class MockResponse:
    def __init__(self, text=VALID_OUTPUT):
        self.text = text


class MockModels:
    def __init__(self):
        self.called = False
        self.kwargs = {}
        self.response = MockResponse()

    def generate_content(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return self.response


class MockGenaiClient:
    def __init__(self, *args, **kwargs):
        self.client_kwargs = kwargs
        self.models = MockModels()


@pytest.fixture
def mock_genai(monkeypatch):
    clients = []

    def mock_init(*args, **kwargs):
        client = MockGenaiClient(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(genai, "Client", mock_init)
    return clients


def test_missing_briefing_fails(c_env, mock_genai):
    date_str, pack_dir = c_env
    (pack_dir / "briefing.md").unlink()
    assert c_research.main(["--date", date_str]) == 1
    assert not mock_genai


def test_success_uses_grounding_and_authoritative_june28_inputs(c_env, mock_genai):
    date_str, pack_dir = c_env
    assert c_research.main(["--date", date_str]) == 0

    models = mock_genai[0].models
    assert models.called
    assert models.kwargs["model"] == c_research.MODEL
    assert models.kwargs["config"].tools
    contents = models.kwargs["contents"]
    assert "Prompt C body" in contents
    assert "2026-06-28" in contents
    assert "m1" in contents
    assert "OVER 8.5" in contents
    assert "-110" in contents

    output = (pack_dir / "chatgpt_c.md").read_text(encoding="utf-8")
    assert "briefing_sha256:" in output
    assert "candidates_sha256:" in output
    assert "request_sha256:" in output
    assert VALID_OUTPUT in output


def test_missing_key_on_cache_miss(c_env, monkeypatch):
    date_str, _ = c_env
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert c_research.main(["--date", date_str]) == 1


def test_existing_output_is_noop_without_key(c_env, monkeypatch, mock_genai):
    date_str, pack_dir = c_env
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    (pack_dir / "chatgpt_c.md").write_text("existing", encoding="utf-8")
    assert c_research.main(["--date", date_str]) == 0
    assert not mock_genai


def test_force_replaces_existing_output(c_env, mock_genai):
    date_str, pack_dir = c_env
    output = pack_dir / "chatgpt_c.md"
    output.write_text("existing", encoding="utf-8")
    assert c_research.main(["--date", date_str, "--force"]) == 0
    assert output.read_text(encoding="utf-8") != "existing"


def test_candidates_change_invalidates_refresh_hash(c_env, monkeypatch):
    _, pack_dir = c_env
    calls = []

    def first_call(*args, **kwargs):
        calls.append("first")
        return VALID_OUTPUT

    monkeypatch.setattr(c_research, "call_gemini", first_call)
    assert c_research.run_c_research(pack_dir) == 0

    _write_candidates(pack_dir, line="9.0", price="-105")
    updated = VALID_OUTPUT.replace("line=8.5 | price=-110", "line=9.0 | price=-105")
    monkeypatch.setattr(c_research, "call_gemini", lambda *a, **k: updated)
    assert c_research.run_c_research(pack_dir, refresh_if_stale=True) == 0
    assert "line=9.0 | price=-105" in (pack_dir / "chatgpt_c.md").read_text(
        encoding="utf-8"
    )
    assert calls == ["first"]


def test_altered_quote_is_rejected_and_previous_output_is_preserved(c_env, monkeypatch):
    _, pack_dir = c_env
    output = pack_dir / "chatgpt_c.md"
    output.write_text("previous valid result", encoding="utf-8")
    altered = VALID_OUTPUT.replace("line=8.5", "line=9.5")
    monkeypatch.setattr(c_research, "call_gemini", lambda *a, **k: altered)

    assert c_research.run_c_research(pack_dir, force=True) == 1
    assert output.read_text(encoding="utf-8") == "previous valid result"
