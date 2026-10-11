"""9c follow-up: legacy slates (2026-09-27, 2026-10-04, 2026-10-05) in the archive.

Fixtures are named and shaped after the files still on Daniel's machine
(``data/NFL/normalized/nfl_*_<date>.json`` with the pipeline's
``{date, updated_at, count, records}`` payload, ``scorecard/``, ``exports/``,
``cache/``, ``snapshots/nfl_prop_snapshots_<week>.jsonl`` and
``reports/NFL/<date>_<AWAY>_<HOME>_Game_Script.md``). Modified times are set
explicitly: Oct 5 files at 7:50 PM ET, after the 1 PM kickoff and before the
night game; the Sep 27 matchup-script copies and ledger on Sep 29.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

import pytest

from outlier_nfl import archive as ar
from outlier_nfl import archive_legacy as al
from scripts import nfl_archive

# kickoffs (UTC)
GAMES = {
    "2026-09-27": [("E927A", "ARI", "SF", "2026-09-27T17:00:00Z"),
                   ("E927B", "KC", "MIA", "2026-09-28T00:20:00Z")],
    "2026-10-04": [("E104A", "BAL", "DAL", "2026-10-04T17:00:00Z")],
    "2026-10-05": [("E105A", "HOU", "IND", "2026-10-05T17:00:00Z"),
                   ("E105B", "LAC", "BUF", "2026-10-06T00:20:00Z")],
}
BEFORE = {"2026-09-27": "2026-09-27T06:10:05Z", "2026-10-04": "2026-10-04T05:18:18Z",
          "2026-10-05": "2026-10-05T23:50:28Z"}  # 7:50 PM ET on Oct 5
SEP29 = "2026-09-29T04:48:54Z"


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _put(path: Path, body: object, mtime: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else json.dumps(body, sort_keys=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (_epoch(mtime), _epoch(mtime)))
    return path


def _payload(date: str, rows: list[dict]) -> dict:
    return {"date": date, "updated_at": BEFORE[date], "count": len(rows), "records": rows}


def _prop(eid: str, kick: str, *, line=24.5, odds=-110) -> dict:
    return {"event_id": eid, "event_starts_at": kick, "player_name": "P One",
            "market": "RECEIVING_YARDS", "position": "OVER", "line": line,
            "best_odds": odds, "sportsbook_best_odds": odds, "implied_probability": 52.4,
            "books": [{"book": "hardrock", "odds": odds, "odds_raw": str(odds)}],
            "model_p": 0.61, "l10_hit_rate": 70.0,
            "confidence_tier": "TIER_1_ANCHOR", "calibration_tags": []}


def _slate(nfl: Path, reports: Path, date: str, *, priced: bool = True,
           card: bool = True) -> None:
    norm, t = nfl / "normalized", BEFORE[date]
    games = [{"event_id": e, "event_starts_at": k, "away_team": a, "home_team": h,
              "market": "SPREAD"} for e, a, h, k in GAMES[date]]
    _put(norm / f"nfl_games_{date}.json", _payload(date, games), t)
    rows = [_prop(e, k) if priced else _prop(e, k, odds=None) | {"books": []}
            for e, _, _, k in GAMES[date]]
    for pre in ("nfl_props_", "nfl_calibrated_props_", "nfl_high_prob_props_",
                "nfl_matchup_props_"):
        _put(norm / f"{pre}{date}.json", _payload(date, rows), t)
    for pre in ("nfl_rosters_", "nfl_external_metrics_", "summary_"):
        _put(norm / f"{pre}{date}.json", {"date": date}, t)
    if card:
        _put(norm / f"nfl_best_bets_{date}.json", _payload(date, rows[:1]), t)
        _put(norm / f"nfl_best_bets_{date}.md", f"# card {date}\n", t)
    for e, a, h, _ in GAMES[date]:
        _put(reports / f"{date}_{a}_{h}_Game_Script.md", f"# {a} at {h}\n", t)


@pytest.fixture
def tree(tmp_path):
    nfl, reports = tmp_path / "data" / "NFL", tmp_path / "reports" / "NFL"
    for d in al.LEGACY_SLATES:
        _slate(nfl, reports, d, card=d != "2026-09-27")  # Sep 27 has no card
    scripts = {"date": "2026-09-27", "count": 1,
               "records": [{"event_id": "E927A", "matchup": "ARI @ SF"}]}
    norm = nfl / "normalized"
    for name in ("nfl_matchup_scripts_2026-09-27.json",
                 "nfl_matchup_scripts_2026-09-27_original_run.json"):
        _put(norm / name, scripts, SEP29)
    _put(norm / "nfl_matchup_scripts_2026-09-27_replay.json", {**scripts, "count": 2}, SEP29)
    _put(nfl / "scorecard" / "ledger_original_run_2026-09-27.jsonl", "{}\n", SEP29)
    _put(nfl / "scorecard" / "replay_2026-09-27_provenance.json", {}, SEP29)
    _put(nfl / "exports" / "nfl_alt_floors_2026-10-05.csv", "a,b\n", BEFORE["2026-10-05"])
    _put(nfl / "exports" / "nfl_only_filter_summary_2026-10-05.json", {}, BEFORE["2026-10-05"])
    _put(nfl / "cache" / "espn_injuries_filter_2026-10-05.json", {}, BEFORE["2026-10-05"])
    snap_rows = [
        # 2026-10-04 week file (week starts Tue Sep 29): shared by Oct 4 and Oct 5
        {"slate_date": "2026-10-05", "event_id": "E105B", "taken_at": "2026-10-05T23:50:00Z"},
        {"slate_date": "2026-10-05", "event_id": "E105A", "taken_at": "2026-10-05T23:50:00Z"},
        {"slate_date": "2026-10-05", "event_id": "E105B", "taken_at": "2026-10-06T00:30:00Z"},
        {"slate_date": "2026-10-05", "event_id": "E105A", "taken_at": "2026-10-05T12:00:00Z",
         "run_id": "R1"},
    ]
    _put(nfl / "snapshots" / "nfl_prop_snapshots_2026-09-29.jsonl",
         "".join(json.dumps(r) + "\n" for r in snap_rows), "2026-10-10T12:00:00Z")
    return nfl, reports


def _disk(*roots: Path) -> dict[str, tuple[bytes, int]]:
    return {f"{r.name}/{p.relative_to(r).as_posix()}": (p.read_bytes(), p.stat().st_mtime_ns)
            for r in roots for p in sorted(r.rglob("*"))
            if p.is_file() and "archive" not in p.parts}


def _verdicts(st: al.LegacyStatus) -> dict[str, al.FileVerdict]:
    return {Path(f.original_path).name: f for f in st.files}


def test_record_copies_under_sha_and_overwrites_nothing(tree):
    nfl, reports = tree
    before = _disk(nfl, reports)
    r = al.record_legacy(nfl, reports_dir=reports)
    assert not r.conflicts and not r.missing
    assert _disk(nfl, reports) == before  # bytes and mtimes of every original unchanged
    for e in r.recorded:
        src = (nfl / e.original_path) if not Path(e.original_path).is_absolute() \
            else Path(e.original_path)
        data = src.read_bytes()
        assert e.sha256 == hashlib.sha256(data).hexdigest() and e.size == len(data)
        assert e.path == f"archive/legacy/{e.sha256}/{src.name}"
        assert (nfl / e.path).read_bytes() == data
        assert datetime.fromisoformat(e.mtime).timestamp() == src.stat().st_mtime
        assert e.run_id is None and e.source == "legacy_disk"
    assert ar.check_archive(nfl, slates=list(al.LEGACY_SLATES),
                            legacy_slates=al.LEGACY_SLATES).gaps == []


def test_running_twice_is_idempotent(tree):
    nfl, reports = tree
    first = al.record_legacy(nfl, reports_dir=reports)
    manifest = ar.manifest_path(nfl).read_bytes()
    archive = _disk(nfl / "archive")
    again = al.record_legacy(nfl, reports_dir=reports)
    assert again.recorded == [] and len(again.already) == len(first.recorded) + len(first.already)
    assert ar.manifest_path(nfl).read_bytes() == manifest
    assert _disk(nfl / "archive") == archive


def test_different_bytes_at_the_archived_copy_are_refused(tree):
    nfl, reports = tree
    al.record_legacy(nfl, reports_dir=reports)
    [e] = [e for e in ar.read_manifest(ar.manifest_path(nfl))[0]
           if e.original_path == "normalized/summary_2026-10-04.json"]
    (nfl / e.path).write_text("tampered", encoding="utf-8")
    r = al.record_legacy(nfl, ("2026-10-04",), reports)
    assert any("different bytes" in c for c in r.conflicts)
    assert (nfl / e.path).read_text("utf-8") == "tampered"  # kept, not replaced


def test_both_statuses_and_card_presence(tree):
    nfl, reports = tree
    # Oct 4 loses its prices: rows without odds cannot be regraded.
    rows = [_prop("E104A", "2026-10-04T17:00:00Z", odds=None) | {"books": []}]
    for pre in al.PREDICTION_PREFIXES:
        _put(nfl / "normalized" / f"{pre}2026-10-04.json", _payload("2026-10-04", rows),
             BEFORE["2026-10-04"])
    al.record_legacy(nfl, reports_dir=reports)
    s927, s104, s105 = (al.legacy_status(nfl, d) for d in al.LEGACY_SLATES)
    assert s927.status == "legacy_regradable" and s927.card == {}
    assert s927.regradable_events == ["E927A", "E927B"]
    assert s927.kickoff_source == "nfl_games"
    assert s927.first_kickoff == "2026-09-27T17:00:00+00:00"
    assert s104.status == "unrecoverable" and s104.card == {"json": "original", "md": "original"}
    assert s105.status == "legacy_regradable"


def test_post_slate_is_decided_per_game(tree):
    nfl, reports = tree
    al.record_legacy(nfl, reports_dir=reports)
    st = al.legacy_status(nfl, "2026-10-05")
    v = _verdicts(st)["nfl_high_prob_props_2026-10-05.json"]
    # written 7:50 PM ET: after the 1 PM game, before the night game
    assert (v.verdict, v.original_for, v.post_slate_for) == ("partial", ["E105B"], ["E105A"])
    assert st.regradable_events == ["E105B"]
    assert _verdicts(st)["2026-10-05_HOU_IND_Game_Script.md"].verdict == "post_slate"
    assert _verdicts(st)["2026-10-05_LAC_BUF_Game_Script.md"].verdict == "original"
    assert st.card == {"json": "post_slate", "md": "partial"}  # json card: 1 PM game only


def test_files_written_after_the_slate_are_post_slate(tree):
    nfl, reports = tree
    al.record_legacy(nfl, reports_dir=reports)
    v = _verdicts(al.legacy_status(nfl, "2026-09-27"))
    for name in ("nfl_matchup_scripts_2026-09-27.json",
                 "nfl_matchup_scripts_2026-09-27_original_run.json",
                 "nfl_matchup_scripts_2026-09-27_replay.json",
                 "ledger_original_run_2026-09-27.jsonl"):
        assert v[name].verdict == "post_slate" and not v[name].via_pre_kickoff_copy, name


def test_post_slate_copy_with_a_pre_kickoff_twin_counts_as_original(tree):
    nfl, reports = tree
    norm = nfl / "normalized"
    pre = norm / "nfl_matchup_scripts_2026-09-27_original_run.json"
    os.utime(pre, (_epoch(BEFORE["2026-09-27"]),) * 2)  # this copy predates kickoff
    al.record_legacy(nfl, reports_dir=reports)
    v = _verdicts(al.legacy_status(nfl, "2026-09-27"))
    restored = v["nfl_matchup_scripts_2026-09-27.json"]  # same bytes, written Sep 29
    assert restored.verdict == "original" and restored.via_pre_kickoff_copy
    assert v["nfl_matchup_scripts_2026-09-27_replay.json"].verdict == "post_slate"  # other bytes


def test_snapshot_rows_are_judged_by_their_own_time_and_run_id(tree):
    nfl, reports = tree
    al.record_legacy(nfl, reports_dir=reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.snapshot_rows == {"pre_kickoff": 1, "post_slate": 2, "with_run_id": 1,
                                "no_kickoff": 0}
    # the file was last written Oct 10, but that never decides anything
    assert _verdicts(st)["nfl_prop_snapshots_2026-09-29.jsonl"].verdict == "per_row"


def test_helper_outputs_get_their_own_role_and_never_count(tree):
    nfl, reports = tree
    r = al.record_legacy(nfl, reports_dir=reports)
    roles = {Path(e.original_path).name: e.role for e in r.recorded}
    assert roles["espn_injuries_filter_2026-10-05.json"] == ar.HELPER_ROLE
    assert roles["nfl_only_filter_summary_2026-10-05.json"] == ar.HELPER_ROLE
    assert roles["nfl_alt_floors_2026-10-05.csv"] == ar.LEGACY_ROLE
    v = _verdicts(al.legacy_status(nfl, "2026-10-05"))
    assert v["espn_injuries_filter_2026-10-05.json"].verdict == "untracked_helper_output"
    assert v["espn_injuries_filter_2026-10-05.json"].original_for == []


def test_legacy_fields_are_validated():
    base = {"slate": "2026-10-05", "role": ar.LEGACY_ROLE, "path": "archive/legacy/x",
            "sha256": "0" * 64, "size": 1, "captured_at": "2026-10-05T23:50:28+00:00",
            "source": "legacy_disk", "season": 2026,
            "original_path": "normalized/x.json", "mtime": "2026-10-05T23:50:28+00:00"}
    assert ar.parse_entry(base).run_id is None
    with pytest.raises(ar.ArchiveError, match="mtime"):
        ar.parse_entry({**base, "mtime": "2026-10-05T23:50:28"})
    with pytest.raises(ar.ArchiveError, match="legacy roles"):
        ar.parse_entry({**base, "role": ar.TAPE_ROLE, "run_id": "R"})


def test_cli_record_and_check(tree, capsys):
    nfl, reports = tree
    args = ["record", "--nfl-dir", str(nfl), "--legacy", "--reports-dir", str(reports)]
    assert nfl_archive.main(args) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("recorded ") and ", conflicts 0, missing 0" in first
    assert nfl_archive.main(args) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("recorded 0, already archived ")
    check = ["check", "--nfl-dir", str(nfl)] + [x for d in al.LEGACY_SLATES
                                                  for x in ("--slate", d)]
    assert nfl_archive.main(check) == 0
    out = capsys.readouterr().out
    assert "STATUS 2026-09-27          legacy_regradable" in out and "card: none" in out
    assert out.strip().splitlines()[-1].startswith("slates 3, complete 3")
    assert nfl_archive.main(check + ["--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert [s["status"] for s in body["legacy"]] == ["legacy_regradable"] * 3


def test_legacy_status_source_has_no_write_calls():
    import inspect
    src = "".join(inspect.getsource(f) for f in (
        al.legacy_status, al._judge_snapshot_rows, al._kickoffs, al._covered, al._load))
    for call in ("write_", "open(", "unlink", "mkdir", "replace(", "record_"):
        assert call not in src, call


def test_unrecorded_legacy_slate_is_a_gap(tree):
    nfl, _ = tree
    gaps = ar.check_archive(nfl, slates=["2026-10-04"], legacy_slates=al.LEGACY_SLATES).gaps
    assert [(g.slate, g.kind) for g in gaps] == [("2026-10-04", "legacy_not_recorded")]
    assert not ar.manifest_path(nfl).exists()


def test_rows_without_a_probability_are_unrecoverable(tree):
    nfl, reports = tree
    rows = [{k: v for k, v in _prop("E104A", "2026-10-04T17:00:00Z").items()
             if k not in ("model_p", "l10_hit_rate")} | {"model_p": float("nan")}]
    for pre in al.PREDICTION_PREFIXES:
        _put(nfl / "normalized" / f"{pre}2026-10-04.json", _payload("2026-10-04", rows),
             BEFORE["2026-10-04"])
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    assert (st.status, st.regradable_events) == ("unrecoverable", [])


def test_a_hit_rate_alone_is_a_probability(tree):
    nfl, reports = tree
    rows = [{k: v for k, v in _prop("E104A", "2026-10-04T17:00:00Z").items()
             if k not in ("model_p", "l10_hit_rate")} | {"season_hit_rate": 55.0}]
    for pre in al.PREDICTION_PREFIXES:
        _put(nfl / "normalized" / f"{pre}2026-10-04.json", _payload("2026-10-04", rows),
             BEFORE["2026-10-04"])
    al.record_legacy(nfl, ("2026-10-04",), reports)
    assert al.legacy_status(nfl, "2026-10-04").status == "legacy_regradable"


def test_probability_source_is_counted_per_slate(tree, capsys):
    nfl, reports = tree
    base = {k: v for k, v in _prop("E104A", "2026-10-04T17:00:00Z").items()
            if k not in ("model_p", "l10_hit_rate")}
    rows = [base | {"player_name": "A", "model_p": 0.6},
            base | {"player_name": "B", "model_p": 0.58, "l5_hit_rate": 60.0},
            base | {"player_name": "C", "l20_hit_rate": 65.0}]
    for pre in al.PREDICTION_PREFIXES:
        _put(nfl / "normalized" / f"{pre}2026-10-04.json", _payload("2026-10-04", rows),
             BEFORE["2026-10-04"])
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    # each row is in 3 prediction files but is counted once
    assert st.probability_sources == {"original_model_p": 2, "recomputed_from_hit_rates": 1}
    check = ["check", "--nfl-dir", str(nfl), "--slate", "2026-10-04"]
    nfl_archive.main(check)
    assert ("original_model_p 2 (original, pre-repair), recomputed_from_hit_rates 1"
            in capsys.readouterr().out)
    nfl_archive.main(check + ["--json"])
    assert json.loads(capsys.readouterr().out)["legacy"][0]["probability_sources"] == {
        "original_model_p": 6, "recomputed_from_hit_rates": 3}
