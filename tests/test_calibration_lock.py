"""Calibration lock: weights are write-once versions behind a verified pointer,
and no test can write the real ``calibration/`` (the write is refused first)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
from outlier_scrapers import (
    calibration_store,
    feedback_db,
    paths,
    probability_blend,
    stake_calibration,
)

# Set by tests/conftest.py before any outlier import (do not import conftest here: a
# second import would create a second temp dir and overwrite the environment).
REAL_CALIBRATION_DIRS = tuple(
    Path(p) for p in os.environ[calibration_store.PROTECTED_DIRS_ENV].split(os.pathsep)
)

BLEND = {"schema_version": probability_blend.SCHEMA_VERSION, "status": "active", "global": {"n": 1}}


def _live(tmp_path: Path) -> Path:
    return tmp_path / "calibration" / "blend_weights.json"


# --- the guard: real calibration/ is refused before any write ---------------------

@pytest.mark.parametrize("real_dir", REAL_CALIBRATION_DIRS, ids=str)
def test_blend_write_to_real_calibration_is_refused_and_creates_nothing(real_dir):
    before = sorted(p.name for p in real_dir.rglob("*")) if real_dir.exists() else None
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        probability_blend.write_weight_artifact(BLEND, real_dir / "blend_weights.json")
    after = sorted(p.name for p in real_dir.rglob("*")) if real_dir.exists() else None
    assert after == before


@pytest.mark.parametrize("real_dir", REAL_CALIBRATION_DIRS, ids=str)
def test_stake_write_to_real_calibration_is_refused(real_dir):
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        stake_calibration.write_stake_calibration_artifact(
            {"schema_version": 1, "status": "active"}, real_dir / "stake_calibration.json"
        )
    assert not (real_dir / "versions" / "stake_calibration").exists()


@pytest.mark.parametrize("real_dir", REAL_CALIBRATION_DIRS, ids=str)
def test_ledger_open_in_real_calibration_is_refused_before_anything_is_created(real_dir):
    ledger = real_dir / "feedback.sqlite3"
    existed = ledger.exists()
    size = ledger.stat().st_size if existed else None
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        feedback_db.initialize_database(ledger)
    assert ledger.exists() == existed
    if existed:
        assert ledger.stat().st_size == size


def test_tests_run_against_a_temporary_calibration_dir():
    assert paths.FEEDBACK_DB_PATH.parent not in REAL_CALIBRATION_DIRS
    for default in (
        probability_blend.DEFAULT_WEIGHTS_PATH,
        stake_calibration.DEFAULT_ARTIFACT_PATH,
    ):
        assert default.parent.resolve() not in REAL_CALIBRATION_DIRS
        assert default.parent == calibration_store.calibration_dir()


def test_symlinked_calibration_dir_pointing_at_real_dir_is_refused(tmp_path):
    real_dir = REAL_CALIBRATION_DIRS[0]
    link = tmp_path / "calibration"
    try:
        os.symlink(real_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        probability_blend.write_weight_artifact(BLEND, link / "blend_weights.json")
    assert not (real_dir / "versions" / "blend_weights").exists() or not any(
        (real_dir / "versions" / "blend_weights").glob("*")
    )


# --- write-once versions and the pointer ------------------------------------------

def test_publish_never_writes_the_live_file_and_readers_follow_the_pointer(tmp_path):
    live = _live(tmp_path)
    live.parent.mkdir()
    live.write_text('{"legacy": true}\n', encoding="utf-8")
    legacy_hash = hashlib.sha256(live.read_bytes()).hexdigest()

    version = probability_blend.write_weight_artifact(BLEND, live)

    assert hashlib.sha256(live.read_bytes()).hexdigest() == legacy_hash
    assert version.parent == live.parent / "versions" / "blend_weights"
    assert probability_blend.load_weight_artifact(live) == BLEND
    pointer = calibration_store.read_pointer(live)
    assert pointer["sha256"] == calibration_store.sha256_file(version)


def test_each_refit_is_a_new_version_and_old_versions_stay(tmp_path):
    live = _live(tmp_path)
    first = probability_blend.write_weight_artifact(BLEND, live)
    first_bytes = first.read_bytes()
    second_artifact = {**BLEND, "global": {"n": 2}}
    second = probability_blend.write_weight_artifact(second_artifact, live)

    assert first != second
    assert first.read_bytes() == first_bytes
    assert probability_blend.load_weight_artifact(live) == second_artifact
    assert calibration_store.known_version_hashes(live) == {
        calibration_store.sha256_file(first),
        calibration_store.sha256_file(second),
    }
    history = (live.parent / calibration_store.POINTER_HISTORY_NAME).read_text().splitlines()
    assert [json.loads(line)["version_file"] for line in history] == [
        first.relative_to(live.parent).as_posix(),
        second.relative_to(live.parent).as_posix(),
    ]


def test_publishing_identical_content_is_idempotent(tmp_path):
    live = _live(tmp_path)
    a = probability_blend.write_weight_artifact(BLEND, live)
    b = probability_blend.write_weight_artifact(BLEND, live)
    assert a == b
    assert len(list(a.parent.glob("*.json"))) == 1


def test_existing_version_name_with_different_content_is_refused(tmp_path):
    live = _live(tmp_path)
    version = probability_blend.write_weight_artifact(BLEND, live)
    version.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        probability_blend.write_weight_artifact(BLEND, live)
    assert version.read_text() == "tampered\n"


def test_altered_version_fails_the_pointer_hash_check(tmp_path):
    live = _live(tmp_path)
    version = probability_blend.write_weight_artifact(BLEND, live)
    version.write_text(json.dumps({**BLEND, "global": {"n": 999}}), encoding="utf-8")
    with pytest.raises(calibration_store.CalibrationVersionError, match="does not match"):
        probability_blend.load_weight_artifact(live)


def test_missing_version_fails_loudly_instead_of_falling_back(tmp_path):
    live = _live(tmp_path)
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps(BLEND), encoding="utf-8")
    version = probability_blend.write_weight_artifact({**BLEND, "global": {"n": 7}}, live)
    version.unlink()
    with pytest.raises(calibration_store.CalibrationVersionError, match="missing"):
        probability_blend.load_weight_artifact(live)


def test_without_a_pointer_the_legacy_live_file_is_still_read(tmp_path):
    live = _live(tmp_path)
    live.parent.mkdir(parents=True)
    live.write_text(json.dumps(BLEND), encoding="utf-8")
    assert calibration_store.resolve_artifact_path(live) == live
    assert probability_blend.load_weight_artifact(live) == BLEND


def test_malformed_pointer_is_an_error(tmp_path):
    live = _live(tmp_path)
    live.parent.mkdir(parents=True)
    calibration_store.pointer_path(live).write_text("{not json", encoding="utf-8")
    with pytest.raises(calibration_store.CalibrationVersionError):
        probability_blend.load_weight_artifact(live)


def test_write_through_a_symlinked_versions_dir_is_refused(tmp_path):
    live = _live(tmp_path)
    live.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        os.symlink(elsewhere, live.parent / "versions", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        probability_blend.write_weight_artifact(BLEND, live)
    assert list(elsewhere.iterdir()) == []


def test_stake_calibration_uses_the_same_versioning(tmp_path):
    live = tmp_path / "calibration" / "stake_calibration.json"
    artifact = {"schema_version": stake_calibration.SCHEMA_VERSION, "status": "active"}
    version = stake_calibration.write_stake_calibration_artifact(artifact, live)
    assert version.parent == live.parent / "versions" / "stake_calibration"
    assert not live.exists()
    assert calibration_store.read_pointer(live)["source"] == "stake_calibration"


def test_ledger_in_a_temporary_dir_is_still_writable(tmp_path):
    db = tmp_path / "calibration" / "feedback.sqlite3"
    feedback_db.initialize_database(db)
    assert db.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1


def test_windows_junction_is_detected_without_path_is_junction(tmp_path, monkeypatch):
    """Python 3.11 has no Path.is_junction: a reparse-point attribute must be enough."""
    junction = tmp_path / "calibration"
    junction.mkdir()
    real_lstat = os.lstat

    class _Stat:
        def __init__(self, inner):
            self._inner = inner

        st_file_attributes = 0x400

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fake_lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        return _Stat(result) if Path(path) == junction else result

    monkeypatch.setattr(calibration_store.os, "lstat", fake_lstat)
    with pytest.raises(calibration_store.CalibrationWriteRefused, match="symlink or junction"):
        probability_blend.write_weight_artifact(BLEND, junction / "blend_weights.json")


def test_crash_between_history_and_pointer_never_leaves_a_pointer_without_history(tmp_path):
    live = _live(tmp_path)

    def crash(src, dst):
        raise RuntimeError("crash before pointer replace")

    with pytest.raises(RuntimeError):
        calibration_store.publish_version(BLEND, live, replace_file=crash)
    assert not calibration_store.pointer_path(live).exists()
    assert (live.parent / calibration_store.POINTER_HISTORY_NAME).exists()


COMMITTED_CALIBRATION = Path(__file__).resolve().parents[1] / "calibration"


def test_committed_weights_load_exactly_as_the_raw_files(tmp_path):
    """No pointer yet: readers return the committed files' own content, as on master."""
    for name, loader in (
        ("blend_weights.json", probability_blend.load_weight_artifact),
        ("stake_calibration.json", stake_calibration.load_stake_calibration_artifact),
    ):
        copy = tmp_path / name
        copy.write_bytes((COMMITTED_CALIBRATION / name).read_bytes())
        assert loader(copy) == json.loads(copy.read_text(encoding="utf-8"))


