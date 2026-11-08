"""Read-only sync of CTG game logs from the NBA Drive hub into the local store."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from outlier_nba import cli
from outlier_nba.ingest import ctg

FIX = Path(__file__).parent / "fixtures" / "ctg"


def _place(root: Path, rel: str, fixture: str) -> Path:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIX / fixture, dest)
    return dest


def _hub(tmp_path: Path) -> Path:
    """A fake NBA_ROOT laid out exactly like the real Drive folder."""
    root = tmp_path / "NBA_Active"
    _place(
        root, "TEAMS/BOS/CLEANINGdaGLASS/GAME_LOGS_11_02_2026.json", "gamelog_old_synthetic.json"
    )
    _place(
        root,
        "Current_Matchups/CLEANING_THE_GLASS/BOS/CLEANINGdaGLASS/GAME_LOGS_11_05_2026.json",
        "gamelog_new_synthetic.json",
    )
    _place(
        root,
        "Current_Matchups/CLEANING_THE_GLASS/BOS/CLEANINGdaGLASS/GAME_LOGS_11_06_2026.json",
        "gamelog_mislabeled.json",
    )
    _place(
        root,
        "Current_Matchups/CLEANING_THE_GLASS/BOS/CLEANINGdaGLASS/GAME_LOGS_11_07_2026.json",
        "gamelog_empty.json",
    )
    (root / "cache" / "ctg").mkdir(parents=True)
    (root / "cache" / "ctg" / "ctg_cookies.json").write_text("{}", encoding="utf-8")
    return root


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "ctg.sqlite3")
    ctg.ensure_schema(conn)
    return conn


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_hub_folder_names_match_the_drive_layout() -> None:
    parts = {part for glob in ctg.GAMELOG_GLOBS for part in glob.split("/") if "*" not in part}
    assert parts == {"TEAMS", "CLEANINGdaGLASS", "Current_Matchups", "CLEANING_THE_GLASS"}


def test_nba_root_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NBA_ROOT", str(tmp_path))
    assert ctg.nba_root() == tmp_path
    monkeypatch.delenv("NBA_ROOT")
    assert ctg.nba_root() == ctg.DEFAULT_NBA_ROOT


def test_discover_finds_team_and_file_date_oldest_first(tmp_path) -> None:
    found = ctg.discover_gamelogs(_hub(tmp_path))
    assert [(f.claimed_team, f.file_date.isoformat()) for f in found] == [
        ("BOS", "2026-11-02"),
        ("BOS", "2026-11-05"),
        ("BOS", "2026-11-06"),
        ("BOS", "2026-11-07"),
    ]
    assert not any("cache" in f.path.parts for f in found)


def test_sync_imports_rejects_and_skips(tmp_path) -> None:
    root = _hub(tmp_path)
    before = _snapshot(root)
    conn = _conn(tmp_path)
    report = ctg.sync_from_hub(conn, root)
    outcomes = {f.path.name: f.outcome for f in report.files}
    assert outcomes == {
        "GAME_LOGS_11_02_2026.json": "imported",
        "GAME_LOGS_11_05_2026.json": "imported",
        "GAME_LOGS_11_06_2026.json": "rejected_mislabeled",
        "GAME_LOGS_11_07_2026.json": "skipped_unusable",
    }
    # the shared games are stored once; the mislabeled file's 11/02 row never lands
    assert conn.execute("SELECT COUNT(*) FROM ctg_team_game").fetchone()[0] == 4
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM ctg_team_game WHERE game_date = '2026-11-02'"
        ).fetchone()[0]
        == 0
    )
    # the newer file wins the row and supplies the regular-season label
    assert conn.execute("SELECT DISTINCT season_type FROM ctg_team_game").fetchall() == [
        ("regular",)
    ]
    assert report.counts == {
        "imported": 2,
        "already_imported": 0,
        "rejected_mislabeled": 1,
        "skipped_unusable": 1,
        "other_season": 0,
    }
    assert _snapshot(root) == before  # the hub is never written


def test_a_bad_file_never_stops_the_files_after_it(tmp_path) -> None:
    root = _hub(tmp_path)
    doc = json.loads((FIX / "gamelog_new_synthetic.json").read_text(encoding="utf-8"))
    rows = doc["season_views"]["regular_season"]["four_factors"]
    del rows[2]["Opp"]  # a later row missing a required key used to raise a bare KeyError
    rows[1]["Team Pts"] = "inf"  # int(inf) used to raise OverflowError
    bad = root / "TEAMS/BOS/CLEANINGdaGLASS/GAME_LOGS_11_01_2026.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    report = ctg.sync_from_hub(_conn(tmp_path), root)
    outcomes = {f.path.name: f.outcome for f in report.files}
    assert outcomes["GAME_LOGS_11_01_2026.json"] == "skipped_unusable"
    assert outcomes["GAME_LOGS_11_05_2026.json"] == "imported"


def test_non_object_views_and_rows_are_unusable() -> None:
    for doc in ({"season_views": {"regular_season": []}}, {"four_factors": ["x"]}):
        with pytest.raises(ctg.CTGUnusableError):
            ctg.parse_gamelog(json.dumps(doc).encode(), team="BOS", name="x.json")


def test_files_under_a_non_team_folder_are_reported(tmp_path) -> None:
    root = _hub(tmp_path)
    _place(
        root,
        "Current_Matchups/CLEANING_THE_GLASS/NYK @ SAS/CLEANINGdaGLASS/GAME_LOGS_11_08_2026.json",
        "gamelog_new_synthetic.json",
    )
    report = ctg.sync_from_hub(_conn(tmp_path), root)
    odd = [f for f in report.files if f.path.name == "GAME_LOGS_11_08_2026.json"]
    assert [(f.claimed_team, f.outcome) for f in odd] == [(None, "skipped_unusable")]
    assert "not a team" in odd[0].detail


def test_import_record_and_rows_commit_together(tmp_path, monkeypatch) -> None:
    root = _hub(tmp_path)
    conn = _conn(tmp_path)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ctg, "_upsert_games", boom)
    with pytest.raises(sqlite3.OperationalError):
        ctg.sync_from_hub(conn, root)
    # nothing half-recorded: a rerun must import the file, not call it already_imported
    assert conn.execute("SELECT COUNT(*) FROM ctg_import").fetchone()[0] == 0
    monkeypatch.undo()
    assert ctg.sync_from_hub(conn, root).counts["imported"] == 2


def test_ctg_sync_command_refuses_a_store_inside_the_hub(tmp_path, capsys) -> None:
    root = _hub(tmp_path)
    before = _snapshot(root)
    code = cli.main(["ctg-sync", "--nba-root", str(root), "--store", str(root / "x.sqlite3")])
    assert code == 2
    assert "read-only" in capsys.readouterr().out
    assert _snapshot(root) == before


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_discovery_never_leaves_the_hub(tmp_path) -> None:
    root = _hub(tmp_path)
    outside = tmp_path / "outside" / "CLEANINGdaGLASS"
    outside.mkdir(parents=True)
    shutil.copyfile(FIX / "gamelog_new_synthetic.json", outside / "GAME_LOGS_11_09_2026.json")
    try:
        (root / "TEAMS" / "NYK").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this machine")
    names = {f.path.name for f in ctg.discover_gamelogs(root)}
    assert "GAME_LOGS_11_09_2026.json" not in names


def test_second_sync_is_a_no_op(tmp_path) -> None:
    root = _hub(tmp_path)
    conn = _conn(tmp_path)
    ctg.sync_from_hub(conn, root)
    again = ctg.sync_from_hub(conn, root)
    assert again.counts["imported"] == 0
    assert again.counts["already_imported"] == 2


def test_season_filter_skips_other_seasons(tmp_path) -> None:
    root = _hub(tmp_path)
    conn = _conn(tmp_path)
    report = ctg.sync_from_hub(conn, root, season="2025-26")
    assert report.counts["imported"] == 0
    assert {f.outcome for f in report.files} >= {"other_season"}


def test_missing_root_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        ctg.sync_from_hub(_conn(tmp_path), tmp_path / "nope")


def test_ctg_sync_command(tmp_path, capsys) -> None:
    root = _hub(tmp_path)
    store = tmp_path / "store" / "ctg.sqlite3"
    code = cli.main(["ctg-sync", "--nba-root", str(root), "--store", str(store)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["imported"] == 2
    assert out["counts"]["rejected_mislabeled"] == 1
    assert out["store"] == str(store)


def test_ctg_sync_command_exits_five_without_the_drive(tmp_path, capsys) -> None:
    code = cli.main(
        ["ctg-sync", "--nba-root", str(tmp_path / "missing"), "--store", str(tmp_path / "s.db")]
    )
    assert code == 5
    assert "not found" in capsys.readouterr().out
