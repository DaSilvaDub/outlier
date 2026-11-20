"""Exercise the artifact tripwire exclusively against temporary checkouts."""

import os

import pytest

from tests import conftest as guard


@pytest.mark.parametrize("location", ["checkout", "canonical"])
@pytest.mark.parametrize("operation", ["create", "rewrite", "delete", "same_stat"])
def test_guard_detects_untracked_calibration_json(tmp_path, monkeypatch, location, operation):
    roots = tuple(tmp_path / name for name in ("checkout", "canonical"))
    target = tmp_path / location / "calibration" / "alerts" / "untracked.json"
    target.parent.mkdir(parents=True)
    if operation != "create":
        target.write_text('{"value": 1}', encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(guard, "_ARTIFACT_ROOTS", roots)
        patch.setattr(guard, "_TRACKED_GUARDED_FILES", ())
        before = guard._guarded_digests()
        if operation == "delete":
            target.unlink()
        else:
            old_stat = target.stat() if target.exists() else None
            target.write_text('{"value": 2}', encoding="utf-8")
            if operation == "same_stat":
                os.utime(target, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        after = guard._guarded_digests()
        assert guard._changed_artifacts(before, after) == [target]


def test_guard_allows_unchanged_json_and_ignores_untracked_ledger(tmp_path, monkeypatch):
    calibration = tmp_path / "calibration"
    calibration.mkdir()
    target = calibration / "blend_weights.json"
    target.write_text("{}", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(guard, "_ARTIFACT_ROOTS", (tmp_path,))
        patch.setattr(guard, "_TRACKED_GUARDED_FILES", ())
        before = guard._guarded_digests()
        target.touch()
        (calibration / "feedback.sqlite3").write_bytes(b"test ledger placeholder")
        assert guard._changed_artifacts(before, guard._guarded_digests()) == []


def test_guard_still_detects_tracked_report_deletion(tmp_path, monkeypatch):
    target = tmp_path / "reports" / "NFL" / "latest.md"
    target.parent.mkdir(parents=True)
    target.write_text("saved report", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(guard, "_ARTIFACT_ROOTS", (tmp_path,))
        patch.setattr(guard, "_TRACKED_GUARDED_FILES", (target,))
        before = guard._guarded_digests()
        target.unlink()
        assert guard._changed_artifacts(before, guard._guarded_digests()) == [target]
