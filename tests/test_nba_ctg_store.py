"""CTG local store: versioned captures, strict as-of reads, legacy-store migration."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from outlier_nba.ingest import ctg

FIX = Path(__file__).parent / "fixtures" / "ctg"
T1 = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)
T3 = datetime(2026, 11, 8, 14, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "ctg.sqlite3")
    ctg.ensure_schema(conn)
    return conn


def _games(**changes):
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    return [dataclasses.replace(g, **changes) for g in games]


# --------------------------------------------------------------------------- season tables


def test_the_same_capture_is_stored_once(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    first = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    again = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    assert first.inserted and not again.inserted
    assert again.capture_id == first.capture_id
    assert conn.execute("SELECT COUNT(*) FROM ctg_capture").fetchone()[0] == 1


def test_snapshot_asof_is_strictly_before_the_slate(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    # a snapshot taken on the slate's own Eastern date is not usable for that slate
    assert ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-03") is None
    snap = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-04")
    assert snap is not None and snap.as_of_utc == T1
    assert snap.teams["BOS"]["off_pts_poss"] == pytest.approx(114.0)


def test_a_table_that_reverts_is_read_as_its_latest_capture(tmp_path) -> None:
    """A at T1, B at T2, A again at T3: the slate after T3 must see A, not B."""
    conn = _store(tmp_path)
    a_path = FIX / "fourfactors_synthetic.csv"
    b_path = tmp_path / "fourfactors_b.csv"
    b_path.write_bytes(a_path.read_bytes().replace(b"114.0", b"99.0", 1))
    a, b = ctg.read_season_table(a_path), ctg.read_season_table(b_path)
    assert a.sha256 != b.sha256
    for table, ts in ((a, T1), (b, T2), (a, T3)):
        assert ctg.import_season_table(conn, table, season="2026-27", as_of_utc=ts).inserted
    snap = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-09")
    assert snap is not None and snap.as_of_utc == T3
    assert snap.teams["BOS"]["off_pts_poss"] == pytest.approx(114.0)
    mid = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-07")
    assert mid is not None and mid.teams["BOS"]["off_pts_poss"] == pytest.approx(99.0)


def test_future_as_of_is_rejected(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    with pytest.raises(ctg.CTGParseError, match="future"):
        ctg.import_season_table(
            conn, table, season="2026-27", as_of_utc=datetime(2999, 1, 1, tzinfo=UTC)
        )


def test_naive_as_of_is_rejected(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    with pytest.raises(ctg.CTGParseError, match="timezone"):
        ctg.import_season_table(conn, table, season="2026-27", as_of_utc=datetime(2026, 11, 3))


# --------------------------------------------------------------------------- game logs


def test_load_team_games_round_trips(tmp_path) -> None:
    conn = _store(tmp_path)
    games = _games()
    ctg.store_gamelog(conn, games, file_sha256="a" * 64)
    loaded = ctg.load_team_games(conn, season="2026-27")
    assert loaded == games
    assert loaded[0].metrics["spread"] == pytest.approx(-4.0)
    assert ctg.load_team_games(conn, season="2025-26") == []


def test_a_later_capture_supplies_the_current_values(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games(off_pp100=90.0), file_sha256="a" * 64, as_of_utc=T1)
    ctg.store_gamelog(conn, _games(off_pp100=130.0), file_sha256="b" * 64, as_of_utc=T2)
    assert {g.off_pp100 for g in ctg.load_team_games(conn)} == {130.0}


def test_an_older_capture_never_overwrites_a_newer_one(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games(off_pp100=130.0), file_sha256="n" * 64, as_of_utc=T2)
    ctg.store_gamelog(conn, _games(off_pp100=90.0), file_sha256="o" * 64, as_of_utc=T1)
    assert {g.off_pp100 for g in ctg.load_team_games(conn)} == {130.0}


def test_a_game_stays_visible_at_its_first_capture_after_a_redownload(tmp_path) -> None:
    """P1: re-downloading a game must not move it later in time for historical reads."""
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games(off_pp100=110.0), file_sha256="a" * 64, as_of_utc=T1)
    ctg.store_gamelog(conn, _games(off_pp100=111.0), file_sha256="b" * 64, as_of_utc=T2)
    between = ctg.load_team_games(conn, as_of_before=T1 + timedelta(hours=1))
    assert len(between) == 4 and {g.off_pp100 for g in between} == {110.0}
    assert ctg.load_team_games(conn, as_of_before=T1) == []
    latest = ctg.load_team_games(conn, as_of_before=T2 + timedelta(seconds=1))
    assert {g.off_pp100 for g in latest} == {111.0}


@pytest.mark.parametrize("playoffs_first", [True, False])
def test_a_playoffs_label_survives_any_import_order(tmp_path, playoffs_first) -> None:
    conn = _store(tmp_path)
    playoff = (_games(season_type="playoffs"), "p" * 64, T1)  # older file, explicit label
    regular = (_games(), "r" * 64, T2)  # newer view that mislabels playoff games "regular"
    for games, sha, ts in (playoff, regular) if playoffs_first else (regular, playoff):
        ctg.store_gamelog(conn, games, file_sha256=sha, as_of_utc=ts)
    assert {g.season_type for g in ctg.load_team_games(conn)} == {"playoffs"}


def test_an_unspecified_capture_never_erases_a_label(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games(), file_sha256="r" * 64, as_of_utc=T1)
    ctg.store_gamelog(conn, _games(season_type="unspecified"), file_sha256="u" * 64, as_of_utc=T2)
    assert {g.season_type for g in ctg.load_team_games(conn)} == {"regular"}


def test_a_capture_without_venue_keeps_the_known_venue(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games(), file_sha256="a" * 64, as_of_utc=T1)
    ctg.store_gamelog(conn, _games(venue=None), file_sha256="b" * 64, as_of_utc=T2)
    assert None not in {g.venue for g in ctg.load_team_games(conn)}


def test_importing_the_same_gamelog_capture_twice_is_a_no_op(tmp_path) -> None:
    conn = _store(tmp_path)
    first = ctg.import_gamelog(conn, _games(), file_sha256="a" * 64, as_of_utc=T1)
    # a second connection racing the first sees the capture and writes nothing
    other = sqlite3.connect(tmp_path / "ctg.sqlite3")
    again = ctg.import_gamelog(other, _games(), file_sha256="a" * 64, as_of_utc=T1)
    assert first.inserted and not again.inserted and again.capture_id == first.capture_id
    assert conn.execute("SELECT COUNT(*) FROM ctg_game_version").fetchone()[0] == 4


# --------------------------------------------------------------------------- migration

V1_SCHEMA = """
CREATE TABLE ctg_import (
    import_id INTEGER PRIMARY KEY, file_sha256 TEXT NOT NULL UNIQUE, table_name TEXT NOT NULL,
    season TEXT NOT NULL, season_type TEXT NOT NULL DEFAULT 'regular', team TEXT,
    source_path TEXT, as_of_utc TEXT NOT NULL, imported_utc TEXT NOT NULL, n_rows INTEGER NOT NULL
);
CREATE TABLE ctg_team_metric (
    import_id INTEGER NOT NULL, team TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
    PRIMARY KEY (import_id, team, metric)
);
CREATE TABLE ctg_team_game (
    season TEXT NOT NULL, season_type TEXT NOT NULL, game_date TEXT NOT NULL, team TEXT NOT NULL,
    opp TEXT NOT NULL, venue TEXT, team_pts INTEGER NOT NULL, opp_pts INTEGER NOT NULL,
    off_pp100 REAL, def_pp100 REAL, metrics_json TEXT NOT NULL, file_sha256 TEXT NOT NULL,
    PRIMARY KEY (season, game_date, team)
);
INSERT INTO ctg_import VALUES
    (1, 'aaaa', 'gamelog', '2026-27', 'regular', 'BOS', 'TEAMS/BOS/x.json',
     '2026-11-03T14:00:00+00:00', '2026-11-08T00:00:00+00:00', 1),
    (2, 'bbbb', 'fourfactors', '2026-27', 'regular', NULL, NULL,
     '2026-11-03T14:00:00+00:00', '2026-11-08T00:00:00+00:00', 1);
