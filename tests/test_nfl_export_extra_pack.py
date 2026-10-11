"""F25 (#229): an explicit-date export uses exactly that validated slate."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_nfl_extra_pack", ROOT / "scripts" / "export_nfl_extra_pack.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REC = {"player_name": "Patrick Mahomes", "player_id": "00-M", "event_id": "e1",
       "market": "PASS_YDS", "position": "OVER", "line": 250.5, "best_odds": -110,
       "scope": "full_game", "confidence_tier": "TIER_1_ANCHOR"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    norm = tmp_path / "normalized"
    norm.mkdir()
    monkeypatch.setattr(mod, "NORMALIZED", norm)
    monkeypatch.setattr(mod, "OUT_CSV", tmp_path / "out" / "nfl_only.csv")
    return norm, tmp_path / "out" / "nfl_only.csv"


def _write(norm: Path, name: str, payload) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (norm / name).write_text(text, encoding="utf-8")


def _run(monkeypatch, *args: str) -> int:
    monkeypatch.setattr("sys.argv", ["export", *args])
    return mod.main()


def test_missing_requested_date_never_uses_latest(env, monkeypatch):
    norm, out = env
    _write(norm, "nfl_high_prob_props_latest.json", {"date": "2026-09-06", "records": [REC]})
    assert _run(monkeypatch, "--date", "2026-09-13") == mod.EXIT_MISSING
    assert not out.exists()


def test_corrupt_json_is_nonzero_and_writes_nothing(env, monkeypatch):
    norm, out = env
    _write(norm, "nfl_high_prob_props_2026-09-13.json", "{nope")
    assert _run(monkeypatch, "--date", "2026-09-13") == mod.EXIT_CORRUPT
    assert not out.exists()


def test_payload_without_records_list_is_corrupt(env, monkeypatch):
    norm, _out = env
    _write(norm, "nfl_high_prob_props_2026-09-13.json", ["not", "an", "object"])
    assert _run(monkeypatch, "--date", "2026-09-13") == mod.EXIT_CORRUPT


@pytest.mark.parametrize("payload", [
    {"date": "2026-09-06", "records": [REC]},
    {"date": "2026-09-13", "window": "1pm", "records": [REC]},
    {"records": [REC]},
])
def test_mismatched_payload_is_refused(env, monkeypatch, payload):
    norm, out = env
    _write(norm, "nfl_high_prob_props_2026-09-13.json", payload)
    assert _run(monkeypatch, "--date", "2026-09-13") == mod.EXIT_MISMATCH
    assert not out.exists()


def test_valid_empty_slate_exits_zero_with_a_header(env, monkeypatch, capsys):
    norm, out = env
    _write(norm, "nfl_high_prob_props_2026-09-13.json", {"date": "2026-09-13", "records": []})
    assert _run(monkeypatch, "--date", "2026-09-13") == 0
    assert out.read_text(encoding="utf-8").splitlines() == [",".join(mod.FIELDNAMES)]
    assert "valid empty slate" in capsys.readouterr().err


def test_rows_carry_run_event_player_and_explicit_status(env, monkeypatch):
    norm, out = env
    recs = [{**REC, "actionable": True}, {**REC, "player_id": "00-X", "actionable": False},
            {**REC, "player_id": "00-Y"}]
    _write(norm, "nfl_high_prob_props_2026-09-13.json",
           {"date": "2026-09-13", "window": None, "run_id": "R1", "records": recs})
    assert _run(monkeypatch, "--date", "2026-09-13") == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [(r["run_id"], r["event_id"], r["player_id"], r["approval_status"]) for r in rows] == [
        ("R1", "e1", "00-M", "actionable"), ("R1", "e1", "00-X", "inventory"),
        ("R1", "e1", "00-Y", "unlabeled")]
    # The tier never implies approval.
    assert all(r["confidence_tier"] == "TIER_1_ANCHOR" for r in rows)


def test_window_reads_the_windowed_file(env, monkeypatch):
    norm, out = env
    _write(norm, "nfl_high_prob_props_2026-09-13_1pm.json",
           {"date": "2026-09-13", "window": "1pm", "records": [REC]})
    assert _run(monkeypatch, "--date", "2026-09-13", "--window", "1pm") == 0
    assert out.exists()


def test_failure_keeps_the_previous_csv(env, monkeypatch):
    _norm, out = env
    out.parent.mkdir(parents=True)
    out.write_text("previous", encoding="utf-8")
    assert _run(monkeypatch, "--date", "2026-09-13") == mod.EXIT_MISSING
    assert out.read_text(encoding="utf-8") == "previous"
