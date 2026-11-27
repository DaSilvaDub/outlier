"""Game scoped availability must never carry an old OUT status forward."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import filter_nba_ruled_out as flt

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
DATE = "2026-11-23"
FIELDS = ["rank", "player", "slate_date", "matchup_abbr", "game_id", "market", "line"]


def report(status="ACTIVE", **changes):
    return {"status": status, "slate_date": DATE, "matchup_abbr": "LAL @ BOS",
            "game_id": "g23", "reported_utc": "2026-11-23T09:30:00Z",
            "source": "https://ak-static.cms.nba.com/report.pdf", **changes}


def registry(path, entries):
    path.write_text(json.dumps({"teams": {"LAL": {"roster": entries}}}), encoding="utf-8")


def pack(path, players):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index, player in enumerate(players, 1):
            writer.writerow({"rank": index, "player": player, "slate_date": DATE,
                             "matchup_abbr": "LAL @ BOS", "game_id": "g23",
                             "market": "PTS", "line": "20.5"})


def test_game_scoped_out_removed_probable_questionable_retained(tmp_path):
    source, reg, summary = (tmp_path / name for name in ("pack.csv", "reg.json", "summary.json"))
    pack(source, ["Out", "Probable", "Questionable", "Healthy"])
    registry(reg, [{"name": name, "game_availability": [report(status)]}
                   for name, status in [("Out", "OUT"), ("Probable", "PROBABLE"),
                                        ("Questionable", "QUESTIONABLE"), ("Healthy", "ACTIVE")]])
    assert flt.filter_csv(source, source, summary, reg, DATE, now=NOW) == (4, 3, ["Out"])
    with source.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["player"] for row in rows] == ["Probable", "Questionable", "Healthy"]
    assert [row["rank"] for row in rows] == ["1", "2", "3"]
    data = json.loads(summary.read_text())
    assert data["after_rows"] == data["actionable_rows"] == 3
    assert data["csv_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(source.with_suffix(".status.json").read_text())["row_count"] == 3


@pytest.mark.parametrize("observation", [
    report("OUT", slate_date="2026-11-22"), report("OUT", game_id="g22"),
    report("OUT", report_submitted=False), report("OUT", reported_utc="2026-11-23T01:00:00Z"),
    report("OUT", reported_utc="2026-11-23T11:00:00Z"), report("OUT", source=""),
    {"status": "OUT", "injuries": ["ankle"]},
])
def test_old_unsubmitted_or_unscoped_status_is_unknown_not_confirmed_out(tmp_path, observation):
    source, reg, summary = (tmp_path / name for name in ("pack.csv", "reg.json", "summary.json"))
    pack(source, ["Player"])
    registry(reg, [{"name": "Player", "status": "OUT", "injuries": [observation]}])
    assert flt.filter_csv(source, source, summary, reg, DATE, now=NOW) == (1, 0, [])
    data = json.loads(summary.read_text())
    assert data["removed_count"] == 0
    assert data["unknown_players"] == ["Player"]
    assert data["status"] == "awaiting_availability"


def test_newer_probable_supersedes_previous_out_for_same_game(tmp_path):
    source, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    pack(source, ["Player"])
    registry(reg, [{"name": "Player", "injuries": [report("OUT", reported_utc="2026-11-23T08:00:00Z"), report("PROBABLE")]}])
    assert flt.filter_csv(source, source, registry_path=reg, slate_date=DATE, now=NOW) == (1, 1, [])


def test_same_name_different_game_is_not_removed(tmp_path):
    source, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    pack(source, ["Player"])
    registry(reg, [{"name": "Player", "injuries": [report("OUT", game_id="g24")]}])
    assert flt.filter_csv(source, source, registry_path=reg, slate_date=DATE, now=NOW) == (1, 0, [])


def test_matchup_can_scope_when_ids_absent():
    evidence = report("PROBABLE", game_id="")
    reg = {"teams": {"LAL": {"roster": [{"name": "Player", "game_availability": [evidence]}]}}}
    row = {"player": "Player", "slate_date": DATE, "matchup_abbr": "LAL@BOS"}
    assert flt.availability(row, reg, DATE, NOW) == "PROBABLE"
    row["matchup_abbr"] = "LAL@OKC"
    assert flt.availability(row, reg, DATE, NOW) == "UNKNOWN"


def test_missing_registry_stands_down_with_explicit_error(tmp_path):
    source = tmp_path / "pack.csv"
    pack(source, ["Player"])
    assert flt.filter_csv(source, source, registry_path=tmp_path / "missing.json", now=NOW) == (1, 0, [])
    status = json.loads(source.with_suffix(".status.json").read_text())
    assert status["status"] == "awaiting_availability"
    assert "not found" in status["availability_error"]


@pytest.mark.parametrize("payload,match", [('{"teams": {', "unreadable"), ('{"teams": {}}', "no players")])
def test_malformed_registry_stands_down(tmp_path, payload, match):
    source, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    pack(source, ["Player"])
    reg.write_text(payload)
    assert flt.filter_csv(source, source, registry_path=reg, now=NOW) == (1, 0, [])
    status = json.loads(source.with_suffix(".status.json").read_text())
    assert match in status["availability_error"]
    assert status["status"] == "awaiting_availability"


def test_empty_pack_preserves_awaiting_odds_without_registry(tmp_path):
    source = tmp_path / "pack.csv"
    pack(source, [])
    source.with_suffix(".status.json").write_text(json.dumps({"status": "awaiting_odds"}))
    assert flt.filter_csv(source, source, slate_date=DATE, now=NOW) == (0, 0, [])
    assert json.loads(source.with_suffix(".status.json").read_text())["status"] == "awaiting_odds"


def test_missing_input_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        flt.filter_csv(tmp_path / "missing.csv", tmp_path / "out.csv")


def test_main_date_argument(tmp_path, monkeypatch):
    source = tmp_path / "pack.csv"
    pack(source, [])
    monkeypatch.setattr(sys, "argv", ["filter_nba_ruled_out.py", "--csv", str(source), "--out", str(source), "--date", DATE])
    assert flt.main() == 0


def test_duplicate_player_identity_is_unknown(tmp_path):
    source, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    pack(source, ["Player"])
    registry(reg, [{"name": "Player", "injuries": [report("PROBABLE")]},
                   {"name": "Player", "injuries": [report("OUT")]}])
    assert flt.filter_csv(source, source, registry_path=reg, now=NOW) == (1, 0, [])


def test_malformed_player_evidence_and_status_json_stand_down(tmp_path):
    source, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    pack(source, ["Player"])
    registry(reg, [{"name": "Player", "injuries": None}])
    source.with_suffix(".status.json").write_text("[]")
    assert flt.filter_csv(source, source, registry_path=reg, now=NOW) == (1, 0, [])
    assert json.loads(source.with_suffix(".status.json").read_text())["status"] == "awaiting_availability"
