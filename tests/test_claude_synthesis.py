import csv
import json

import pytest
import anthropic

from outlier_scrapers import claude_synthesis, pack, pack_index, verdicts
from outlier_scrapers import runner_common as rc


# Copied from tests/test_claude_reasoning.py to keep this test file self-contained
# (repo style: no shared conftest for mocks across these runner tests)

def _envelope_dict(pack_dir) -> dict:
    """A schema-valid, gate-clean pass-E reconciliation envelope for this pack.

    `reconciliations` is deliberately empty: these tests cover runner mechanics,
    not reconciliation content (tests/test_pass_e_publish.py covers that against
    real published upstream passes). The three pack hashes must match the live
    pack or the gate rejects with pack_mismatch, so this is built at call time.
    """
    index = pack_index.build_pack_index(
        pack_dir, policy_path=pack_dir / "no-such-portfolio-policy.json"
    )
    publications = rc.load_current_publication_documents(pack_dir)
    return {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": pack_dir.name,
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {
            "A": publications["A"].publication_id,
            "D": publications["D"].publication_id,
            "B": publications["B"].publication_id,
            "C": publications["C"].publication_id if "C" in publications else None,
        },
        "reconciliations": [],
        "slate_notes": [],
        "needs": [],
    }


class MockTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class MockToolUseBlock:
    """Mirrors what a forced `emit_reconciliations` tool call actually returns."""

    type = "tool_use"
    name = "emit_reconciliations"

    def __init__(self, payload):
        self.input = payload


class MockMessage:
    def __init__(self, text=None, stop_reason="end_turn", tool_input=None):
        if tool_input is not None:
            self.content = [MockToolUseBlock(tool_input)]
        else:
            self.content = [MockTextBlock(text)] if text else []
        self.stop_reason = stop_reason
        self.stop_details = None


class MockStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class MockMessages:
    def __init__(self, pack_dir=None):
        self.called = False
        self.kwargs = {}
        # None => emit a valid envelope at call time. Tests that want a
        # specific (usually invalid) response assign `message` directly.
        self.message = None
        self._pack_dir = pack_dir

    def stream(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        message = self.message
        if message is None:
            message = MockMessage(tool_input=_envelope_dict(self._pack_dir))
        return MockStream(message)


class MockAnthropic:
    def __init__(self, *args, pack_dir=None, **kwargs):
        self.client_kwargs = kwargs
        self.messages = MockMessages(pack_dir)


@pytest.fixture
def mock_anthropic(monkeypatch, synth_env):
    _, _, pack_dir = synth_env
    clients = []

    def mock_init(*args, **kwargs):
        client = MockAnthropic(*args, pack_dir=pack_dir, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(anthropic, "Anthropic", mock_init)
    return clients


@pytest.fixture
def synth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "E.md").write_text("Prompt E body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("briefing", encoding="utf-8")
    (pack_dir / "chatgpt_a.md").write_text("A output", encoding="utf-8")
    (pack_dir / "gemini_b.md").write_text("B output", encoding="utf-8")
    (pack_dir / "claude_d.md").write_text("D output", encoding="utf-8")

    # Pass E validates the candidates ledger for its own pack identity, so the
    # upstream Markdown alone is no longer a complete pack for this runner.
    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pack.CANDIDATES_HEADER)
        w.writeheader()
        row = {k: "" for k in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "_event_starts_at": "2099-01-01T12:00:00Z",
                "market_id": "m1",
                "outcome_id": "out1",
                "market_type": "PLAYER_PROP",
                "player_id": "p1",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "board": "A",
                "actionable": "true",
                "market_label": "SO",
                "max_units": "2.0",
                "recommended_units_pre_news": "1.5",
                "team_name": "A",
                "opp_name": "B",
                "matchup": "A @ B",
            }
        )
        w.writerow(row)

    return tmp_path, date_str, pack_dir


def _upstream_envelope(index, pass_name: str, units: float) -> dict:
    """One BET on the indexed row, as pass `pass_name` would have published it."""
    row = next(iter(index.rows.values()))
    kind = "findings" if pass_name == "C" else "verdicts"
    record = {
        "market_id": row.market_id,
        "outcome_id": row.outcome_id,
        "stream": row.stream,
        "selection": row.data["selection"],
        "line": row.data["line"],
        "price": row.data["price"],
    }
    if pass_name == "C":
        record.update(
            {
                "verdict": "CONFIRMS",
                "claim": "Starter confirmed.",
                "source_name": "Official",
                "source_tier": 1,
                "source_timestamp": "2026-06-27T12:00:00Z",
                "evidence": [],
            }
        )
    else:
        record.update(
            {
                "book": row.data["book"],
                "verdict": "BET",
                "confidence": 0.7,
                "recommended_units": units,
                "evidence": [],
                "contradictions": [],
                "kill_triggers": [],
                "rejection_reasons": [],
            }
        )
    envelope = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": pass_name,
        "pack_date": "2026-06-27",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        kind: [record],
    }
    if pass_name == "C":
        envelope["no_sourced_findings"] = False
    else:
        envelope["slate_notes"] = []
        envelope["needs"] = []
    return envelope


