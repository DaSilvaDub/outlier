"""#231 part c: the archive writer records files and never modifies them.

Bundles come from a real fixture pipeline run (``runs/<run_id>/raw/*.json`` plus
``manifest.json``, and the dated ``nfl_run_pointer_<date>.json``). nflverse sidecars
are written with ``boxscore_nflverse._write_meta``, the #252 writer, so their shape
is the real one (``url, fetched_at, sha256, size, rows``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from outlier_nfl import archive as ar
from outlier_nfl.boxscore_nflverse import CacheMeta, _write_meta
from outlier_nfl.pipeline import NflPipeline
from scripts import nfl_archive

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-09-13"


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file() and "archive" not in p.parts}


@pytest.fixture
def run(tmp_path):
    summary = NflPipeline(data_dir=tmp_path).run(
        date=SLATE, offline_fixtures_dir=FIXTURES_DIR, reports_dir=tmp_path / "reports")
    return tmp_path / "NFL", summary["run_id"]


def _cache(tmp_path: Path, season: int = 2025) -> Path:
    cache = tmp_path / "nflverse"
    cache.mkdir()
    for name in (f"stats_player_week_{season}.csv", "games.csv"):
        data = b"player_id,week\nP1,1\n"
        (cache / name).write_bytes(data)
        _write_meta(cache / name, CacheMeta(url=f"https://x/{name}",
                                            fetched_at="2026-01-06T12:00:00+00:00",
                                            sha256=hashlib.sha256(data).hexdigest(),
                                            size=len(data), rows=1))
    return cache


def test_records_every_pregame_role_without_touching_the_bundle(run):
    nfl, run_id = run
    before = _tree(nfl)
    result = ar.record_pointed_runs(nfl)
    assert not result.conflicts and not result.missing
    assert sorted(e.role for e in result.recorded) == sorted(ar.REQUIRED_SLATE_ROLES)
    assert all(e.slate == SLATE and e.run_id == run_id and e.season == 2026
               for e in result.recorded)
    assert all(not Path(e.path).is_absolute() for e in result.recorded)
    assert _tree(nfl) == before  # bytes and mtimes of every original unchanged
    entries, problems = ar.read_manifest(ar.manifest_path(nfl))
    assert not problems and entries == result.recorded


def test_recording_twice_appends_nothing(run):
    nfl, _ = run
    ar.record_pointed_runs(nfl)
    first = ar.manifest_path(nfl).read_bytes()
    again = ar.record_pointed_runs(nfl)
    assert not again.recorded and len(again.already) == 4
    assert ar.manifest_path(nfl).read_bytes() == first


def test_changed_file_is_refused_and_the_entry_kept(run):
    nfl, run_id = run
    ar.record_pointed_runs(nfl)
    manifest = ar.manifest_path(nfl).read_bytes()
    target = nfl / "runs" / run_id / "manifest.json"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ar.ArchiveConflict, match="already archived"):
        ar.record_file(nfl, target, role="run_manifest", slate=SLATE, source="run_writer",
                       season=2026, captured_at="2026-09-13T12:00:00+00:00", run_id=run_id)
    assert ar.manifest_path(nfl).read_bytes() == manifest


def test_raw_file_edited_after_the_run_is_never_archived(run):
    nfl, run_id = run
    props = nfl / "runs" / run_id / "raw" / "player_props.json"
    props.write_bytes(props.read_bytes().replace(b"-110", b"-105", 1))
    result = ar.record_run(nfl, run_id, SLATE)
    assert any("differ from the run manifest" in c for c in result.conflicts)
    assert "pregame_player_props" not in {e.role for e in result.recorded}


def test_invalid_manifest_blocks_recording_and_is_left_alone(run):
    nfl, _ = run
    mpath = ar.manifest_path(nfl)
    mpath.parent.mkdir(parents=True)
    mpath.write_text("{broken\n", encoding="utf-8")
    result = ar.record_pointed_runs(nfl)
    assert not result.recorded and result.missing
    assert mpath.read_text(encoding="utf-8") == "{broken\n"


def test_nflverse_inputs_get_their_own_role_with_fetched_at(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    before = _tree(cache)
    result = ar.record_nflverse(nfl, cache, [2025])
    assert not result.conflicts and not result.missing
    names = sorted(Path(e.path).name for e in result.recorded)
    assert names == ["games.csv", "games.csv.meta.json", "stats_player_week_2025.csv",
                     "stats_player_week_2025.csv.meta.json"]
    assert {(e.role, e.slate, e.fetched_at, e.source) for e in result.recorded} == {
        ("nflverse_boxscore", None, "2026-01-06T12:00:00+00:00", "nflverse")}
    assert all(Path(e.path).is_absolute() for e in result.recorded)  # outside nfl_dir
    assert _tree(cache) == before


def test_nflverse_csv_that_fails_its_sidecar_is_refused(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    (cache / "games.csv").write_bytes(b"player_id,week\nP1,2\n")
    result = ar.record_nflverse(nfl, cache, [2025])
    assert any("differ from its sidecar" in c for c in result.conflicts)
    assert all(not e.path.endswith("games.csv") for e in result.recorded)


def test_missing_nflverse_season_is_reported(tmp_path):
    result = ar.record_nflverse(tmp_path / "NFL", _cache(tmp_path), [2024])
    assert any("stats_player_week_2024.csv" in m for m in result.missing)


def test_cli_record_exit_codes(run, capsys):
    nfl, run_id = run
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl)]) == 0
    assert "recorded 4" in capsys.readouterr().out
    target = nfl / "runs" / run_id / "manifest.json"
    target.write_bytes(target.read_bytes() + b" ")
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl)]) == 1
    assert "CONFLICT" in capsys.readouterr().out
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl), "--nflverse-cache", "x"]) == 2
