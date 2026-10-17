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
    return _build_tree(tmp_path)


def _build_tree(tmp_path: Path) -> tuple[Path, Path]:
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
                                "no_kickoff": 0, "copies": 1}
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
    assert all("files dated by mtime" in x for x in out.splitlines() if x.startswith("STATUS"))
    assert out.strip().splitlines()[-1].startswith("slates 3, complete 3")
    assert nfl_archive.main(check + ["--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert [s["status"] for s in body["legacy"]] == ["legacy_regradable"] * 3
    assert {s["dated_by"] for s in body["legacy"]} == {"mtime"}


def test_legacy_status_source_has_no_write_calls():
    import inspect
    src = "".join(inspect.getsource(f) for f in (
        al.legacy_status, al._judge_snapshot_rows, al._snapshot_union,
        al.player_identity_lost, al._identity_lost_summary, al._kickoffs, al._covered, al._load))
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
        "original_model_p": 2, "recomputed_from_hit_rates": 1}


def test_a_pre_kickoff_twin_from_another_slate_does_not_vouch(tree):
    nfl, reports = tree
    norm = nfl / "normalized"
    oct4 = (norm / "nfl_rosters_2026-10-04.json").read_bytes()  # written before Oct 5 games
    late = norm / "nfl_rosters_2026-10-05.json"
    late.write_bytes(oct4)
    os.utime(late, (_epoch("2026-10-06T12:00:00Z"),) * 2)  # after every Oct 5 kickoff
    al.record_legacy(nfl, reports_dir=reports)
    v = _verdicts(al.legacy_status(nfl, "2026-10-05"))["nfl_rosters_2026-10-05.json"]
    assert v.verdict == "post_slate" and not v.via_pre_kickoff_copy


SNAP = "snapshots/nfl_prop_snapshots_2026-09-29.jsonl"


def _snap(nfl: Path, rows: list[dict], mtime: str) -> None:
    _put(nfl / SNAP, "".join(json.dumps(r) + "\n" for r in rows), mtime)


def _srow(eid: str, taken: str, **kw) -> dict:
    return {"slate_date": "2026-10-05", "event_id": eid, "player_name": "P One",
            "market": "RECEIVING_YARDS", "position": "OVER", "taken_at": taken,
            "line": 24.5, **kw}


def test_snapshot_rows_are_the_union_of_every_recorded_copy(tree):
    nfl, reports = tree
    a = _srow("E105B", "2026-10-05T20:00:00Z")
    b = _srow("E105B", "2026-10-05T21:00:00Z")
    c = _srow("E105A", "2026-10-05T12:00:00Z")
    _snap(nfl, [a, b], "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    _snap(nfl, [b, c], "2026-10-07T12:00:00Z")  # rewritten later: a is gone, c is new
    al.record_legacy(nfl, ("2026-10-05",), reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.status == "legacy_regradable" and st.refused_reason is None
    assert st.snapshot_rows == {"pre_kickoff": 3, "post_slate": 0, "with_run_id": 0,
                                "no_kickoff": 0, "copies": 2}  # b counted once


def test_snapshot_copies_that_disagree_refuse_the_slate(tree, capsys):
    nfl, reports = tree
    _snap(nfl, [_srow("E105B", "2026-10-05T20:00:00Z")], "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    _snap(nfl, [_srow("E105B", "2026-10-05T20:00:00Z", best_odds=-125)],  # other price
          "2026-10-07T12:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.status == "refused" and "snapshot copies disagree on row" in st.refused_reason
    assert "across two copies" in st.refused_reason
    # the copies are named in the same order as their values
    copies, what = st.refused_reason.split(" across two copies ", 1)[1].rsplit(": ", 1)
    first, second = copies.split(" vs ")
    odds = [json.loads((nfl / c).read_text()).get("best_odds") for c in (first, second)]
    assert what == f"field 'best_odds' {odds[0]!r} vs {odds[1]!r} with the same books"
    assert sorted(odds, key=str) == [-125, None]
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-05"]) == 1
    assert "REFUSED snapshot copies disagree" in capsys.readouterr().out


# Shaped after the two keys that refused Oct 4 and Oct 5 on Daniel's machine: one
# copy of nfl_prop_snapshots_2026-09-29.jsonl, no run_id, the same prop_key and
# taken_at on several rows. Each row is what snapshots.append_snapshot writes
# (SNAPSHOT_FIELDS + slate_date, taken_at, books as {book: odds}).
REAL_KEYS = {
    "first team reception": ("05cd570bc7ad0f6493e9e1388095741bf2d05ead",
                             "FIRST_TEAM_RECEPTION", "2026-10-01T17:32:38.856999+00:00"),
    "first team td scorer": ("23a2e7817d5c4b313d5f0960ff102293b8f0cd2c",
                             "FIRST_TEAM_TOUCHDOWN", "2026-10-01T17:36:42.360800+00:00"),
}


def _wrow(slate: str, name: str, *, line, books: dict, team: str = "") -> dict:
    eid, market, taken = REAL_KEYS[name]
    odds = max(books.values())
    return {"event_id": eid, "event_starts_at": GAMES[slate][0][3], "matchup": "",
            "team": team, "player_name": name, "market": market, "position": "OVER",
            "line": line, "scope": "full_game", "best_odds": odds,
            "implied_probability": 50.0, "is_consensus_line": True,
            "slate_date": slate, "taken_at": taken, "books": books}


@pytest.mark.parametrize("slate", ["2026-10-04", "2026-10-05"])
def test_rows_under_one_prop_key_with_other_teams_lines_or_books_are_not_a_conflict(
        tree, slate):
    nfl, reports = tree
    rows = [_wrow(slate, "first team reception", line=0.5, team="LAC", books={"hardrock": -110}),
            _wrow(slate, "first team reception", line=0.5, team="BUF", books={"hardrock": -120}),
            _wrow(slate, "first team reception", line=1.5, team="LAC", books={"hardrock": 150}),
            # empty line string; the second row only adds a book: no conflict
            _wrow(slate, "first team td scorer", line="", team="LAC", books={"hardrock": 400}),
            _wrow(slate, "first team td scorer", line="", team="LAC",
                  books={"hardrock": 400, "draftkings": 450}),
            _wrow(slate, "first team td scorer", line="", team="BUF", books={"draftkings": 450}),
            # the same row written twice is one row
            _wrow(slate, "first team td scorer", line="", team="BUF", books={"draftkings": 450})]
    _snap(nfl, rows, "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, (slate,), reports)
    st = al.legacy_status(nfl, slate)
    assert st.status == "legacy_regradable" and st.refused_reason is None
    # the {hardrock} and {hardrock, draftkings} rows are both kept; the repeat is one
    assert st.snapshot_rows == {"pre_kickoff": 6, "post_slate": 0, "with_run_id": 0,
                                "no_kickoff": 0, "copies": 1}


def test_an_extra_book_with_the_same_shared_price_is_not_a_conflict(tree):
    nfl, reports = tree
    _snap(nfl, [_wrow("2026-10-05", "first team reception", line=0.5,
                      books={"hardrock": -110}),
                _wrow("2026-10-05", "first team reception", line=0.5,
                      books={"hardrock": -110, "fanduel": 105})],
          "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.status == "legacy_regradable" and st.refused_reason is None
    assert st.snapshot_rows["pre_kickoff"] == 2  # both rows kept, unaltered


def test_a_shared_book_with_another_price_is_refused_despite_an_extra_book(tree, capsys):
    nfl, reports = tree
    _snap(nfl, [_wrow("2026-10-05", "first team reception", line=0.5,
                      books={"hardrock": -110}),
                _wrow("2026-10-05", "first team reception", line=0.5,
                      books={"hardrock": -125, "fanduel": 105})],
          "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.status == "refused" and "snapshot rows disagree on row" in st.refused_reason
    assert " within one file archive/legacy/" in st.refused_reason
    assert st.refused_reason.endswith(": book 'hardrock' price -110 vs -125")
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-05"]) == 1
    assert "REFUSED snapshot rows disagree" in capsys.readouterr().out


def test_every_distinct_row_under_a_key_is_compared(tree):
    nfl, reports = tree
    # hardrock-only first, then two fanduel rows that disagree with each other
    _snap(nfl, [_wrow("2026-10-05", "first team reception", line=0.5,
                      books={"hardrock": -110}),
                _wrow("2026-10-05", "first team reception", line=0.5, books={"fanduel": 105}),
                _wrow("2026-10-05", "first team reception", line=0.5, books={"fanduel": 120})],
          "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    assert al.legacy_status(nfl, "2026-10-05").status == "refused"


@pytest.mark.parametrize("line", [0.5, ""])
def test_a_true_duplicate_with_another_price_in_one_copy_is_refused(tree, capsys, line):
    nfl, reports = tree
    _snap(nfl, [_wrow("2026-10-05", "first team reception", line=line,
                      books={"hardrock": -110}),
                _wrow("2026-10-05", "first team reception", line=line,
                      books={"hardrock": -125})],  # same book, side and line; other price
          "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    st = al.legacy_status(nfl, "2026-10-05")
    assert st.status == "refused" and "within one file" in st.refused_reason
    assert st.refused_reason.endswith(": book 'hardrock' price -110 vs -125")
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-05"]) == 1
    assert "REFUSED snapshot rows disagree" in capsys.readouterr().out


# Order independence: 10a reruns must come out byte-identical.
HR_FD = {"hardrock": -110, "fanduel": 105}
HR_DK = {"hardrock": -110, "draftkings": 200}


def _rows_ab(slate: str = "2026-10-05") -> tuple[dict, dict]:
    return (_wrow(slate, "first team reception", line=0.5, books=HR_FD),
            _wrow(slate, "first team reception", line=0.5, books=HR_DK))


def _check_text(base: Path, nfl: Path, capsys, *extra: str) -> tuple[int, str]:
    capsys.readouterr()
    code = nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-05", *extra])
    return code, capsys.readouterr().out.replace(str(base), "<ROOT>")


def _union(nfl: Path) -> tuple[list[dict], str | None]:
    entries, _ = ar.read_manifest(ar.manifest_path(nfl))
    snaps = [e for e in entries if (e.original_path or "") == SNAP]
    rows, refused, _ = al._snapshot_union(nfl, snaps, "2026-10-05")
    return rows, refused


def test_non_conflicting_rows_under_one_key_are_all_kept_unaltered(tree):
    nfl, reports = tree
    a, b = _rows_ab()
    _snap(nfl, [a, b, dict(a)], "2026-10-05T21:00:00Z")  # a twice, byte-identical
    al.record_legacy(nfl, ("2026-10-05",), reports)
    rows, refused = _union(nfl)
    assert refused is None
    assert rows == sorted([a, b], key=al._canon)  # both kept, books not merged
    assert al.legacy_status(nfl, "2026-10-05").snapshot_rows["pre_kickoff"] == 2


def test_row_order_inside_one_copy_does_not_change_the_result(tmp_path, capsys):
    a, b = _rows_ab()
    results = []
    for name, rows in (("fwd", [a, b, a]), ("rev", [b, a, b])):
        nfl, reports = _build_tree(tmp_path / name)
        _snap(nfl, rows, "2026-10-05T21:00:00Z")
        al.record_legacy(nfl, ("2026-10-05",), reports)
        # the copy's bytes (and so its sha256) differ, so compare the text check
        # output, which names files by original path, plus the kept rows
        results.append((_check_text(tmp_path / name, nfl, capsys), _union(nfl),
                        al.legacy_status(nfl, "2026-10-05").snapshot_rows))
    assert results[0] == results[1]
    assert results[0][1] == (sorted([a, b], key=al._canon), None)


def _two_copies(base: Path, swap: bool, rows_1: list[dict], rows_2: list[dict]
                ) -> tuple[Path, Path]:
    nfl, reports = _build_tree(base)
    copies = [(rows_1, "2026-10-05T21:00:00Z"), (rows_2, "2026-10-07T12:00:00Z")]
    for rows, mtime in (copies[::-1] if swap else copies):
        _snap(nfl, rows, mtime)
        al.record_legacy(nfl, ("2026-10-05",), reports)
    return nfl, reports


def test_copy_order_does_not_change_the_check_output(tmp_path, capsys):
    a, b = _rows_ab()
    outs = []
    for swap in (False, True):
        base = tmp_path / ("swapped" if swap else "listed")
        nfl, _ = _two_copies(base, swap, [a], [b, a])
        outs.append((_check_text(base, nfl, capsys), _check_text(base, nfl, capsys, "--json"),
                     _union(nfl)))
    assert outs[0] == outs[1]  # byte-identical text and JSON output
    assert outs[0][2] == (sorted([a, b], key=al._canon), None)


@pytest.mark.parametrize("swap", [False, True])
def test_a_conflict_is_refused_in_either_order(tmp_path, capsys, swap):
    a = _wrow("2026-10-05", "first team reception", line=0.5, books={"hardrock": -110})
    b = _wrow("2026-10-05", "first team reception", line=0.5,
              books={"hardrock": -125, "fanduel": 105})
    # across two copies, either recording order: identical refusal
    base = tmp_path / "copies"
    nfl, _ = _two_copies(base, swap, [a], [b])
    code, out = _check_text(base, nfl, capsys)
    assert code == 1 and "REFUSED snapshot copies disagree" in out
    _, reason = _union(nfl)
    assert reason is not None
    # inside one copy, either row order
    nfl1, reports1 = _build_tree(tmp_path / "one")
    _snap(nfl1, [b, a] if swap else [a, b], "2026-10-05T21:00:00Z")
    al.record_legacy(nfl1, ("2026-10-05",), reports1)
    st = al.legacy_status(nfl1, "2026-10-05")
    assert "across two copies" in reason and "within one file" in st.refused_reason
    # one file names the pair by canonical JSON; two copies name it in path order
    assert st.status == "refused"
    assert st.refused_reason.rsplit(": ", 1)[1] == "book 'hardrock' price -110 vs -125"
    assert reason.rsplit(": ", 1)[1] in ("book 'hardrock' price -110 vs -125",
                                         "book 'hardrock' price -125 vs -110")


def test_a_conflict_reason_does_not_depend_on_copy_order(tmp_path):
    a = _wrow("2026-10-05", "first team reception", line=0.5, books={"hardrock": -110})
    b = _wrow("2026-10-05", "first team reception", line=0.5,
              books={"hardrock": -125, "fanduel": 105})
    reasons = [al.legacy_status(_two_copies(tmp_path / str(swap), swap, [a], [b])[0],
                                "2026-10-05").refused_reason for swap in (False, True)]
    assert reasons[0] is not None and reasons[0] == reasons[1]


# Line identity: numeric lines compare as floats; None, "" and numbers stay apart.
def _hr_rows(line_a, line_b, books_a: dict, books_b: dict) -> list[dict]:
    return [_wrow("2026-10-05", "first team reception", line=line_a, books=books_a),
            _wrow("2026-10-05", "first team reception", line=line_b, books=books_b)]


def _status_of(tree, rows: list[dict]) -> al.LegacyStatus:
    nfl, reports = tree
    _snap(nfl, rows, "2026-10-05T21:00:00Z")
    al.record_legacy(nfl, ("2026-10-05",), reports)
    return al.legacy_status(nfl, "2026-10-05")


def test_line_1_and_1_0_are_one_key_so_a_hard_rock_price_change_is_refused(tree):
    st = _status_of(tree, _hr_rows(1, 1.0, {"hardrock": -110}, {"hardrock": -125}))
    assert st.status == "refused"
    assert st.refused_reason.endswith(": book 'hardrock' price -110 vs -125")


def test_line_1_and_1_0_with_the_same_price_and_an_extra_book_are_kept(tree):
    st = _status_of(tree, _hr_rows(1, 1.0, {"hardrock": -110},
                                   {"hardrock": -110, "fanduel": 105}))
    assert st.status == "legacy_regradable" and st.refused_reason is None
    assert st.snapshot_rows["pre_kickoff"] == 2


def test_none_empty_string_and_numeric_lines_are_separate_keys():
    assert al._line_key(1) == al._line_key(1.0)
    assert al._line_key(0) == al._line_key(0.0) == al._line_key(-0.0)
    assert len({al._line_key(None), al._line_key(""), al._line_key(0)}) == 3
    assert al._line_key(False) != al._line_key(0)  # a bool is not a number here
    assert al._line_key(float("nan")) == al._line_key(float("nan"))


def test_none_and_empty_string_lines_with_other_prices_are_not_compared(tree):
    st = _status_of(tree, _hr_rows(None, "", {"hardrock": -110}, {"hardrock": -125}))
    assert st.refused_reason is None and st.snapshot_rows["pre_kickoff"] == 2


def test_two_nan_lines_that_differ_by_an_extra_book_are_not_refused(tree):
    nan = float("nan")
    st = _status_of(tree, _hr_rows(nan, nan, {"hardrock": -110},
                                   {"hardrock": -110, "fanduel": 105}))
    assert st.status != "refused" and st.refused_reason is None
    assert st.snapshot_rows["pre_kickoff"] == 2


def test_two_nan_lines_with_another_shared_book_price_are_refused(tree):
    nan = float("nan")
    st = _status_of(tree, _hr_rows(nan, nan, {"hardrock": -110},
                                   {"hardrock": -125, "fanduel": 105}))
    assert st.status == "refused"
    assert st.refused_reason.endswith(": book 'hardrock' price -110 vs -125")


# Player identity lost: real rows from Daniel's archived copy of
# nfl_prop_snapshots_2026-09-29.jsonl (archive/legacy/b08596eb.../), each line
# "<line number> <row>" exactly as printed by the read-only extract. The subset
# holds lines 1063-1067 (DET First Team Reception), the first two rows of each of
# the 15 keys where the writer stored the market label as player_name, and the
# first five other rows of each slate.
REAL_EXTRACT = Path(__file__).parent / "fixtures" / "nfl" / "legacy_snapshot_rows_2026-09-29.txt"


def _real_rows(lines: range | None = None) -> list[tuple[int, str]]:
    out = []
    for text in REAL_EXTRACT.read_text(encoding="utf-8").splitlines():
        n, row = text.split(" ", 1)
        if lines is None or int(n) in lines:
            out.append((int(n), row))
    return out


def _snap_at_lines(nfl: Path, rows: list[tuple[int, str]], mtime: str) -> None:
    """The rows at their original line numbers; the gaps are blank lines (skipped)."""
    body = [""] * max(n for n, _ in rows)
    for n, row in rows:
        body[n - 1] = row
    _put(nfl / SNAP, "\n".join(body) + "\n", mtime)


DET_1063_1067 = range(1063, 1068)


def test_the_five_labels_are_the_ones_in_the_real_rows():
    labels = {(r["market"], r["player_name"]) for r in (json.loads(t) for _, t in _real_rows())
              if r["player_name"] in al.MULTI_PLAYER_MARKET_LABELS.values()}
    assert labels == set(al.MULTI_PLAYER_MARKET_LABELS.items())


def test_det_first_team_reception_lines_1063_1067_are_set_aside_not_refused(tree, capsys):
    nfl, reports = tree
    rows = _real_rows(DET_1063_1067)
    assert [json.loads(t)["books"] for _, t in rows] == [
        {"draftkings": 205}, {"draftkings": 340}, {"draftkings": 330},
        {"draftkings": 1000}, {"draftkings": 450}]
    _snap_at_lines(nfl, rows, "2026-10-10T12:00:00Z")
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    assert st.status == "legacy_regradable" and st.refused_reason is None
    # not pre_kickoff: none of the five rows is counted anywhere
    assert st.snapshot_rows == {"pre_kickoff": 0, "post_slate": 0, "with_run_id": 0,
                                "no_kickoff": 0, "copies": 1}
    u = st.unrecoverable_rows
    (path, nums), = u["lines"].items()
    assert path.startswith("archive/legacy/")
    assert path.endswith("/nfl_prop_snapshots_2026-09-29.jsonl")
    assert u == {"label": "unrecoverable_row: player_identity_lost", "rows": 5, "keys": 1,
                 "keys_kept": 0, "by_market": {"FIRST_TEAM_RECEPTION": 5},
                 "by_game": [{"event_id": "05cd570bc7ad0f6493e9e1388095741bf2d05ead",
                              "matchup": "DET @ CAR", "rows": 5}],
                 "lines": {path: [1063, 1064, 1065, 1066, 1067]}}
    capsys.readouterr()
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-04"]) == 0
    out = capsys.readouterr().out
    assert ("  unrecoverable_row: player_identity_lost: 5 rows under 1 keys set aside, "
            "not counted as pre_kickoff (0 keys kept)\n") in out
    assert "    by market: FIRST_TEAM_RECEPTION 5\n" in out
    assert "    by game: 05cd570bc7ad0f6493e9e1388095741bf2d05ead (DET @ CAR) 5\n" in out
    assert f"    lines {path}: 1063-1067\n" in out
    assert "REFUSED" not in out
    capsys.readouterr()
    nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-04", "--json"])
    (legacy,) = json.loads(capsys.readouterr().out)["legacy"]
    assert legacy["unrecoverable_rows"] == u


def _named(row: str, name: str, **kw) -> dict:
    return {**json.loads(row), "player_name": name, **kw}


def test_a_real_player_price_conflict_beside_an_excluded_group_still_refuses(tree, capsys):
    nfl, reports = tree
    rows = _real_rows(DET_1063_1067)
    first = rows[0][1]
    # a named player in the same market, same key, DraftKings +205 vs +210
    a = _named(first, "P One")
    b = _named(first, "P One", books={"draftkings": 210}, best_odds=210)
    _snap_at_lines(nfl, rows + [(1068, json.dumps(a)), (1069, json.dumps(b))],
                   "2026-10-10T12:00:00Z")
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    assert st.status == "refused"
    assert "snapshot rows disagree on row" in st.refused_reason and "'p one'" in st.refused_reason
    assert st.refused_reason.endswith(": book 'draftkings' price 205 vs 210")
    assert st.unrecoverable_rows["rows"] == 5 and st.unrecoverable_rows["keys"] == 1
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--slate", "2026-10-04"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED snapshot rows disagree" in out and "player_identity_lost: 5 rows" in out


def test_a_real_player_in_a_multi_player_market_gets_the_normal_check(tree):
    nfl, reports = tree
    first = _real_rows(DET_1063_1067)[0][1]
    rows = [(1, json.dumps(_named(first, "P One"))),
            (2, json.dumps(_named(first, "P One", books={"draftkings": 205, "fanduel": 230})))]
    _snap_at_lines(nfl, rows, "2026-10-10T12:00:00Z")
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    assert st.refused_reason is None and st.unrecoverable_rows == {}
    assert st.snapshot_rows["pre_kickoff"] == 2  # both kept, compared, counted


@pytest.mark.parametrize("name,market", [
    ("first team reception", "FIRST_TEAM_RECEPTION"),   # not the exact label
    ("First Team Reception ", "FIRST_TEAM_RECEPTION"),  # trailing space
    ("First TD Scorer", "FIRST_TEAM_RECEPTION"),        # another market's label
    ("First Team Reception", "first_team_reception"),   # market not on the list as written
    ("Anytime TD Scorer", "ANYTIME_TD"),                # not one of the five markets
])
def test_only_the_exact_market_label_pair_is_set_aside(tree, name, market):
    nfl, reports = tree
    rows = [_named(t, name, market=market) for _, t in _real_rows(range(1063, 1065))]
    assert not any(al.player_identity_lost(r) for r in rows)
    _snap_at_lines(nfl, [(i + 1, json.dumps(r)) for i, r in enumerate(rows)],
                   "2026-10-10T12:00:00Z")
    al.record_legacy(nfl, ("2026-10-04",), reports)
    st = al.legacy_status(nfl, "2026-10-04")
    # compared as usual: DraftKings +205 vs +340 under one key is a conflict
    assert st.status == "refused" and st.unrecoverable_rows == {}
    assert st.refused_reason.endswith(": book 'draftkings' price 205 vs 340")


@pytest.mark.parametrize("slate,lost_rows,lost_keys,kept_keys,by_market", [
    ("2026-10-04", 17, 7, 5, {"FIRST_TD": 4, "FIRST_TEAM_RECEPTION": 5,
                              "FIRST_TEAM_TOUCHDOWN": 4, "LAST_TOUCHDOWN": 4}),
    ("2026-10-05", 16, 8, 5, {"FIRST_TD": 4, "FIRST_TEAM_TOUCHDOWN": 4,
                              "LAST_TOUCHDOWN": 4, "MOST_RECEIVING_YARDS": 4}),
])
def test_real_subset_sets_aside_the_15_label_keys(tree, slate, lost_rows, lost_keys,
                                                  kept_keys, by_market):
    nfl, reports = tree
    rows = _real_rows()
    # 7 keys on Oct 4 + 8 on Oct 5 = the 15 colliding keys of the full 648-row file
    _snap_at_lines(nfl, rows, "2026-10-10T12:00:00Z")
    al.record_legacy(nfl, (slate,), reports)
    st = al.legacy_status(nfl, slate)
    assert st.status == "legacy_regradable" and st.refused_reason is None
    u = st.unrecoverable_rows
    assert (u["rows"], u["keys"], u["keys_kept"], u["by_market"]) == (
        lost_rows, lost_keys, kept_keys, by_market)
    assert st.snapshot_rows["pre_kickoff"] == kept_keys  # the clean rows, one per key


def test_check_prints_line_numbers_as_ranges():
    assert nfl_archive._ranges([1063, 1064, 1065, 1066, 1067, 1291, 1293]) == "1063-1067,1291,1293"
    assert nfl_archive._ranges([]) == ""
