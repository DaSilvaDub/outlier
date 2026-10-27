"""The committed weights restored as locked versions: same values, live files untouched."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from outlier_scrapers import calibration_store, probability_blend, stake_calibration
from scripts import restore_committed_calibration as restore

REPO = Path(__file__).resolve().parents[1]
LOADERS = {
    "blend_weights.json": probability_blend.load_weight_artifact,
    "stake_calibration.json": stake_calibration.load_stake_calibration_artifact,
}


def _committed(name: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO), "show", f"HEAD:calibration/{name}"], capture_output=True, check=True
    ).stdout


def test_restored_versions_load_the_committed_values(tmp_path):
    restore.restore(REPO, "HEAD", tmp_path)
    for name, loader in LOADERS.items():
        assert loader(tmp_path / name) == json.loads(_committed(name).decode("utf-8"))
        pointer = calibration_store.read_pointer(tmp_path / name)
        assert pointer["run_id"] == restore.RUN_ID
        assert pointer["source"] == f"git:{restore.COMMITTED_BLOBS[name]}"


def test_restore_never_writes_the_live_files_and_is_idempotent(tmp_path):
    for name in LOADERS:
        (tmp_path / name).write_bytes(_committed(name))
    before = {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in LOADERS}
    restore.restore(REPO, "HEAD", tmp_path)
    history = (tmp_path / calibration_store.POINTER_HISTORY_NAME).read_text()
    again = restore.restore(REPO, "HEAD", tmp_path)
    assert all("unchanged" in line for line in again)
    assert (tmp_path / calibration_store.POINTER_HISTORY_NAME).read_text() == history
    assert before == {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in LOADERS}


def test_restore_refuses_a_blob_that_is_not_the_committed_one(tmp_path, monkeypatch):
    # the right 7-character prefix is not enough: the whole blob id must match
    monkeypatch.setitem(
        restore.COMMITTED_BLOBS,
        "blend_weights.json",
        restore.COMMITTED_BLOBS["blend_weights.json"][:7] + "0" * 33,
    )
    with pytest.raises(restore.RestoreError, match="expected the committed"):
        restore.restore(REPO, "HEAD", tmp_path)
    assert not (tmp_path / "versions").exists()


def test_restore_does_not_repoint_a_pointer_at_another_version(tmp_path):
    live = tmp_path / "blend_weights.json"
    calibration_store.publish_version({"status": "other"}, live)
    with pytest.raises(restore.RestoreError, match="not repointing"):
        restore.restore(REPO, "HEAD", tmp_path)


def test_restore_into_the_real_calibration_dir_is_refused_under_test():
    probe = REPO / "calibration" / "restore_probe"
    with pytest.raises(calibration_store.CalibrationWriteRefused):
        restore.restore(REPO, "HEAD", probe)
    assert not probe.exists()


@pytest.mark.parametrize("name", sorted(LOADERS))
def test_checked_in_pointer_verifies_and_matches_the_checked_in_weights(name):
    """The pointer and version shipped in ``calibration/`` verify and equal the live file."""
    live = REPO / "calibration" / name
    assert calibration_store.resolve_artifact_path(live) != live
    assert LOADERS[name](live) == json.loads(live.read_text(encoding="utf-8"))
