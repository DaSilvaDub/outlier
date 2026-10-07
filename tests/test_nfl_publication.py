"""Run bundles and publication (F27 consistency, F11 dated publication)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl import run_writer
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_writer import RunWriter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-09-13"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, **kw):
    return NflPipeline(data_dir=tmp_path).run(
        date=SLATE, offline_fixtures_dir=FIXTURES_DIR, reports_dir=tmp_path / "reports", **kw
    )


# ---------------------------------------------------------------------------
# F27: one staged bundle per run, pointers written last
# ---------------------------------------------------------------------------

def test_run_bundle_manifest_hashes_match_and_pointer_written_last(tmp_path, monkeypatch):
    order: list[str] = []
    real = run_writer._atomic_write_bytes

    def record(dest: Path, data: bytes) -> None:
        order.append(dest.name)
        real(dest, data)

    monkeypatch.setattr(run_writer, "_atomic_write_bytes", record)
    summary = _run(tmp_path)

    run_dir = Path(summary["run_dir"])
    assert run_dir.parent == tmp_path / "NFL" / "runs" and run_dir.name == summary["run_id"]
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["context"]["slate_date"] == SLATE and manifest["context"]["mode"] == "fixture"
    names = {a["name"] for a in manifest["artifacts"]}
    assert {"summary.json", "nfl_best_bets.json", "nfl_matchup_scripts.json",
            "raw/schedule.json", "raw/player_props.json"} <= names
    for art in manifest["artifacts"]:
        assert _sha(run_dir / art["name"]) == art["sha256"], art["name"]
        for dest in art["published_to"]:
            assert _sha(Path(dest)) == art["sha256"], dest

    pointers = [n for n in order if n.startswith("nfl_run_pointer_")]
    assert pointers and order[-len(pointers):] == pointers  # nothing published after a pointer
    pointer = json.loads((tmp_path / "NFL" / "normalized" / f"nfl_run_pointer_{SLATE}.json")
                         .read_text())
    assert pointer["run_id"] == summary["run_id"]
    assert pointer["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert not list((tmp_path / "NFL" / "runs").glob(".staging-*"))


def test_failed_run_publishes_nothing(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("roster index exploded")

    monkeypatch.setattr(nfl_pipeline, "build_team_roster_index", boom)
    with pytest.raises(RuntimeError):
        _run(tmp_path)
    normalized = tmp_path / "NFL" / "normalized"
    assert not list(normalized.glob("*.json"))  # no half-written slate
    assert not [p for p in (tmp_path / "NFL" / "runs").iterdir() if not p.name.startswith(".")]


def test_snapshot_rows_carry_the_run_id(tmp_path):
    summary = _run(tmp_path)
    rows = [json.loads(line) for line in
            next((tmp_path / "NFL" / "snapshots").glob("*.jsonl")).read_text().splitlines()]
    assert rows and {r["run_id"] for r in rows} == {summary["run_id"]}


def test_run_writer_refuses_reuse(tmp_path):
    writer = RunWriter(tmp_path, "r1")
    writer.stage_json("a.json", {"x": 1}, publish=[tmp_path / "out" / "a.json"])
    writer.commit({}, pointers=[tmp_path / "out" / "ptr.json"])
    assert json.loads((tmp_path / "out" / "a.json").read_text()) == {"x": 1}
    with pytest.raises(RuntimeError):
        writer.commit({})
    with pytest.raises(FileExistsError):
        RunWriter(tmp_path, "r1")
