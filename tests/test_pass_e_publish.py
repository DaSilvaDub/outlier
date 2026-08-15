"""Pass E reconciliation publish path — fake Claude client, no live provider."""

from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

from outlier_scrapers import (
    claude_synthesis,
    desk_snapshot,
    pack,
    pack_index,
    paths,
    verdict_gate,
)
from outlier_scrapers import runner_common as rc


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir, tmp_path):
    pack_dir.mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "E.md").write_text("Prompt E body", encoding="utf-8")
    (pack_dir / "briefing.md").write_text("SLATE: 2026-08-14\n", encoding="utf-8")
    for name in ("chatgpt_a.md", "gemini_b.md", "claude_d.md"):
        (pack_dir / name).write_text(f"legacy {name}\n", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "_event_starts_at": _future(),
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
            }
        )
        writer.writerow(row)


def _verdict_envelope(index, pass_name: str, units: float = 1.0):
    return {
        "schema_version": "1.0",
        "pass": pass_name,
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [
            {
                "market_id": "m1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "confidence": 0.7,
                "recommended_units": units,
                "evidence": [],
                "contradictions": [],
                "kill_triggers": [],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }


def _publish_upstream(pack_dir, index, units: float = 1.0):
    pubs = {}
    for pass_name in ("A", "D", "B"):
        result = rc.publish_verdict_pass(
            pack_dir,
            json.dumps(_verdict_envelope(index, pass_name, units)),
            pass_=pass_name,
            request_sha256=pass_name * 16,
            candidates_sha256=index.candidates_sha256,
            game_totals_sha256=index.game_totals_sha256,
            team_totals_sha256=index.team_totals_sha256,
            model="fake",
        )
        data = json.loads((result.path / "verdicts.json").read_text(encoding="utf-8"))
        pubs[pass_name] = {
            "publication_id": result.publication_id,
            "record_id": data["verdicts"][0]["record_id"],
        }
    return pubs


def _e_envelope(index, pubs, *, units=1.0, cites=None, line="5.5"):
    if cites is None:
        cites = [
            {
                "pass": name,
                "publication_id": info["publication_id"],
                "record_id": info["record_id"],
            }
            for name, info in pubs.items()
        ]
    return {
        "schema_version": "1.0",
        "pass": "E",
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {
            **{name: info["publication_id"] for name, info in pubs.items()},
            "C": pubs.get("C", {}).get("publication_id"),
        },
        "reconciliations": [
            {
                "market_id": "m1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": line,
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "recommended_units": units,
                "narrative": "A, D, and B agree on the side.",
                "cites": cites,
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }


class _ClaudeClient:
    def __init__(self, text: str):
        self._text = text
        self.last_kwargs = None
        self.messages = self

    def stream(self, **kwargs):
        self.last_kwargs = kwargs
        text = self._text

        class _Block:
            type = "text"

            def __init__(self, body):
                self.text = body

        class _Msg:
            stop_reason = "end_turn"

            def __init__(self, body):
                self.content = [_Block(body)]

        class _Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def get_final_message(self_inner):
                return _Msg(text)

        return _Stream()


def test_pass_e_publishes_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pubs = _publish_upstream(pack_dir, index)
    client = _ClaudeClient(json.dumps(_e_envelope(index, pubs)))

    assert claude_synthesis.run_claude_e(pack_dir, client=client) == 0
    current = json.loads((pack_dir / "verdicts" / "E" / "current.json").read_text(encoding="utf-8"))
    dest = pack_dir / "verdicts" / "E" / current["publication_id"]
    assert (dest / "verdicts.json").is_file()
    assert (pack_dir / "claude_e.md").is_file()
    assert client.last_kwargs["tool_choice"]["name"] == "emit_reconciliations"


def test_pass_e_reads_validates_and_publishes_under_one_lock(tmp_path, monkeypatch):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pubs = _publish_upstream(pack_dir, index)
    events = []
    state = {"locked": False}

    @contextmanager
    def tracked_locks(*args, **kwargs):
        assert not state["locked"]
        state["locked"] = True
        events.append("lock_enter")
        try:
            yield
        finally:
            events.append("lock_exit")
            state["locked"] = False

    original_build = pack_index.build_pack_index
    original_load = rc.load_current_publications
    original_validate = verdict_gate.validate_envelope
    original_publish = rc.publish_pass

    def tracked_build(*args, **kwargs):
        assert state["locked"]
        events.append("build_index")
        return original_build(*args, **kwargs)

    def tracked_load(*args, **kwargs):
        assert state["locked"]
        events.append("load_publications")
        return original_load(*args, **kwargs)

    def tracked_validate(*args, **kwargs):
        assert state["locked"]
        events.append("validate")
        return original_validate(*args, **kwargs)

    def tracked_publish(*args, **kwargs):
        assert state["locked"]
        assert kwargs.get("hold_locks") is False
        events.append("publish")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(desk_snapshot, "fingerprint_locks", tracked_locks)
    monkeypatch.setattr(pack_index, "build_pack_index", tracked_build)
    monkeypatch.setattr(rc, "load_current_publications", tracked_load)
    monkeypatch.setattr(verdict_gate, "validate_envelope", tracked_validate)
    monkeypatch.setattr(rc, "publish_pass", tracked_publish)

    result = rc.publish_reconciliation_pass(
        pack_dir,
        json.dumps(_e_envelope(index, pubs)),
        request_sha256="E" * 16,
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        model="fake",
    )

    assert result.path.is_dir()
    assert events == [
        "lock_enter",
        "build_index",
        "load_publications",
        "validate",
        "publish",
        "lock_exit",
    ]


def test_pass_e_rejects_stale_top_level_upstream_publication_id(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pubs = _publish_upstream(pack_dir, index)
    envelope = _e_envelope(index, pubs)
    envelope["upstream_publication_ids"]["A"] = "stale-a-publication"

    try:
        rc.publish_reconciliation_pass(
            pack_dir,
            json.dumps(envelope),
            request_sha256="E" * 16,
            candidates_sha256=index.candidates_sha256,
            game_totals_sha256=index.game_totals_sha256,
            team_totals_sha256=index.team_totals_sha256,
            model="fake",
        )
    except rc.RunnerError as exc:
        assert "stale_upstream_publications" in str(exc)
    else:
        raise AssertionError("stale E publication manifest was published")


def test_load_current_publications_preserves_injury_supported_record_ids(
    tmp_path, monkeypatch
):
    source_policy = paths.PROJECT_ROOT / "config" / "verdict_policy.json"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "verdict_policy.json").write_bytes(source_policy.read_bytes())
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    envelope = _verdict_envelope(index, "B")
    envelope["verdicts"][0]["evidence"] = [
        {
            "claim": "Player One is questionable with an injury.",
            "kind": "external",
            "subject_type": "player",
            "player_id": "p1",
            "team": None,
            "market_id": "m1",
            "outcome_id": "out1",
            "source": "official report",
            "tier": 1,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
    ]
    result = rc.publish_verdict_pass(
        pack_dir,
        json.dumps(envelope),
        pass_="B",
        request_sha256="B" * 16,
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        model="fake",
    )
    data = json.loads((result.path / "verdicts.json").read_text(encoding="utf-8"))
    record_id = data["verdicts"][0]["record_id"]

    loaded = rc.load_current_publications(pack_dir)
    assert loaded["B"].injury_supported_record_ids == frozenset({record_id})


def test_pass_e_rejects_unsourced_c_only_cite(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    _publish_upstream(pack_dir, index)
    cites = [{"pass": "C", "publication_id": "pub_c", "record_id": "only_c"}]
    env = _e_envelope(index, {"C": {"publication_id": "pub_c", "record_id": "only_c"}}, cites=cites)
    client = _ClaudeClient(json.dumps(env))

    assert claude_synthesis.run_claude_e(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "E" / "current.json").exists()


def test_pass_e_rejects_stake_above_upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pubs = _publish_upstream(pack_dir, index, units=1.0)
    client = _ClaudeClient(json.dumps(_e_envelope(index, pubs, units=1.5)))

    assert claude_synthesis.run_claude_e(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "E" / "current.json").exists()


def test_pass_e_rejects_tampered_line(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pubs = _publish_upstream(pack_dir, index)
    client = _ClaudeClient(json.dumps(_e_envelope(index, pubs, line="9.9")))

    assert claude_synthesis.run_claude_e(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "E" / "current.json").exists()
