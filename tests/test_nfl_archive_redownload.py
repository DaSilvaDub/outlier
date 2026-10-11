"""#231 / #222 follow-up: an identical nflverse re-download is already archived.

Sidecar shape: ``boxscore_nflverse.CacheMeta`` as written by ``_write_meta``
(url, fetched_at, sha256, size, rows, optional previous_sha256).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from outlier_nfl import archive as ar
from outlier_nfl.boxscore_nflverse import CacheMeta, _write_meta
from scripts import nfl_archive

DATA = b"player_id,week\nP1,1\n"


def _write(cache: Path, name: str, *, fetched_at: str, url: str | None = None,
           previous: str | None = None, rows: int = 1) -> None:
    (cache / name).write_bytes(DATA)
    _write_meta(cache / name, CacheMeta(url=url or f"https://x/{name}", fetched_at=fetched_at,
                                        sha256=hashlib.sha256(DATA).hexdigest(),
                                        size=len(DATA), rows=rows, previous_sha256=previous))


def _cache(tmp_path: Path, **kw) -> Path:
    cache = tmp_path / "nflverse"
    cache.mkdir(exist_ok=True)
    for name in ("stats_player_week_2025.csv", "games.csv"):
        _write(cache, name, **kw)
    return cache


def test_ignored_sidecar_fields_are_exactly_the_download_fields():
    assert ar.SIDECAR_DOWNLOAD_FIELDS == frozenset({"fetched_at", "previous_sha256"})
    # Every other real sidecar field describes the file and is compared.
    real = {f.name for f in dataclasses.fields(CacheMeta)}
    assert real - ar.SIDECAR_DOWNLOAD_FIELDS == {"url", "sha256", "size", "rows"}


def test_identical_redownload_is_already_archived(tmp_path, capsys):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path, fetched_at="2026-01-06T12:00:00+00:00")
    first = ar.record_nflverse(nfl, cache, [2025]).recorded
    side_entry = next(e for e in first if e.path.endswith("stats_player_week_2025.csv.meta.json"))
    first_bytes = (nfl / side_entry.path).read_bytes()
    _cache(tmp_path, fetched_at="2026-01-13T12:00:00+00:00",
           previous=hashlib.sha256(DATA).hexdigest())
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl), "--nflverse-cache", str(cache),
                             "--season", "2025"]) == 0
    assert "recorded 0, already archived 4, conflicts 0" in capsys.readouterr().out
    assert (nfl / side_entry.path).read_bytes() == first_bytes
    assert ar.sha256_file(nfl / side_entry.path) == side_entry.sha256
    assert len(ar.read_manifest(ar.manifest_path(nfl))[0]) == 4
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--season", "2025"]) == 0


def test_file_describing_field_change_is_still_a_conflict(tmp_path):
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path, fetched_at="2026-01-06T12:00:00+00:00")
    ar.record_nflverse(nfl, cache, [2025])
    for change in ({"url": "https://mirror/games.csv"}, {"rows": 2}):
        _write(cache, "games.csv", fetched_at="2026-01-13T12:00:00+00:00", **change)
        result = ar.record_nflverse(nfl, cache, [2025])
        assert any("games.csv.meta.json" in c and "different bytes" in c
                   for c in result.conflicts), change


def test_unreadable_archived_sidecar_never_matches(tmp_path):
    bad = tmp_path / "x.meta.json"
    bad.write_bytes(b"{nope")
    assert not ar.same_sidecar_file(bad, json.dumps({"sha256": "a"}).encode())
    assert not ar.same_sidecar_file(tmp_path / "missing.json", b"{}")


def test_previous_sha256_on_one_side_only_is_ignored(tmp_path):
    base = {"url": "https://x/games.csv", "sha256": "a" * 64, "size": 3, "rows": 1}
    with_prev = {**base, "fetched_at": "2026-01-13T00:00:00+00:00", "previous_sha256": "b" * 64}
    without = {**base, "fetched_at": "2026-01-06T00:00:00+00:00"}
    for archived, new in ((without, with_prev), (with_prev, without)):
        dest = tmp_path / "games.csv.meta.json"
        dest.write_text(json.dumps(archived), encoding="utf-8")
        assert ar.same_sidecar_file(dest, json.dumps(new).encode())


def test_same_bytes_from_a_different_url_are_a_conflict(tmp_path):
    """#233 moved the games.csv URL once; the source is part of what the file is."""
    nfl = tmp_path / "NFL"
    cache = _cache(tmp_path, fetched_at="2026-01-06T12:00:00+00:00")
    ar.record_nflverse(nfl, cache, [2025])
    _write(cache, "games.csv", fetched_at="2026-01-13T12:00:00+00:00",
           url="https://github.com/nflverse/nfldata/raw/master/data/games.csv")
    result = ar.record_nflverse(nfl, cache, [2025])
    assert any("games.csv.meta.json" in c for c in result.conflicts)
