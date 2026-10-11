"""#231 part c: the archive completeness checker (read-only, non-zero on gaps).

Uses the same real fixture-run bundle and #252 sidecar shapes as
``test_nfl_archive_writer``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outlier_nfl import archive as ar
from scripts import nfl_archive
from tests.test_nfl_archive_writer import SLATE, _cache, _tree, run  # noqa: F401


def _kinds(report: ar.CheckReport) -> set[tuple[str | None, str]]:
    return {(g.slate, g.kind) for g in report.gaps}


def test_complete_archive_passes_and_reads_only(run, tmp_path):  # noqa: F811
    nfl, _ = run
    cache = _cache(tmp_path)
    ar.record_pointed_runs(nfl)
    ar.record_nflverse(nfl, cache, [2025])
    before = {**_tree(nfl), **{f"c/{k}": v for k, v in _tree(cache).items()}}
    mbytes = ar.manifest_path(nfl).read_bytes()
    report = ar.check_archive(nfl, seasons=[2025])
    assert report.gaps == [] and report.complete_slates == [SLATE] and report.entries == 8
    assert {**_tree(nfl), **{f"c/{k}": v for k, v in _tree(cache).items()}} == before
    assert ar.manifest_path(nfl).read_bytes() == mbytes


def test_no_manifest_reports_every_role_of_every_pointed_slate(run):  # noqa: F811
    nfl, _ = run
    report = ar.check_archive(nfl)
    assert {(g.role, g.kind) for g in report.gaps} == {
        (r, "missing_role") for r in ar.REQUIRED_SLATE_ROLES}
    assert not ar.manifest_path(nfl).exists()  # the checker never creates it


def test_expected_slate_without_a_pointer_is_a_gap(run):  # noqa: F811
    nfl, _ = run
    ar.record_pointed_runs(nfl)
    report = ar.check_archive(nfl, slates=["2026-09-20"])
    assert _kinds(report) == {("2026-09-20", "missing_role")}
    assert report.complete_slates == [SLATE]


def test_missing_role_sha_and_size_mismatch_and_deleted_file(run):  # noqa: F811
    nfl, run_id = run
    ar.record_pointed_runs(nfl)
    raw = nfl / "runs" / run_id / "raw"
    props = raw / "player_props.json"
    data = props.read_bytes()
    props.write_bytes(data.replace(b"-110", b"-105", 1))  # same size, different bytes
    (raw / "event_markets.json").write_bytes(b"{}")
    (raw / "schedule.json").unlink()
    kinds = {(g.role, g.kind) for g in ar.check_archive(nfl).gaps}
    assert {("pregame_player_props", "sha_mismatch"),
            ("pregame_event_markets", "size_mismatch"),
            ("pregame_schedule", "file_missing")} <= kinds


def test_rerun_moving_the_pointer_is_flagged_until_archived(run, tmp_path):  # noqa: F811
    nfl, run_id = run
    ar.record_pointed_runs(nfl)
    pointer = nfl / "normalized" / f"nfl_run_pointer_{SLATE}.json"
    body = json.loads(pointer.read_text("utf-8"))
    body["run_id"] = "LATER-RUN"
    pointer.write_text(json.dumps(body), encoding="utf-8")
    assert (SLATE, "pointer_run_not_archived") in _kinds(ar.check_archive(nfl))


def test_missing_nflverse_season_is_a_gap(run, tmp_path):  # noqa: F811
    nfl, _ = run
    ar.record_pointed_runs(nfl)
    ar.record_nflverse(nfl, _cache(tmp_path), [2025])
    gaps = ar.check_archive(nfl, seasons=[2024, 2025]).gaps
    assert {g.detail for g in gaps} == {
        "season 2024: stats_player_week_2024.csv not archived",
        "season 2024: stats_player_week_2024.csv.meta.json not archived"}


@pytest.mark.parametrize("mode", ["clean", "gap", "invalid"])
def test_cli_exit_codes(run, capsys, mode):  # noqa: F811
    nfl, run_id = run
    ar.record_pointed_runs(nfl)
    if mode == "gap":
        (nfl / "runs" / run_id / "raw" / "schedule.json").unlink()
    if mode == "invalid":
        with ar.manifest_path(nfl).open("a", encoding="utf-8") as fh:
            fh.write("{oops\n")
    code = nfl_archive.main(["check", "--nfl-dir", str(nfl)])
    out = capsys.readouterr().out
    assert code == {"clean": 0, "gap": 1, "invalid": 2}[mode]
    assert out.strip().splitlines()[-1].startswith("slates 1, complete ")
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl), "--json"]) == code
    assert json.loads(capsys.readouterr().out)["slates"] == [SLATE]


def test_checker_source_has_no_write_calls():
    import inspect
    src = inspect.getsource(ar.check_archive) + inspect.getsource(nfl_archive._check)
    for call in ("write_", "open(", "unlink", "mkdir", "replace(", "record_"):
        assert call not in src, call
    assert Path(nfl_archive.__file__).exists()
