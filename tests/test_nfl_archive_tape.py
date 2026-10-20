"""9c follow-up: the tape a run admitted is archived as a write-once copy.

The run manifest's ``sources`` carries the admitted tape's sha256
(``matchup.load_tape_envelope``: ``SourceRecord("tape", "AVAILABLE", ...,
sha256=...)``). The tape file is ``<nfl_dir>/tape/prior_week.json`` (shared) or
``tape/replay/<slate>/<as_of>/tape/prior_week.json`` (``pipeline._refresh_tape``
for non-live runs). The live original is produced as in the stage-gate tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from outlier_nfl import archive as ar
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_archive
from scripts import nfl_snapshot_diff as snap
from tests.test_nfl_archive_writer import unarchived

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def unavailable(*_a, **_k):
        raise OSError("network disabled in tests")

    for name in ("load_external_metrics", "load_slate_weather", "load_usage"):
        monkeypatch.setattr(nfl_pipeline, name, unavailable)


def _live(tmp_path: Path, clock: str = PREGAME) -> tuple[Path, str]:
    nfl = tmp_path / "NFL"
    tape = nfl / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    s = NflPipeline(client=snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                    clock=lambda: now).run(date=SLATE, as_of_utc=clock,
                                           reports_dir=tmp_path / "reports")
    unarchived(nfl)
    return nfl, s["run_id"]


def _tape_entries(nfl: Path) -> list[ar.ArchiveEntry]:
    return [e for e in ar.read_manifest(ar.manifest_path(nfl))[0] if e.role == ar.TAPE_ROLE]


def test_the_admitted_tape_is_copied_under_its_sha(tmp_path):
    nfl, run_id = _live(tmp_path)
    original = (nfl / "tape" / "prior_week.json").read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    result = ar.record_pointed_runs(nfl)
    assert not result.conflicts and not result.notes
    [e] = _tape_entries(nfl)
    assert (e.path, e.sha256, e.run_id, e.slate) == (
        f"archive/tape/{sha}/prior_week.json", sha, run_id, SLATE)
    assert (nfl / e.path).read_bytes() == original
    assert (nfl / "tape" / "prior_week.json").read_bytes() == original  # original untouched


def test_rewritten_tape_keeps_the_archived_copy(tmp_path):
    nfl, _ = _live(tmp_path)
    ar.record_pointed_runs(nfl)
    [first] = _tape_entries(nfl)
    tape = nfl / "tape" / "prior_week.json"
    tape.write_text(tape.read_text("utf-8").replace("2026", "2026 ", 1), encoding="utf-8")
    assert not ar.record_pointed_runs(nfl).conflicts
    assert ar.sha256_file(nfl / first.path) == first.sha256
    assert ar.check_archive(nfl).gaps == []


def test_run_whose_tape_is_gone_records_nothing(tmp_path, capsys):
    nfl, _ = _live(tmp_path)
    tape = nfl / "tape" / "prior_week.json"
    tape.write_text(tape.read_text("utf-8") + " ", encoding="utf-8")  # rewritten after the run
    assert nfl_archive.main(["record", "--nfl-dir", str(nfl)]) == 0
    out = capsys.readouterr().out
    assert "recorded 4" in out and "NOTE" in out and "no tape archived" in out
    assert _tape_entries(nfl) == []
    assert ar.check_archive(nfl).gaps == []  # tape is optional


def test_refused_or_packaged_tape_records_nothing(tmp_path):
    NflPipeline(data_dir=tmp_path).run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR,
                                       reports_dir=tmp_path / "reports")  # packaged tape
    nfl = tmp_path / "NFL"
    unarchived(nfl)
    result = ar.record_pointed_runs(nfl)
    assert not result.notes and _tape_entries(nfl) == []


def test_replay_tape_is_found_by_hash(tmp_path):
    nfl, run_id = _live(tmp_path)
    shared = nfl / "tape" / "prior_week.json"
    replay = nfl / "tape" / "replay" / SLATE / "20261004T150000Z" / "tape" / "prior_week.json"
    replay.parent.mkdir(parents=True)
    replay.write_bytes(shared.read_bytes())
    shared.write_text("{}", encoding="utf-8")  # shared tape rewritten; the replay copy remains
    ar.record_pointed_runs(nfl)
    [e] = _tape_entries(nfl)
    assert ar.sha256_file(nfl / e.path) == e.sha256 and e.run_id == run_id


def test_existing_copy_with_other_bytes_is_refused(tmp_path):
    nfl, _ = _live(tmp_path)
    sha = hashlib.sha256((nfl / "tape" / "prior_week.json").read_bytes()).hexdigest()
    squat = nfl / "archive" / "tape" / sha / "prior_week.json"
    squat.parent.mkdir(parents=True)
    squat.write_bytes(b"x")
    result = ar.record_pointed_runs(nfl)
    assert any("different bytes" in c for c in result.conflicts)
    assert squat.read_bytes() == b"x"


def test_tape_entry_validates_and_is_checked_like_any_file(tmp_path):
    nfl, _ = _live(tmp_path)
    ar.record_pointed_runs(nfl)
    [e] = _tape_entries(nfl)
    (nfl / e.path).write_bytes(b"{}")
    kinds = {(g.role, g.kind) for g in ar.check_archive(nfl).gaps}
    assert (ar.TAPE_ROLE, "size_mismatch") in kinds
