"""Focused Prompt C tests using the June 28 pack target."""

import csv
import json

import pytest
from google import genai

from outlier_scrapers import c_research, pack, paths
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER


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
                "outcome_id": "m1",
                "market_type": "TOTAL",
                "selection": f"OVER {line}",
                "line": line,
                "price": price,
                "team_name": "A",
                "opp_name": "B",
                "matchup": "A @ B",
                "_event_starts_at": "2099-06-28T19:00:00+00:00",
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
    assert "pack_date: 2026-06-28" in contents
    assert "candidates_sha256:" in contents
    assert "game_totals_sha256:" in contents
    assert "team_totals_sha256:" in contents
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
    updated = VALID_OUTPUT.replace("line=8.5 | price=-110", "line=9.0 | price=-105").replace("selection=OVER 8.5", "selection=OVER 9.0")
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


def test_totals_only_output_validates_against_totals_id(c_env, monkeypatch):
    _, pack_dir = c_env
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(pack.CANDIDATES_HEADER)
    with (pack_dir / "game_totals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GAME_TOTALS_HEADER)
        writer.writeheader()
        row = {field: "" for field in GAME_TOTALS_HEADER}
        row.update(
            totals_id="total:m1:8.5:OVER",
            market_id="m1",
            outcome_id="total:m1:8.5:OVER",
            selection="A @ B Total OVER 8.5",
            line="8.5",
            price="-110",
            actionable="true",
        )
        writer.writerow(row)

    totals_output = VALID_OUTPUT.replace("market_id=m1", "market_id=total:m1:8.5:OVER").replace(
        "selection=OVER 8.5", "selection=A @ B Total OVER 8.5"
    )
    monkeypatch.setattr(c_research, "call_gemini", lambda *args, **kwargs: totals_output)
    assert c_research.run_c_research(pack_dir) == 0
    assert totals_output in (pack_dir / "chatgpt_c.md").read_text(encoding="utf-8")


def test_malformed_timestamp_is_rejected(c_env, monkeypatch):
    """A genuinely unparseable source_timestamp must still be rejected with a
    clear error, not silently swallowed. Regression guard: the timestamp check
    previously wrapped the dateutil import in the same bare `except Exception`
    used for parse failures, so a missing/broken dateutil install masqueraded
    as this exact error message for every finding instead of failing loudly at
    import time."""
    _, pack_dir = c_env
    bad = VALID_OUTPUT.replace(
        "source_timestamp=2026-06-28T12:00:00Z", "source_timestamp=not-a-real-timestamp"
    )
    monkeypatch.setattr(c_research, "call_gemini", lambda *a, **k: bad)
    assert c_research.run_c_research(pack_dir, force=True) == 1
    assert not (pack_dir / "chatgpt_c.md").exists()


def test_malformed_pack_date_still_validates_timestamp_parseability():
    """Regression guard (PR #30 review): pack_date_str is the pack directory's
    own name -- an internal, caller-controlled value, not part of the model's
    output. A malformed pack_date_str must only disable the date-window check
    below; it must never silently skip validating that source_timestamp
    itself is a parseable date."""
    candidates = {"m1": {"selection": "OVER 8.5", "line": "8.5", "price": "-110"}}
    garbled = VALID_OUTPUT.replace(
        "source_timestamp=2026-06-28T12:00:00Z", "source_timestamp=not-a-real-timestamp"
    )
    with pytest.raises(c_research.rc.RunnerError, match="unparseable timestamp"):
        c_research.validate_output(garbled, candidates, "not-a-pack-date")


def test_malformed_pack_date_skips_window_check_for_valid_timestamp():
    """Complements the guard above: when pack_date_str can't be parsed, a
    genuinely valid source_timestamp must still pass (the window check has
    nothing to compare against, so it is skipped rather than raising)."""
    candidates = {"m1": {"selection": "OVER 8.5", "line": "8.5", "price": "-110"}}
    c_research.validate_output(VALID_OUTPUT, candidates, "not-a-pack-date")  # no raise


def test_dateutil_is_a_declared_dependency():
    """c_research.py imports dateutil directly at module level; it must be
    declared in pyproject.toml so a clean CI/deploy environment has it.
    Regression guard for the CI-only 'unparseable timestamp' failure caused by
    an undeclared dependency that happened to already be installed locally."""
    import tomllib

    data = tomllib.loads((paths.PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(d.lower().startswith("python-dateutil") for d in deps)


def test_json_finding_envelope_validates(c_env, monkeypatch):
    _, pack_dir = c_env
    envelope = {
        "schema_version": "1.0",
        "pass": "C",
        "pack_date": "2026-06-28",
        "candidates_sha256": "x",
        "game_totals_sha256": "x",
        "team_totals_sha256": "x",
        "findings": [
            {
                "market_id": "m1",
                "outcome_id": "m1",
                "stream": "candidates",
                "selection": "OVER 8.5",
                "line": "8.5",
                "price": "-110",
                "verdict": "CONFIRMS",
                "claim": "Starter confirmed",
                "source_name": "MLB",
                "source_tier": 1,
                "source_timestamp": "2026-06-28T12:00:00Z",
                "evidence": [],
            }
        ],
        "no_sourced_findings": False,
    }
    # Hashes in the fixture are placeholders; the runner overwrites them only
    # for FINDING-pipe conversion. JSON must echo the live pack hashes.
    from outlier_scrapers import pack_index

    index = pack_index.build_pack_index(
        pack_dir, policy_path=pack_dir / "no-policy.json"
    )
    envelope["candidates_sha256"] = index.candidates_sha256
    envelope["game_totals_sha256"] = index.game_totals_sha256
    envelope["team_totals_sha256"] = index.team_totals_sha256
    monkeypatch.setattr(
        c_research, "call_gemini", lambda *a, **k: json.dumps(envelope)
    )
    assert c_research.run_c_research(pack_dir) == 0


def test_json_finding_envelope_wrapped_in_prose_and_fence_validates(c_env, monkeypatch):
    _, pack_dir = c_env
    from outlier_scrapers import pack_index

    index = pack_index.build_pack_index(pack_dir, policy_path=pack_dir / "no-policy.json")
    envelope = {
        "schema_version": "1.0",
        "pass": "C",
        "pack_date": "2026-06-28",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "findings": [
            {
                "market_id": "m1",
                "outcome_id": "m1",
                "stream": "candidates",
                "selection": "OVER 8.5",
                "line": "8.5",
                "price": "-110",
                "verdict": "CONFIRMS",
                "claim": "Starter confirmed",
                "source_name": "MLB",
                "source_tier": 1,
                "source_timestamp": "2026-06-28T12:00:00Z",
                "evidence": [],
            }
        ],
        "no_sourced_findings": False,
    }
    wrapped = "Here is the finding envelope:\n```json\n" + json.dumps(envelope) + "\n```\n"
    monkeypatch.setattr(c_research, "call_gemini", lambda *a, **k: wrapped)

    assert c_research.run_c_research(pack_dir) == 0


def test_arbitrary_prose_is_not_accepted_as_prompt_c_output():
    candidates = {"m1": {"selection": "OVER 8.5", "line": "8.5", "price": "-110"}}

    with pytest.raises(c_research.rc.RunnerError, match="not a FINDING record"):
        c_research.validate_output("Starter looks healthy according to reports.", candidates, "2026-06-28")


def test_no_sourced_findings_sentinel_remains_valid():
    c_research.validate_output(c_research.NO_FINDINGS, {}, "2026-06-28")