INSERT INTO ctg_team_metric VALUES (2, 'BOS', 'off_pts_poss', 114.0);
INSERT INTO ctg_team_game VALUES
    ('2026-27', 'regular', '2026-11-01', 'BOS', 'LAL', 'home', 125, 112, 124.0, 110.0, '{}',
     'aaaa'),
    ('2026-27', 'regular', '2026-11-02', 'BOS', 'NYK', 'away', 100, 101, 101.0, 102.0, '{}',
     'orphan');
"""


def test_a_v1_store_is_migrated_with_its_timestamps(tmp_path) -> None:
    """P2: rows written before per-row as-of existed must not vanish from historical reads."""
    conn = sqlite3.connect(tmp_path / "old.sqlite3")
    conn.executescript(V1_SCHEMA)
    ctg.ensure_schema(conn)
    ctg.ensure_schema(conn)  # idempotent
    assert conn.execute("PRAGMA user_version").fetchone()[0] == ctg.SCHEMA_VERSION
    games = ctg.load_team_games(conn, as_of_before=T1 + timedelta(seconds=1))
    assert [(g.game_date.isoformat(), g.opp) for g in games] == [("2026-11-01", "LAL")]
    # a row with no import record has an unknown capture time: visible only to "latest" reads
    assert len(ctg.load_team_games(conn)) == 2
    snap = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-04")
    assert snap is not None and snap.teams["BOS"]["off_pts_poss"] == pytest.approx(114.0)
    # the next capture id continues after the migrated ones
    nxt = ctg.import_gamelog(conn, _games(), file_sha256="c" * 64, as_of_utc=T2)
    assert nxt.capture_id > 2


def test_default_store_honours_the_cache_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUTLIER_NBA_CACHE", str(tmp_path / "cache"))
    assert ctg.default_store() == tmp_path / "cache" / "ctg" / "ctg.sqlite3"