def test_daily_refit_next_to_committed_weights_leaves_them_untouched(tmp_path, monkeypatch):
    from outlier_scrapers import daily_job

    live = tmp_path / "blend_weights.json"
    live.write_bytes((COMMITTED_CALIBRATION / "blend_weights.json").read_bytes())
    committed_hash = hashlib.sha256(live.read_bytes()).hexdigest()
    db = tmp_path / "feedback.sqlite3"
    db.write_text("", encoding="utf-8")
    refit = {**BLEND, "eligible_samples": 123, "model_version": "blend-lock-test"}

    def fit_through_the_real_writer(db_path, output_path):
        probability_blend.write_weight_artifact(refit, output_path)
        return refit

    monkeypatch.setattr(daily_job.feedback, "fit_blend_weights", fit_through_the_real_writer)
    stats = daily_job.refit_blend_weights(db, live)

    assert stats["status"] == "ok"
    assert hashlib.sha256(live.read_bytes()).hexdigest() == committed_hash
    pointer = calibration_store.read_pointer(live)
    assert pointer["version_file"].startswith("versions/blend_weights/blend_weights.")
    assert probability_blend.load_weight_artifact(live) == refit


@pytest.mark.parametrize("error", [
    calibration_store.CalibrationWriteRefused("refused"),
    calibration_store.CalibrationVersionError("bad pointer"),
])
def test_daily_refit_aborts_on_a_refused_write_or_bad_pointer(tmp_path, monkeypatch, error):
    from outlier_scrapers import daily_job

    db = tmp_path / "feedback.sqlite3"
    db.write_text("", encoding="utf-8")

    def fail(*_a, **_k):
        raise error

    monkeypatch.setattr(daily_job.feedback, "fit_blend_weights", fail)
    with pytest.raises(type(error)):
        daily_job.refit_blend_weights(db, tmp_path / "blend_weights.json")
