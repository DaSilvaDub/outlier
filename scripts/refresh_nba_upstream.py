"""Collect NBA publication inputs without the broad watcher or research desk."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_nba_extra_pack import FIELDNAMES

# The ESPN_MODEL projections behind RG-free edges come from this engine's PlayerBook,
# which refuses history more than a day behind the slate. Nothing else refreshes it.
DEFAULT_ENGINE_DIR = Path(r"C:\Users\dasil\.gemini\config\skills\nba-master-betting-pipeline")
ESPN_MODEL_STATUS = re.compile(r"ESPN PlayerBook (?:status=(\w+)|unavailable)")


def season_start_year(slate: date) -> int:
    return slate.year if slate.month >= 7 else slate.year - 1


def refresh_history(engine_dir: Path, slate_date: str, env: dict, runner) -> dict:
    """Bring the engine's ESPN results cache up to the slate. Best effort: edges still
    run without it, but every Hard Rock line then goes unmatched in RG-free mode."""
    script = engine_dir / "scripts" / "espn_history.py"
    if not script.is_file():
        return {"name": "history", "skipped": "engine_missing"}
    season = str(season_start_year(date.fromisoformat(slate_date)))
    codes = []
    for what in ("games", "boxes"):
        try:
            result = runner(
                [sys.executable, str(script), what, "--seasons", season],
                cwd=engine_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            codes.append(result.returncode)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"name": "history", "error": type(exc).__name__}
    return {"name": "history", "returncode": max(codes)}


def empty_inputs(output: Path, slate_date: str, error: str) -> None:
    """Replace retained inputs on failure; never fall back to another run."""
    output.mkdir(parents=True, exist_ok=True)
    for name in ("prop_edges_latest.csv", "nba_graded_plays_latest.csv"):
        with (output / name).open("w", encoding="utf-8", newline="") as stream:
            csv.DictWriter(stream, fieldnames=FIELDNAMES).writeheader()
    (output / "prop_edges_latest.json").write_text(
        json.dumps(
            {
                "slate_date": slate_date,
                "rows": [],
                "publication_error": error,
            }
        ),
        encoding="utf-8",
    )
    (output / "nba_availability_registry.json").write_text(
        json.dumps(
            {
                "slate_date": slate_date,
                "teams": {},
                "error": error,
            }
        ),
        encoding="utf-8",
    )


def refresh(
    pipeline_repo: Path,
    slate_date: str,
    output: Path,
    data_root: Path,
    *,
    use_rg: bool = False,
    report_pdf: Path | None = None,
    report_url: str | None = None,
    engine_dir: Path | None = None,
    runner=None,
) -> int:
    pipeline_repo = pipeline_repo.resolve()
    output = output.resolve()
    report_pdf = report_pdf.resolve() if report_pdf else None
    date.fromisoformat(slate_date)
    output.mkdir(parents=True, exist_ok=True)
    runner = runner or subprocess.run
    env = os.environ.copy()
    env.update(NBA_ROOT=str(data_root.resolve()), PYTHONIOENCODING="utf-8")
    status = {
        "slate_date": slate_date,
        "started_utc": datetime.now(UTC).isoformat(),
        "status": "running",
        "stages": [],
    }
    # Clear retained input products before attempting a new collection.
    empty_inputs(output, slate_date, "refresh_in_progress")
    engine_dir = engine_dir or Path(os.environ.get("NBA_ENGINE_DIR") or DEFAULT_ENGINE_DIR)
    stages = [
        (
            "odds",
            [
                str(pipeline_repo / "scrapers/hardrock/fetch_player_props.py"),
                "--date",
                slate_date,
                "--strict-date",
            ],
        ),
        (
            "edges",
            [
                str(pipeline_repo / "pipeline/generate_prop_edges.py"),
                "--date",
                slate_date,
                "--output-dir",
                str(output),
            ]
            + ([] if use_rg else ["--no-rg"]),
        ),
        (
            "grades",
            [
                str(pipeline_repo / "pipeline/generate_playbook_grades.py"),
                "--edges-json",
                str(output / "prop_edges_latest.json"),
                "--output-dir",
                str(output),
            ],
        ),
        (
            "availability",
            [
                str(pipeline_repo / "scripts/refresh_official_availability.py"),
                "--date",
                slate_date,
                "--props-csv",
                str(output / "prop_edges_latest.csv"),
                "--out",
                str(output / "nba_availability_registry.json"),
                "--raw-dir",
                str(output / "official_reports"),
            ]
            + (["--report-pdf", str(report_pdf)] if report_pdf else [])
            + (["--report-url", report_url] if report_url else []),
        ),
    ]
    try:
        for name, args in stages:
            if not Path(args[0]).is_file():
                raise RuntimeError(f"{name}: required upstream script missing")
            if name == "edges":
                status["stages"].append(refresh_history(engine_dir, slate_date, env, runner))
            result = runner(
                [sys.executable, *args],
                cwd=pipeline_repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            status["stages"].append({"name": name, "returncode": result.returncode})
            # Provider diagnostics may contain credential-bearing URLs. Never echo them.
            if result.returncode:
                raise RuntimeError(f"{name}: upstream exited {result.returncode}")
            if name == "edges":
                edges = json.loads((output / "prop_edges_latest.json").read_text(encoding="utf-8"))
                if edges.get("slate_date") != slate_date or not isinstance(edges.get("rows"), list):
                    raise RuntimeError("edges: requested slate mismatch or invalid rows")
                status["source_lines"] = edges.get("hardrock_total_lines", 0)
                status["matched_rows"] = len(edges["rows"])
                # A fixed status token only; edge output carries no provider URLs.
                found = ESPN_MODEL_STATUS.search(getattr(result, "stdout", None) or "")
                status["espn_model"] = (found.group(1) or "unavailable") if found else "ok"
        edges = json.loads((output / "prop_edges_latest.json").read_text(encoding="utf-8"))
        status["status"] = (
            "inputs_collected"
            if edges.get("rows")
            else "no_matched_edges"
            if status.get("source_lines")
            else "awaiting_odds"
        )
        code = 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Fixed diagnostic only: avoid persisting exceptions containing request URLs.
        error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if name == "availability" and (edges.get("rows") or status.get("source_lines")):
            # Valid current quotes can remain research inputs; the availability
            # filter will stand them down using the explicit unknown registry.
            (output / "nba_availability_registry.json").write_text(
                json.dumps({"slate_date": slate_date, "teams": {}, "error": error}),
                encoding="utf-8",
            )
            status.update(
                status="awaiting_availability" if edges.get("rows") else "no_matched_edges",
                error=error,
            )
        else:
            empty_inputs(output, slate_date, error)
            status.update(status="awaiting_odds", error=error)
        code = 2
    status["completed_utc"] = datetime.now(UTC).isoformat()
    (output / "upstream_inputs.status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    print(f"NBA upstream: {status['status']}; stages={len(status['stages'])}; date={slate_date}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-repo", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--report-pdf", type=Path)
    parser.add_argument("--report-url")
    parser.add_argument("--use-rg", action="store_true")
    parser.add_argument("--engine-dir", type=Path, help="nba-master-betting-pipeline checkout")
    args = parser.parse_args()
    return refresh(
        args.pipeline_repo,
        args.date,
        args.output_dir,
        args.data_root,
        use_rg=args.use_rg,
        report_pdf=args.report_pdf,
        report_url=args.report_url,
        engine_dir=args.engine_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
