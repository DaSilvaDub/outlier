"""#231 part c: the archive writer records files and never modifies them.

Bundles come from a real fixture pipeline run (``runs/<run_id>/raw/*.json`` plus
``manifest.json``, and the dated ``nfl_run_pointer_<date>.json``). nflverse sidecars
are written with ``boxscore_nflverse._write_meta``, the #252 writer, so their shape
is the real one (``url, fetched_at, sha256, size, rows``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from outlier_nfl import archive as ar
from outlier_nfl.boxscore_nflverse import CacheMeta, _write_meta
from outlier_nfl.pipeline import NflPipeline
from scripts import nfl_archive

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-09-13"


def unarchived(nfl: Path) -> None:
    """Drop the archive the pipeline wrote at commit, leaving a bundle as it was before
    the pipeline recorded its own runs (what the manual ``record`` catch-up sees)."""
    shutil.rmtree(nfl / "archive", ignore_errors=True)


def _tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file() and "archive" not in p.parts}


@pytest.fixture
def run(tmp_path):
    summary = NflPipeline(data_dir=tmp_path).run(
        date=SLATE, offline_fixtures_dir=FIXTURES_DIR, reports_dir=tmp_path / "reports")
    unarchived(tmp_path / "NFL")
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


def _refresh(cache: Path, name: str, data: bytes, fetched_at: str) -> None:
    """What a #252 refresh does: new bytes and sidecar at the same cache path."""
    (cache / name).write_bytes(data)
    _write_meta(cache / name, CacheMeta(url=f"https://x/{name}", fetched_at=fetched_at,
                                        sha256=hashlib.sha256(data).hexdigest(),
                                        size=len(data), rows=data.count(b"\n") - 1))


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
    # Copies live in the archive, keyed by the CSV's sha256; the cache is untouched.
    assert all(e.path.startswith("archive/nflverse/") for e in result.recorded)
    for e in result.recorded:
        assert ar.sha256_file(nfl / e.path) == e.sha256
        assert Path(e.path).parent.name in {e2.sha256 for e2 in result.recorded}
    assert _tree(cache) == before


def test_cache_refresh_keeps_the_first_archived_copy(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    first = ar.record_nflverse(nfl, cache, [2025]).recorded
    _refresh(cache, "stats_player_week_2025.csv", b"player_id,week\nP1,1\nP2,1\n",
             "2026-01-13T12:00:00+00:00")
    second = ar.record_nflverse(nfl, cache, [2025])
    assert not second.conflicts
    stats = [e for e in ar.read_manifest(ar.manifest_path(nfl))[0]
             if Path(e.path).name == "stats_player_week_2025.csv"]
    assert len(stats) == 2 and len({e.path for e in stats}) == 2
    assert [e.fetched_at for e in stats] == ["2026-01-06T12:00:00+00:00",
                                            "2026-01-13T12:00:00+00:00"]
    for e in first:  # the first copies still hash to their entries
        assert ar.sha256_file(nfl / e.path) == e.sha256
    assert ar.check_archive(nfl, seasons=[2025]).gaps == []


def test_cache_changing_mid_copy_cannot_reach_the_archive(tmp_path, monkeypatch):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    original = (cache / "games.csv").read_bytes()
    real = ar._write_once

    def refresh_then_write(dest: Path, data: bytes) -> bool:
        # A refresh of games.csv lands between our read of it and our write.
        if dest.name == "games.csv":
            _refresh(cache, "games.csv", b"player_id,week\nZZ,9\n", "2026-01-07T00:00:00+00:00")
        return real(dest, data)


    monkeypatch.setattr(ar, "_write_once", refresh_then_write)
    result = ar.record_nflverse(nfl, cache, [2025])
    [games] = [e for e in result.recorded if Path(e.path).name == "games.csv"]
    assert (nfl / games.path).read_bytes() == original
    assert games.sha256 == hashlib.sha256(original).hexdigest() == Path(games.path).parent.name


def test_archive_copy_with_other_bytes_is_refused_and_kept(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    sha = hashlib.sha256((cache / "games.csv").read_bytes()).hexdigest()
    squatter = nfl / "archive" / "nflverse" / sha / "games.csv"
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"not it")
    result = ar.record_nflverse(nfl, cache, [2025])
    assert any("already exists with different bytes" in c for c in result.conflicts)
    assert squatter.read_bytes() == b"not it"
    assert not list(squatter.parent.glob(".*.tmp"))


def test_nflverse_csv_that_fails_its_sidecar_is_refused(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    (cache / "games.csv").write_bytes(b"player_id,week\nP1,2\n")
    result = ar.record_nflverse(nfl, cache, [2025])
    assert any("do not match its sidecar" in c for c in result.conflicts)
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


def test_new_sidecar_next_to_the_old_csv_is_refused(tmp_path):
    """#236 order: the sidecar is written first, carrying previous_sha256 of the old CSV."""
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    old = (cache / "games.csv").read_bytes()
    new = b"player_id,week\nP9,2\n"
    (cache / "games.csv.meta.json").write_text(json.dumps({
        "url": "https://x/games.csv", "fetched_at": "2026-01-08T00:00:00+00:00",
        "sha256": hashlib.sha256(new).hexdigest(), "size": len(new), "rows": 1,
        "previous_sha256": hashlib.sha256(old).hexdigest()}), encoding="utf-8")
    result = ar.record_nflverse(nfl, cache, [2025])
    [msg] = [c for c in result.conflicts if "games.csv" in c]
    assert "refresh is in progress" in msg and "retry" in msg
    assert all(not e.path.endswith(("games.csv", "games.csv.meta.json")) for e in result.recorded)


def test_archived_sidecar_is_the_exact_bytes_that_were_checked(tmp_path, monkeypatch):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path)
    side = cache / "stats_player_week_2025.csv.meta.json"
    checked = side.read_bytes()
    real = ar._write_once

    def rewrite_sidecar_first(dest: Path, data: bytes) -> bool:
        side.write_bytes(checked.replace(b"2026-01-06", b"2099-01-01"))
        return real(dest, data)

    monkeypatch.setattr(ar, "_write_once", rewrite_sidecar_first)
    result = ar.record_nflverse(nfl, cache, [2025])
    [entry] = [e for e in result.recorded if e.path.endswith("stats_player_week_2025.csv.meta.json")]
    assert (nfl / entry.path).read_bytes() == checked


def test_record_run_id_keys_by_the_pointer_slate_name(tmp_path):
    summary = NflPipeline(data_dir=tmp_path).run(
        date=SLATE, window="early", offline_fixtures_dir=FIXTURES_DIR,
        reports_dir=tmp_path / "reports")
    nfl = tmp_path / "NFL"
    pointers = ar.pointed_runs(nfl)
    [(suffix, run_id)] = pointers.items()
    assert run_id == summary["run_id"] and suffix != SLATE  # e.g. 2026-09-13_early
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl), "--run-id", run_id]) == 0
    entries, _ = ar.read_manifest(ar.manifest_path(nfl))
    assert {e.slate for e in entries} == {suffix}
    assert ar.check_archive(nfl).gaps == []


def test_manifest_not_matching_the_pointer_hash_is_refused(run):
    nfl, run_id = run
    target = nfl / "runs" / run_id / "manifest.json"
    body = json.loads(target.read_text("utf-8"))
    body["status"] = "EDITED"
    target.write_text(json.dumps(body), encoding="utf-8")
    result = ar.record_run(nfl, run_id)
    assert not result.recorded and any("manifest_sha256" in c for c in result.conflicts)
    assert not ar.manifest_path(nfl).exists()
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl)]) == 1