def _publish_pass(pack_dir, pass_name: str, *, units: float = 1.0) -> dict:
    """Publish one upstream pass and return its publication_id + first record_id."""
    index = pack_index.build_pack_index(
        pack_dir, policy_path=pack_dir / "no-such-portfolio-policy.json"
    )
    envelope_json = json.dumps(_upstream_envelope(index, pass_name, units))
    request_sha256 = (pass_name * 64)[:64]

    if pass_name != "C":
        result = rc.publish_verdict_pass(
            pack_dir,
            envelope_json,
            pass_=pass_name,
            request_sha256=request_sha256,
            candidates_sha256=index.candidates_sha256,
            game_totals_sha256=index.game_totals_sha256,
            team_totals_sha256=index.team_totals_sha256,
            model="fake",
        )
    else:
        result = rc.publish_finding_pass(
            pack_dir,
            envelope_json,
            request_sha256=request_sha256,
            candidates_sha256=index.candidates_sha256,
            game_totals_sha256=index.game_totals_sha256,
            team_totals_sha256=index.team_totals_sha256,
            model="fake",
        )

    data = json.loads((result.path / "verdicts.json").read_text(encoding="utf-8"))
    key = "findings" if pass_name == "C" else "verdicts"
    return {
        "publication_id": result.publication_id,
        "record_id": data[key][0]["record_id"],
    }


def _publish_upstream(pack_dir, only=("A", "D", "B")) -> dict:
    return {name: _publish_pass(pack_dir, name) for name in only}


def test_missing_upstream_publication_fails(synth_env):
    """E reconciles published envelopes; it cannot run against a partial desk."""
    _, date_str, pack_dir = synth_env
    _publish_upstream(pack_dir, only=("A", "D"))  # B never published
    assert claude_synthesis.main(["--date", date_str]) == 1


def test_missing_briefing_fails(synth_env):
    _, date_str, pack_dir = synth_env
    _publish_upstream(pack_dir)
    (pack_dir / "briefing.md").unlink()
    assert claude_synthesis.main(["--date", date_str]) == 1


def test_success_shows_upstream_envelopes_not_markdown(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    pubs = _publish_upstream(pack_dir)
    expected_hash = claude_synthesis.expected_request_sha256(pack_dir)
    config = claude_synthesis.provider_configuration()
    assert config["provider"] == "anthropic"
    assert config["effort"] == "high"
    assert "key" not in json.dumps(config).lower()
    # Prose left on disk must NOT be what E is shown.
    (pack_dir / "chatgpt_a.md").write_text("A PROSE OUTPUT", encoding="utf-8")

    assert claude_synthesis.main(["--date", date_str]) == 0
    content = mock_anthropic[0].messages.kwargs["messages"][0]["content"]

    assert "Prompt E body" in content
    assert "briefing" in content
    assert "A PROSE OUTPUT" not in content
    for pass_name, pub in pubs.items():
        # E must see each publication_id and every citable record_id, or it
        # cannot emit a citation the gate will accept.
        assert f"UPSTREAM PASS {pass_name}" in content
        assert pub["publication_id"] in content
        assert pub["record_id"] in content

    out = pack_dir / "claude_e.md"
    assert out.exists()
    assert f"request_sha256: {expected_hash}" in out.read_text(encoding="utf-8")


def test_optional_c_included_when_published(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    _publish_upstream(pack_dir)
    c_pub = _publish_pass(pack_dir, "C")
    assert claude_synthesis.main(["--date", date_str]) == 0
    content = mock_anthropic[0].messages.kwargs["messages"][0]["content"]
    assert "UPSTREAM PASS C" in content
    assert c_pub["publication_id"] in content


def test_refresh_reruns_when_an_upstream_publication_changes(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    _publish_upstream(pack_dir)
    assert claude_synthesis.main(["--date", date_str]) == 0

    # Republish D with a different stake -> new publication_id -> E must re-run.
    _publish_pass(pack_dir, "D", units=0.5)

    assert claude_synthesis.run_claude_e(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_anthropic) == 2


def test_noop_when_output_exists_no_key(monkeypatch, synth_env, mock_anthropic):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, date_str, pack_dir = synth_env
    (pack_dir / "claude_e.md").write_text("existing", encoding="utf-8")
    assert claude_synthesis.main(["--date", date_str]) == 0
    assert len(mock_anthropic) == 0
