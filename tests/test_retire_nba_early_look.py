"""Offline retirement regression checks; original generators never execute."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "retirement", Path(__file__).resolve().parents[1] / "scripts/retire_nba_early_look.py"
)
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


def seed(tmp_path):
    source = tmp_path / "scripts"
    source.mkdir()
    for name in retirement.ENTRYPOINTS:
        (source / name).write_text("raise AssertionError('original must not run')\n")
    return source, tmp_path / "quarantine"


def test_retirement_preserves_originals_and_is_idempotent(tmp_path):
    source, backup = seed(tmp_path)
    first = retirement.retire(source, backup)
    assert retirement.retire(source, backup) == first
    assert len(first["files"]) == 13
    assert json.loads((backup / "retirement_manifest.json").read_text()) == first
    for row in first["files"]:
        assert retirement.digest((backup / row["name"]).read_bytes()) == row["original_sha256"]


def test_tombstones_fail_on_execution_and_import_without_outputs(tmp_path):
    source, backup = seed(tmp_path)
    retirement.retire(source, backup)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    for name in retirement.ENTRYPOINTS:
        path = source / name
        assert not re.search(rb"\d+(?:\.\d+)?%", path.read_bytes())
        for command in ([sys.executable, "-B", str(path)],
                        [sys.executable, "-B", "-c", f"import runpy; runpy.run_path({str(path)!r})"]):
            result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
            assert result.returncode != 0
            assert "RETIRED / NO BET" in result.stderr
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_corrupt_backup_blocks_before_active_mutation(tmp_path):
    source, backup = seed(tmp_path)
    retirement.retire(source, backup)
    (backup / retirement.ENTRYPOINTS[0]).write_text("corruption")
    with pytest.raises(ValueError, match="hash mismatch"):
        retirement.retire(source, backup)


def test_refuses_backup_inside_active_tree(tmp_path):
    source, _ = seed(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        retirement.retire(source, source / "backup")


def test_unrelated_files_are_untouched(tmp_path):
    source, backup = seed(tmp_path)
    unrelated = source / "safe_engine.py"
    unrelated.write_text("untouched")
    retirement.retire(source, backup)
    assert unrelated.read_text() == "untouched"
