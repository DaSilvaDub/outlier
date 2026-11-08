"""CTG local store: idempotent imports, strict as-of snapshot reads, game-log rows."""

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


def _store(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "ctg.sqlite3")
    ctg.ensure_schema(conn)
    return conn


def test_import_is_idempotent_by_file_hash(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    first = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    again = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T2)
    assert first.inserted and not again.inserted
    assert conn.execute("SELECT COUNT(*) FROM ctg_import").fetchone()[0] == 1


def test_snapshot_asof_is_strictly_before_the_slate(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    # a snapshot taken on the slate's own Eastern date is not usable for that slate
    assert ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-03") is None
    snap = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-04")
    assert snap is not None and snap.as_of_utc == T1
    assert snap.teams["BOS"]["off_pts_poss"] == pytest.approx(114.0)


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


def test_store_gamelog_replaces_by_game_key(tmp_path) -> None:
    conn = _store(tmp_path)
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    ctg.store_gamelog(conn, games, file_sha256="a" * 64)
    ctg.store_gamelog(conn, games, file_sha256="b" * 64)  # a later, fuller download
    assert conn.execute("SELECT COUNT(*) FROM ctg_team_game").fetchone()[0] == 4
    row = conn.execute(
        "SELECT opp, venue, off_pp100, file_sha256 FROM ctg_team_game WHERE game_date = ?",
        ("2026-10-27",),
    ).fetchone()
    assert row == ("NYK", "home", pytest.approx(120.4), "b" * 64)


def test_load_team_games_round_trips(tmp_path) -> None:
    conn = _store(tmp_path)
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    ctg.store_gamelog(conn, games, file_sha256="a" * 64)
    loaded = ctg.load_team_games(conn, season="2026-27")
    assert [(g.game_date, g.opp, g.off_pp100) for g in loaded] == [
        (g.game_date, g.opp, g.off_pp100) for g in games
    ]
    assert loaded[0].metrics["spread"] == pytest.approx(-4.0)
    assert ctg.load_team_games(conn, season="2025-26") == []


def _games_with_off(value: float):
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    return [dataclasses.replace(g, off_pp100=value) for g in games]


def test_an_older_file_never_overwrites_a_newer_one(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games_with_off(130.0), file_sha256="n" * 64, as_of_utc=T2)
    ctg.store_gamelog(conn, _games_with_off(90.0), file_sha256="o" * 64, as_of_utc=T1)
    offs = {r[0] for r in conn.execute("SELECT off_pp100 FROM ctg_team_game")}
    assert offs == {130.0}


def test_playoffs_label_is_never_downgraded(tmp_path) -> None:
    conn = _store(tmp_path)
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    playoff = [dataclasses.replace(g, season_type="playoffs") for g in games]
    ctg.store_gamelog(conn, playoff, file_sha256="p" * 64, as_of_utc=T1)
    ctg.store_gamelog(conn, games, file_sha256="r" * 64, as_of_utc=T2)  # mislabeled "regular"
    labels = {r[0] for r in conn.execute("SELECT season_type FROM ctg_team_game")}
    assert labels == {"playoffs"}


def test_load_team_games_honours_the_as_of_cutoff(tmp_path) -> None:
    conn = _store(tmp_path)
    ctg.store_gamelog(conn, _games_with_off(120.0), file_sha256="a" * 64, as_of_utc=T2)
    assert ctg.load_team_games(conn, as_of_before=T1) == []
    assert len(ctg.load_team_games(conn, as_of_before=T2 + timedelta(seconds=1))) == 4


def test_old_store_gains_the_as_of_column(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "old.sqlite3")
    conn.executescript(ctg.SCHEMA.replace("    as_of_utc    TEXT,", ""))
    ctg.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ctg_team_game)")}
    assert "as_of_utc" in cols


def test_default_store_honours_the_cache_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUTLIER_NBA_CACHE", str(tmp_path / "cache"))
    assert ctg.default_store() == tmp_path / "cache" / "ctg" / "ctg.sqlite3"
