"""ESPN backfill: parsing, polite fetching, cache, store build/copy/verify (offline)."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from outlier_nba.ingest import espn
from outlier_nba.ingest.espn import (
    BlockedError,
    EspnCache,
    GameRow,
    Throttle,
    parse_scoreboard,
    parse_summary_box,
    season_label,
)

FIX = Path(__file__).parent / "fixtures" / "nba"


def _sb() -> dict:
    return json.loads((FIX / "espn_scoreboard_20170101.json").read_text(encoding="utf-8"))


def _summary() -> dict:
    return json.loads((FIX / "espn_summary_400899374.json").read_text(encoding="utf-8"))


def test_season_label_uses_espn_end_year() -> None:
    assert season_label(2017) == "2016-17"
    assert season_label(2000) == "1999-00"


def test_parse_scoreboard_maps_teams_scores_and_ot() -> None:
    games, gaps = parse_scoreboard(_sb())
    assert gaps == []
    by_id = {g.game_id: g for g in games}
    ot = by_id["400899374"]
    # ESPN "SA" is the registry's SAS; the 5th period marks a single overtime
    assert (ot.home, ot.away) == ("ATL", "SAS")
    assert ot.home_pts == 114 and ot.away_pts > 0
    assert ot.periods == 5 and ot.ot_periods == 1
    assert ot.season == "2016-17" and ot.season_type == 2
    assert ot.neutral is False and ot.attendance == 18088
    assert ot.home_fga == 92 and ot.home_fta is not None
    reg = by_id["400899381"]
    assert reg.ot_periods == 0 and reg.periods == 4


def test_parse_scoreboard_skips_preseason_and_flags_non_final() -> None:
    payload = _sb()
    pre = copy.deepcopy(payload["events"][0])
    pre["id"] = "pre1"
    pre["season"]["type"] = 1
    live = copy.deepcopy(payload["events"][1])
    live["id"] = "live1"
    live["competitions"][0]["status"]["type"]["name"] = "STATUS_IN_PROGRESS"
    live["status"]["type"]["name"] = "STATUS_IN_PROGRESS"
    payload["events"] += [pre, live]
    games, gaps = parse_scoreboard(payload)
    ids = {g.game_id for g in games}
    assert "pre1" not in ids and "live1" not in ids
    kinds = {(g["kind"], g["key"]) for g in gaps}
    assert ("NOT_FINAL", "live1") in kinds
    assert ("SKIPPED_SEASON_TYPE", "pre1") in kinds


def test_parse_scoreboard_unresolved_team_is_a_gap_not_a_guess() -> None:
    payload = _sb()
    payload["events"][0]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "ZZZ"
    games, gaps = parse_scoreboard(payload)
    assert "400899374" not in {g.game_id for g in games}
    assert any(g["kind"] == "UNRESOLVED_TEAM" and g["key"] == "400899374" for g in gaps)


def test_parse_summary_box_reads_offensive_rebounds_and_turnovers() -> None:
    box = parse_summary_box(_summary())
    assert set(box) == {"home", "away"}
    assert box["away"]["oreb"] == 9 and box["away"]["tov"] == 12
    assert box["home"]["oreb"] is not None and box["home"]["tov"] is not None


def test_parse_summary_box_missing_teams_returns_empty() -> None:
    assert parse_summary_box({"boxscore": {}}) == {}


def test_throttle_pauses_between_bursts() -> None:
    sleeps: list[float] = []
    t = Throttle(burst=3, gap=0.1, pause=5.0, sleep=sleeps.append)
    for _ in range(7):
        t.wait()
    assert sleeps.count(5.0) == 2  # after request 3 and request 6
    assert all(s in (0.1, 5.0) for s in sleeps)


def test_cache_roundtrip_and_fetch_skips_cached(tmp_path: Path) -> None:
    cache = EspnCache(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return _sb()

    th = Throttle(burst=5, gap=0, pause=0, sleep=lambda s: None)
    n1 = espn.fetch_scoreboards(["20170101"], cache, fetch, th)
    n2 = espn.fetch_scoreboards(["20170101"], cache, fetch, th)
    assert (n1, n2) == (1, 0) and len(calls) == 1
    assert "dates=20170101" in calls[0]
    assert cache.get_scoreboard("20170101")["events"]


def test_blocked_response_stops_the_run(tmp_path: Path) -> None:
    cache = EspnCache(tmp_path)

    def fetch(url: str) -> dict:
        raise BlockedError("403")

    th = Throttle(burst=5, gap=0, pause=0, sleep=lambda s: None)
    with pytest.raises(BlockedError):
        espn.fetch_scoreboards(["20170101", "20170102"], cache, fetch, th)
    assert cache.get_scoreboard("20170102") is None


def test_summary_cache_is_trimmed_to_box_score(tmp_path: Path) -> None:
    cache = EspnCache(tmp_path)
    full = _summary()
    full["plays"] = [{"x": 1}] * 500
    th = Throttle(burst=5, gap=0, pause=0, sleep=lambda s: None)
    espn.fetch_summaries(["400899374"], cache, lambda url: full, th)
    cached = cache.get_summary("400899374")
    assert "plays" not in cached and "boxscore" in cached


def test_season_dates_cover_the_covid_calendar() -> None:
    dates = espn.season_dates("2019-20")
    assert dates[0] == "20191001" and "20201011" in dates  # bubble finals ran into October
    assert espn.season_dates("2020-21")[0] == "20201201"
    assert len(set(dates)) == len(dates)


def _seed_cache(tmp_path: Path) -> EspnCache:
    cache = EspnCache(tmp_path / "cache")
    cache.put_scoreboard("20170101", _sb())
    cache.put_scoreboard("20170102", {"events": []})
    cache.put_summary("400899374", _summary())
    return cache


def test_build_store_merges_box_and_records_gaps(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    db = tmp_path / "store.sqlite3"
    stats = espn.build_store(cache, db)
    assert stats["games"] == 3 and stats["with_box"] == 1
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT home_oreb, home_tov, away_oreb, away_tov, ot_periods FROM games "
            "WHERE game_id='400899374'"
        ).fetchone()
        assert row[1] is not None and row[2] == 9 and row[4] == 1
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM gaps")}
    assert "MISSING_BOX" in kinds and "EMPTY_DATE" in kinds


def test_build_store_is_deterministic_and_replaces_existing(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    db = tmp_path / "store.sqlite3"
    espn.build_store(cache, db)
    espn.build_store(cache, db)  # rebuild over an existing file, no duplicate rows
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 3


def test_copy_store_verifies_counts_from_a_fresh_process(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    tmp_db = tmp_path / "tmp.sqlite3"
    stats = espn.build_store(cache, tmp_db)
    dest = tmp_path / "out" / "nba_games.sqlite3"
    report = espn.install_store(tmp_db, dest)
    assert report["games"] == stats["games"] and dest.exists()


def test_install_store_refuses_on_count_mismatch(tmp_path: Path, monkeypatch) -> None:
    cache = _seed_cache(tmp_path)
    tmp_db = tmp_path / "tmp.sqlite3"
    espn.build_store(cache, tmp_db)
    real = espn.verify_store_fresh_process

    def lying(path: Path) -> dict:
        out = real(path)
        out["games"] += 1
        return out

    monkeypatch.setattr(espn, "verify_store_fresh_process", lying)
    dest = tmp_path / "out" / "nba_games.sqlite3"
    with pytest.raises(RuntimeError, match="count mismatch"):
        espn.install_store(tmp_db, dest)
    assert not dest.exists()


def _store(tmp_path: Path, name: str, scoreboards: dict, summaries: bool = True) -> Path:
    cache = EspnCache(tmp_path / f"cache-{name}")
    for day, payload in scoreboards.items():
        cache.put_scoreboard(day, payload)
    if summaries:
        cache.put_summary("400899374", _summary())
    db = tmp_path / f"{name}.sqlite3"
    espn.build_store(cache, db)
    return db


def _sb_other_season() -> dict:
    payload = _sb()
    for i, ev in enumerate(payload["events"]):
        ev["id"] = f"other{i}"
        ev["season"] = {"year": 2018, "type": 2, "slug": "regular-season"}
    return payload


def test_install_refuses_a_store_with_less_history_and_keeps_the_old_one(tmp_path: Path) -> None:
    full = _store(tmp_path, "full", {"20170101": _sb(), "20170102": _sb_other_season()})
    dest = tmp_path / "out" / "nba_games.sqlite3"
    espn.install_store(full, dest)
    before = dest.read_bytes()
    thin = _store(tmp_path, "thin", {"20170102": _sb_other_season()})  # one season gone
    with pytest.raises(espn.StoreRegressionError, match="less history"):
        espn.install_store(thin, dest)
    assert dest.read_bytes() == before  # the previous store is untouched


def test_install_refuses_a_store_that_lost_box_scores(tmp_path: Path) -> None:
    with_box = _store(tmp_path, "box", {"20170101": _sb()})
    dest = tmp_path / "out" / "nba_games.sqlite3"
    espn.install_store(with_box, dest)
    no_box = _store(tmp_path, "nobox", {"20170101": _sb()}, summaries=False)
    with pytest.raises(espn.StoreRegressionError, match="box"):
        espn.install_store(no_box, dest)


def test_install_accepts_a_store_that_grows(tmp_path: Path) -> None:
    small = _store(tmp_path, "small", {"20170101": _sb()})
    dest = tmp_path / "out" / "nba_games.sqlite3"
    espn.install_store(small, dest)
    bigger = _store(tmp_path, "bigger", {"20170101": _sb(), "20170102": _sb_other_season()})
    assert espn.install_store(bigger, dest)["games"] > espn.store_counts(small)["games"]


def test_load_games_returns_game_rows(tmp_path: Path) -> None:
    cache = _seed_cache(tmp_path)
    db = tmp_path / "store.sqlite3"
    espn.build_store(cache, db)
    games = espn.load_games(db)
    assert all(isinstance(g, GameRow) for g in games)
    assert [g.start_utc for g in games] == sorted(g.start_utc for g in games)
    with_box = [g for g in games if g.home_oreb is not None]
    assert len(with_box) == 1
