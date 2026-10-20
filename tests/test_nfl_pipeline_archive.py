"""Phase 1: every committed pipeline run is recorded in the archive, or the run fails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import archive as ar
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"
LATER = "2026-10-04T16:00:00+00:00"


def _run(tmp_path: Path, client: Any, clock: str) -> dict[str, Any]:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    return NflPipeline(client=client, data_dir=tmp_path, clock=lambda: now).run(
        date=SLATE, as_of_utc=clock, reports_dir=tmp_path / "reports")


def _entries(tmp_path: Path, run_id: str) -> dict[str, ar.ArchiveEntry]:
    entries, problems = ar.read_manifest(ar.manifest_path(tmp_path / "NFL"))
    assert problems == []
    return {e.role: e for e in entries if e.run_id == run_id}


def _assert_archived(tmp_path: Path, run: dict[str, Any]) -> None:
    got = _entries(tmp_path, run["run_id"])
    assert set(ar.PREGAME_ROLES) <= set(got), sorted(got)
    archived = ar.resolve_path(tmp_path / "NFL", got["run_manifest"].path)
    manifest = json.loads(archived.read_text(encoding="utf-8"))
    assert manifest["run_id"] == run["run_id"]
    assert manifest["status"] == run["status"]


def test_published_run_is_archived(tmp_path: Path) -> None:
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    assert run["status"] == "OK" and run["publication"] == "published"
    _assert_archived(tmp_path, run)


def test_refused_run_is_archived_with_its_status(tmp_path: Path) -> None:
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    assert run["status"] == "FAILED" and run["publication"] == "bundle_only"
    _assert_archived(tmp_path, run)


def test_record_failure_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(nfl_dir: Any, run_id: str, slate: str | None = None) -> ar.RecordResult:
        return ar.RecordResult([], [], [f"{run_id}/manifest.json: simulated conflict"], [])

    monkeypatch.setattr(nfl_pipeline, "record_run", broken)
    with pytest.raises(nfl_pipeline.RunArchiveError, match="not archived"):
        _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)


def test_record_exception_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> ar.RecordResult:
        raise OSError("disk full")

    monkeypatch.setattr(nfl_pipeline, "record_run", boom)
    with pytest.raises(nfl_pipeline.RunArchiveError, match="disk full"):
        _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
