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


def _drop_raw_before_record(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Delete ``name`` from the committed bundle just before it is recorded."""
    real = nfl_pipeline.record_run

    def dropping(nfl_dir: Any, run_id: str, *a: Any, **k: Any) -> ar.RecordResult:
        (Path(nfl_dir) / "runs" / run_id / name).unlink()
        return real(nfl_dir, run_id, *a, **k)

    monkeypatch.setattr(nfl_pipeline, "record_run", dropping)


def _pointer(tmp_path: Path) -> Path:
    return tmp_path / "NFL" / "normalized" / f"nfl_run_pointer_{SLATE}.json"


def test_published_run_missing_a_raw_file_fails(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _drop_raw_before_record(monkeypatch, "raw/player_props.json")
    with pytest.raises(nfl_pipeline.RunArchiveError, match="raw/player_props.json"):
        _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    assert not _pointer(tmp_path).exists()


def test_refused_run_missing_a_raw_file_is_archived_with_the_list_and_reported_by_check(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _drop_raw_before_record(monkeypatch, "raw/player_props.json")
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    assert run["status"] == "FAILED"
    got = _entries(tmp_path, run["run_id"])
    entry = got["run_manifest"]
    assert entry.missing_raw == [f"{run['run_id']}/raw/player_props.json"]
    assert run["archive"]["missing"] == entry.missing_raw
    report = ar.check_archive(tmp_path / "NFL", [entry.slate])  # type: ignore[list-item]
    assert any(g.kind == "missing_raw" and g.slate == entry.slate for g in report.gaps)
    assert entry.slate not in report.complete_slates


def test_check_catches_a_tampered_archived_run_manifest(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import nfl_archive

    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    nfl = tmp_path / "NFL"
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl)]) == 0
    capsys.readouterr()
    archived = ar.resolve_path(nfl, _entries(tmp_path, run["run_id"])["run_manifest"].path)
    body = json.loads(archived.read_text(encoding="utf-8"))
    body["status"] = "OK" if body["status"] != "OK" else "FAILED"
    data = json.dumps(body, indent=2).encode("utf-8")
    original_size = archived.stat().st_size
    archived.write_bytes(data[:original_size].ljust(original_size, b" "))  # same size
    assert nfl_archive.main(["check", "--nfl-dir", str(nfl)]) == 1
    out = capsys.readouterr().out
    assert "sha_mismatch" in out and "manifest.json" in out


def test_pointer_names_a_recorded_run_and_does_not_move_when_record_fails(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    pointer = _pointer(tmp_path)
    before = pointer.read_bytes()
    assert json.loads(before)["run_id"] == first["run_id"]
    assert "run_manifest" in _entries(tmp_path, first["run_id"])

    seen: list[bool] = []
    real = nfl_pipeline.record_run

    def failing(nfl_dir: Any, run_id: str, *a: Any, **k: Any) -> ar.RecordResult:
        # When record runs, the pointer must not name this run yet.
        seen.append(json.loads(pointer.read_text(encoding="utf-8"))["run_id"] == run_id)
        real(nfl_dir, run_id, *a, **k)
        return ar.RecordResult([], [], [f"{run_id}/manifest.json: simulated conflict"], [])

    monkeypatch.setattr(nfl_pipeline, "record_run", failing)
    with pytest.raises(nfl_pipeline.RunArchiveError):
        _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    assert seen == [False]
    assert pointer.read_bytes() == before
    for p in (tmp_path / "NFL" / "normalized").glob("nfl_run_pointer_*.json"):
        assert "run_manifest" in _entries(tmp_path, json.loads(p.read_text())["run_id"])


def test_refused_run_logs_nothing_published_before_a_record_failure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    messages: list[str] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    def boom(*_a: Any, **_k: Any) -> ar.RecordResult:
        raise OSError("disk full")

    handler = _Keep(level=logging.ERROR)
    nfl_pipeline.logger.addHandler(handler)
    monkeypatch.setattr(nfl_pipeline, "record_run", boom)
    try:
        with pytest.raises(nfl_pipeline.RunArchiveError):
            _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    finally:
        nfl_pipeline.logger.removeHandler(handler)
    assert any("nothing published" in m for m in messages), messages



def test_refused_sibling_with_missing_raw_leaves_the_pointer_slate_complete(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Published and refused runs share 2026-10-04_early; only the pointer run blocks."""
    key = f"{SLATE}_early"
    published = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    assert published["publication"] == "published"
    nfl = tmp_path / "NFL"
    assert key in ar.check_archive(nfl).complete_slates
    _drop_raw_before_record(monkeypatch, "raw/player_props.json")
    refused = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    assert refused["publication"] == "bundle_only"
    slates = {r["run_id"]: {e.slate for e in _entries(tmp_path, r["run_id"]).values()}
              for r in (published, refused)}
    assert slates == {published["run_id"]: {key}, refused["run_id"]: {key}}
    assert _entries(tmp_path, refused["run_id"])["run_manifest"].missing_raw
    report = ar.check_archive(nfl)
    assert not any(g.kind == "missing_raw" for g in report.gaps)
    assert any(g.kind == "missing_raw" and g.slate == key and refused["run_id"] in g.detail
               for g in report.notes)
    assert key in report.complete_slates


def test_pointer_run_with_missing_raw_blocks_its_slate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"{SLATE}_early"
    published = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    _drop_raw_before_record(monkeypatch, "raw/player_props.json")
    refused = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    nfl = tmp_path / "NFL"
    pointer = nfl / "normalized" / f"nfl_run_pointer_{key}.json"
    body = json.loads(pointer.read_text(encoding="utf-8"))
    assert body["run_id"] == published["run_id"]
    body["run_id"] = refused["run_id"]  # the slate's pointer now names the refused run
    pointer.write_text(json.dumps(body), encoding="utf-8")
    report = ar.check_archive(nfl)
    assert any(g.kind == "missing_raw" and g.slate == key and refused["run_id"] in g.detail
               for g in report.gaps)
    assert key not in report.complete_slates

def test_failed_record_writes_no_new_published_copy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Readers open dated/_latest copies directly, so they must not move either."""
    _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    nfl = tmp_path / "NFL"
    watched = [p for p in sorted((nfl / "normalized").glob("*.json"))
               if not p.name.startswith("nfl_run_pointer_")]
    assert any(p.name.endswith(f"_{SLATE}.json") for p in watched)
    assert any(p.name.endswith("_latest.json") for p in watched)

    def snapshot() -> dict[str, bytes]:
        roots = [nfl / "normalized", nfl / "exports", tmp_path / "reports"]
        return {str(p): p.read_bytes() for r in roots if r.is_dir()
                for p in sorted(r.rglob("*")) if p.is_file()}

    before = snapshot()
    real = nfl_pipeline.record_run

    def failing(nfl_dir: Any, run_id: str, *a: Any, **k: Any) -> ar.RecordResult:
        real(nfl_dir, run_id, *a, **k)
        return ar.RecordResult([], [], [f"{run_id}/manifest.json: simulated conflict"], [])

    monkeypatch.setattr(nfl_pipeline, "record_run", failing)
    with pytest.raises(nfl_pipeline.RunArchiveError):
        _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), "2026-10-04T15:30:00+00:00")
    assert snapshot() == before


def _window_run(tmp_path: Path, client: Any, clock: str) -> dict[str, Any]:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    return NflPipeline(client=client, data_dir=tmp_path, clock=lambda: now).run(
        date=SLATE, window="Early", as_of_utc=clock, reports_dir=tmp_path / "reports")


def test_pipeline_passes_its_suffix_as_the_slate_name(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the context fallback disabled, the pipeline's suffix alone names the slate."""
    monkeypatch.setattr(ar, "context_slate_key", lambda ctx: ctx.get("slate_date"))
    run = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    assert run["publication"] == "bundle_only"
    assert {e.slate for e in _entries(tmp_path, run["run_id"]).values()} == {f"{SLATE}_early"}


def test_record_run_without_a_pointer_names_the_slate_from_the_run_context(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual catch-up (no pointer, no slate argument) builds <date>_<window>."""
    def unrecorded(nfl_dir: Any, run_id: str, *a: Any, **k: Any) -> ar.RecordResult:
        return ar.RecordResult([], [ar.parse_entry({
            "slate": SLATE, "role": "run_manifest", "path": "x", "sha256": "0" * 64,
            "size": 0, "captured_at": PREGAME, "source": "run_writer", "season": 2026,
            "run_id": run_id})], [], [])

    monkeypatch.setattr(nfl_pipeline, "record_run", unrecorded)
    run = _window_run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    assert _entries(tmp_path, run["run_id"]) == {}
    ar.record_run(tmp_path / "NFL", run["run_id"])
    assert {e.slate for e in _entries(tmp_path, run["run_id"]).values()} == {f"{SLATE}_early"}


def test_bundle_only_artifacts_are_listed_and_a_missing_raw_input_is_not(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _drop_raw_before_record(monkeypatch, "raw/player_props.json")
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True), LATER)
    nfl = tmp_path / "NFL"
    entry = _entries(tmp_path, run["run_id"])["run_manifest"]
    manifest = json.loads(ar.resolve_path(nfl, entry.path).read_text(encoding="utf-8"))
    skipped = sorted(a["name"] for a in manifest["artifacts"] if not a["published_to"])
    raw = set(ar.PREGAME_ROLES.values())
    assert "raw/player_props.json" in skipped
    assert entry.bundle_only == [n for n in skipped if n not in raw]
    assert entry.bundle_only  # a refused run publishes nothing, so there are some
    assert entry.missing_raw == [f"{run['run_id']}/raw/player_props.json"]


def test_published_run_entry_lists_only_its_unpublished_artifacts(tmp_path: Path) -> None:
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), PREGAME)
    entry = _entries(tmp_path, run["run_id"])["run_manifest"]
    manifest = json.loads(ar.resolve_path(tmp_path / "NFL", entry.path).read_text("utf-8"))
    raw = set(ar.PREGAME_ROLES.values())
    want = sorted(a["name"] for a in manifest["artifacts"]
                  if not a["published_to"] and a["name"] not in raw)
    assert (entry.bundle_only or []) == want
    assert entry.missing_raw is None


@pytest.mark.parametrize("window", [None, "Early", " 1PM "])
def test_context_slate_key_equals_the_pipeline_suffix(tmp_path: Path, window: str | None) -> None:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True, exist_ok=True)
    tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(PREGAME)
    run = NflPipeline(client=snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                      clock=lambda: now).run(date=SLATE, window=window, as_of_utc=PREGAME,
                                             reports_dir=tmp_path / "reports")
    suffix = run["run_id"].rsplit("-", 2)[0]
    manifest = json.loads((tmp_path / "NFL" / "runs" / run["run_id"] / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert ar.context_slate_key(manifest["context"]) == suffix
    assert suffix == (SLATE if window is None else f"{SLATE}_{window.strip().lower()}")
    assert (tmp_path / "NFL" / "normalized" / f"nfl_run_pointer_{suffix}.json").is_file()


@pytest.mark.parametrize("window", [None, "", "early", " 1PM "])
def test_context_slate_key_matches_the_pipeline_suffix_formula(window: str | None) -> None:
    """Hand-written contexts (catch-up of old bundles) get the pipeline's suffix too."""
    suffix = f"{SLATE}_{window.strip().lower()}" if window and window.strip() else SLATE
    assert ar.context_slate_key({"slate_date": SLATE, "window": window}) == suffix
