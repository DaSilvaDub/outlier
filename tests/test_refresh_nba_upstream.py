"""Upstream collection must select its own evidence and stand down on failure."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from scripts.refresh_nba_upstream import refresh


def pipeline_files(root):
    for name in (
        "scrapers/hardrock/fetch_player_props.py",
        "pipeline/generate_prop_edges.py",
        "pipeline/generate_playbook_grades.py",
        "scripts/refresh_official_availability.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_success_preserves_explicit_date_and_avoids_broad_watchers(tmp_path):
    repo, output = tmp_path / "pipeline", tmp_path / "inputs"
    pipeline_files(repo)
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if Path(args[1]).name == "generate_prop_edges.py":
            (output / "prop_edges_latest.json").write_text(
                json.dumps({"slate_date": "2026-11-26", "rows": []})
            )
        return SimpleNamespace(returncode=0)

    assert refresh(repo, "2026-11-26", output, tmp_path / "source", runner=runner) == 0
    assert len(calls) == 4
    assert "--no-rg" in calls[1][0]
    assert calls[1][0][calls[1][0].index("--date") + 1] == "2026-11-26"
    assert calls[3][0][calls[3][0].index("--props-csv") + 1] == str(
        output / "prop_edges_latest.csv"
    )
    assert all(
        "watcher" not in " ".join(args) and "run_daily" not in " ".join(args) for args, _ in calls
    )
    assert (
        json.loads((output / "upstream_inputs.status.json").read_text())["status"]
        == "awaiting_odds"
    )


def test_fetch_failure_replaces_retained_edges_without_leaking_diagnostics(tmp_path):
    repo, output = tmp_path / "pipeline", tmp_path / "inputs"
    pipeline_files(repo)
    output.mkdir()
    (output / "prop_edges_latest.csv").write_text("prior slate picks")
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=2, stderr="https://example.test/?apiKey=secret")

    assert refresh(repo, "2026-11-26", output, tmp_path / "source", runner=runner) == 2
    assert len(calls) == 1
    assert "prior slate picks" not in (output / "prop_edges_latest.csv").read_text()
    assert "secret" not in (output / "upstream_inputs.status.json").read_text()
    assert json.loads((output / "nba_availability_registry.json").read_text())["teams"] == {}


def test_wrong_slate_or_timeout_cannot_leave_ready_inputs(tmp_path):
    repo, output = tmp_path / "pipeline", tmp_path / "inputs"
    pipeline_files(repo)

    def wrong_slate(args, **kwargs):
        if Path(args[1]).name == "generate_prop_edges.py":
            (output / "prop_edges_latest.json").write_text(
                json.dumps({"slate_date": "2026-11-25", "rows": [{}]})
            )
        return SimpleNamespace(returncode=0)

    assert refresh(repo, "2026-11-26", output, tmp_path / "source", runner=wrong_slate) == 2
    assert json.loads((output / "prop_edges_latest.json").read_text())["rows"] == []

    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 240, stderr="secret")

    assert refresh(repo, "2026-11-26", output, tmp_path / "source", runner=timed_out) == 2
    assert "secret" not in (output / "upstream_inputs.status.json").read_text()


def test_unavailable_official_report_preserves_quotes_as_research_only(tmp_path):
    repo, output = tmp_path / "pipeline", tmp_path / "inputs"
    pipeline_files(repo)

    def runner(args, **kwargs):
        stage = Path(args[1]).name
        if stage == "generate_prop_edges.py":
            (output / "prop_edges_latest.json").write_text(
                json.dumps({"slate_date": "2026-11-26", "rows": [{"player": "Current Player"}]})
            )
        return SimpleNamespace(returncode=2 if stage == "refresh_official_availability.py" else 0)

    assert refresh(repo, "2026-11-26", output, tmp_path / "source", runner=runner) == 2
    assert json.loads((output / "prop_edges_latest.json").read_text())["rows"]
    assert json.loads((output / "nba_availability_registry.json").read_text())["teams"] == {}
    assert (
        json.loads((output / "upstream_inputs.status.json").read_text())["status"]
        == "awaiting_availability"
    )
