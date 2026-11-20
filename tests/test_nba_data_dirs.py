"""Durable run-bundle location: one helper, env overrides, never the repo or TEMP."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import outlier_nba.cli as cli
from outlier_nba import data_dirs, team_run
from outlier_nba.run_writer import sha256_hex
from outlier_scrapers import paths

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nba_shadow_daily.ps1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in (data_dirs.DATA_DIR_ENV, data_dirs.BUNDLES_DIR_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(data_dirs, "_forbidden_roots", lambda: ())  # tmp_path is under TEMP
    return tmp_path


def test_default_data_dir_is_next_to_the_ledger_under_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert data_dirs.data_dir() == tmp_path / "outlier_nba_data"
    assert data_dirs.bundles_dir() == tmp_path / "outlier_nba_data" / "bundles"


def test_env_overrides_the_data_dir_and_the_bundles_dir_separately(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(data_dirs.DATA_DIR_ENV, str(tmp_path / "d"))
    assert data_dirs.bundles_dir() == tmp_path / "d" / "bundles"
    monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(tmp_path / "b"))
    assert data_dirs.bundles_dir() == tmp_path / "b"


def test_team_and_capture_bundles_get_separate_roots(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(tmp_path))
    team, capture = data_dirs.team_bundle_root(), data_dirs.capture_bundle_root()
    assert team != capture and team.parent == capture.parent == tmp_path


def test_a_location_inside_the_repo_or_temp_is_refused(monkeypatch, tmp_path) -> None:
    monkeypatch.undo()  # drop the autouse relaxation: use the real forbidden roots
    for bad in (paths.PROJECT_ROOT / "data" / "bundles", Path(__import__("tempfile").gettempdir())):
        monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(bad / "x"))
        with pytest.raises(data_dirs.NonDurableLocationError):
            data_dirs.bundles_dir()


def test_the_default_location_is_acceptable_under_the_real_rules(monkeypatch) -> None:
    monkeypatch.undo()
    monkeypatch.delenv(data_dirs.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv(data_dirs.BUNDLES_DIR_ENV, raising=False)
    assert data_dirs.bundles_dir().name == "bundles"  # does not raise


def test_run_and_capture_default_to_the_durable_roots(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(tmp_path / "b"))
    seen: dict = {}

    def fake_team(client, **kw):
        seen["team"] = kw["nba_dir"]
        return cli.PipelineResult("r", "OK", 0, True, None, [])

    def fake_capture(client, **kw):
        seen["capture"] = kw["nba_dir"]
        return cli.PipelineResult("r", "OK", 0, True, None, [])

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(team_run, "run_team_shadow", fake_team)
    monkeypatch.setattr(cli, "run_capture", fake_capture)
    db = str(tmp_path / "l.sqlite3")
    cli.main(["run", "--db", db])
    cli.main(["capture", "--db", db])
    assert seen["team"] == tmp_path / "b" / "team"
    assert seen["capture"] == tmp_path / "b" / "capture"


def _bundle(root: Path, run_id: str) -> None:
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    data = team_run.rows_to_csv([]).encode()
    (run_dir / "team_shadow.csv").write_bytes(data)
    body = {
        "run_id": run_id,
        "artifacts": [{"name": "team_shadow.csv", "sha256": sha256_hex(data)}],
    }
    (run_dir / "manifest.json").write_text(json.dumps(body), encoding="utf-8")


def test_backfill_finds_bundles_at_the_default_without_flags(monkeypatch, tmp_path, capsys) -> None:
    import sqlite3

    monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(tmp_path / "b"))
    _bundle(data_dirs.team_bundle_root(), "r1")
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert cli.main(["backfill-model-probs", "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["bundles_scanned"] == 1


def test_backfill_default_does_not_touch_the_old_temp_location(
    monkeypatch, tmp_path, capsys
) -> None:
    import sqlite3

    monkeypatch.setenv(data_dirs.BUNDLES_DIR_ENV, str(tmp_path / "b"))
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert cli.main(["backfill-model-probs", "--db", str(db)]) == 0  # nothing at the default yet
    assert json.loads(capsys.readouterr().out)["bundles_scanned"] == 0


def test_the_daily_script_no_longer_writes_bundles_to_temp() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "nba_runs" not in text and "$env:TEMP" not in text
    assert "--nba-dir" not in text  # the CLI default (the shared helper) decides
    assert re.search(r"OUTLIER_NBA_DATA_DIR\s*=\s*\$DataDir", text)


def test_no_script_still_points_bundles_at_the_old_temp_folders() -> None:
    for script in (ROOT / "scripts").glob("*.ps1"):
        assert "nba_runs" not in script.read_text(encoding="utf-8", errors="ignore"), script.name
