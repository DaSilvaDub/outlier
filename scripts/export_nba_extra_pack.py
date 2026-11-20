"""Export NBA prop edges into an nba_only.csv extra pack for Outlier Drive packs.

Reads the latest prop edges from nba-props-pipeline or Google Drive, standardizes
columns into canonical nba_only.csv, and stages the artifact at
data/NBA/exports/nba_only.csv.

Invariants Enforced:
- NBA-SYS-001: Pre-flight disk space assertion (>= 5.0 GB free required).
- NBA-PROP-010: If prop_edges_latest.csv is missing, empty, or >12h stale,
  automatically triggers on-demand live generation via nba-props-pipeline.
- Fail-Closed: Never silently emit 0-row packs without explicit warning.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess  # nosec B404 - only runs fixed local pipeline scripts, never shell or user input
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_DIR = REPO_ROOT / "data" / "NBA" / "exports"
DEFAULT_OUT = EXPORTS_DIR / "nba_only.csv"
NBA_PIPELINE_REPO = Path(r"C:\Users\dasil\Dev\GitHub\nba-props-pipeline")

FIELDNAMES = [
    "rank",
    "slate_date",
    "matchup",
    "matchup_abbr",
    "player",
    "market",
    "line",
    "rg_projection",
    "rg_minutes",
    "rg_opp",
    "shotquality_projection",
    "sq_agreement",
    "baseline_source",
    "recommended_side",
    "recommended_odds",
    "over_odds",
    "under_odds",
    "edge_value",
    "edge_pct",
    "projection_sources",
    "game_time",
]

CANDIDATE_SOURCES = [
    Path(r"G:\My Drive\Sports_Analytics\01_Active\NBA_Active\PROP EDGES\prop_edges_latest.csv"),
    Path(
        r"C:\Users\dasil\Dev\GitHub\Sports_Analytics\Sports_Analytics\01_Active\NBA_Active\PROP EDGES\prop_edges_latest.csv"
    ),
    NBA_PIPELINE_REPO / "output" / "PROP EDGES" / "prop_edges_latest.csv",
    DEFAULT_OUT,
]


def check_disk_space(min_gb: float = 5.0) -> None:
    """Invariant NBA-SYS-001: Assert sufficient disk space on C: drive."""
    try:
        free_bytes = shutil.disk_usage("C:\\").free
        free_gb = free_bytes / (1024**3)
        if free_gb < min_gb:
            raise RuntimeError(
                f"[NBA-SYS-001 VIOLATION] Insufficient disk space on C: ({free_gb:.2f} GB free, minimum {min_gb:.1f} GB required). "
                "Halt to prevent Errno 28 capture corruption."
            )
    except Exception as e:
        if "NBA-SYS-001" in str(e):
            raise
        print(f"Warning: unable to check disk space: {e}")


def is_source_stale(p: Path, max_age_hours: float = 12.0) -> bool:
    """Check if file modification time is older than max_age_hours."""
    if not p.exists():
        return True
    try:
        mtime = p.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600.0
        return age_hours > max_age_hours
    except Exception:
        return True


def trigger_live_prop_pipeline() -> bool:
    """Invariant NBA-PROP-010: Trigger live prop edges and playbook generation."""
    if not NBA_PIPELINE_REPO.exists():
        print(f"Warning: nba-props-pipeline repo not found at {NBA_PIPELINE_REPO}")
        return False

    print("Triggering on-demand NBA props generation in nba-props-pipeline...")
    python_exe = sys.executable
    edges_script = NBA_PIPELINE_REPO / "pipeline" / "generate_prop_edges.py"
    grades_script = NBA_PIPELINE_REPO / "pipeline" / "generate_playbook_grades.py"

    try:
        if edges_script.exists():
            print(f"Executing {edges_script.name}...")
            # Fixed argv (this interpreter + a script under NBA_PIPELINE_REPO), no shell.
            res = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
                [python_exe, str(edges_script), "--allow-stale-hardrock"],
                cwd=str(NBA_PIPELINE_REPO),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if res.returncode != 0:
                print(
                    f"Warning: generate_prop_edges exited with code {res.returncode}: {res.stderr[:200]}"
                )

        if grades_script.exists():
            print(f"Executing {grades_script.name}...")
            # Fixed argv (this interpreter + a script under NBA_PIPELINE_REPO), no shell.
            res = subprocess.run(  # nosec B603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
                [python_exe, str(grades_script)],
                cwd=str(NBA_PIPELINE_REPO),
                capture_output=True,
                text=True,
                timeout=180,
            )
            if res.returncode != 0:
                print(
                    f"Warning: generate_playbook_grades exited with code {res.returncode}: {res.stderr[:200]}"
                )

        return True
    except Exception as e:
        print(f"Error executing live prop pipeline: {e}")
        return False


def resolve_source(explicit_src: str | None = None, auto_refresh: bool = True) -> Path | None:
    if explicit_src:
        p = Path(explicit_src)
        if p.exists():
            return p
        return None

    resolved: Path | None = None
    for cand in CANDIDATE_SOURCES:
        if cand.exists():
            resolved = cand
            break

    # Invariant NBA-PROP-010: auto-refresh if stale or missing
    if auto_refresh and (resolved is None or is_source_stale(resolved, max_age_hours=12.0)):
        print(f"Source file {resolved} is missing or >12h stale. Attempting on-demand refresh...")
        if trigger_live_prop_pipeline():
            for cand in CANDIDATE_SOURCES:
                if cand.exists() and not is_source_stale(cand, max_age_hours=2.0):
                    resolved = cand
                    break

    return resolved


def export_nba_pack(
    src_path: Path | None,
    out_path: Path,
    slate_date: str | None = None,
    min_edge_pct: float | None = None,
    allow_latest_fallback: bool = True,
    fail_on_empty: bool = False,
) -> int:
    check_disk_space(min_gb=5.0)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    raw_rows: list[dict[str, Any]] = []
    if src_path and src_path.exists():
        with open(src_path, mode="r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)

    date_matches = [r for r in raw_rows if r.get("slate_date") == slate_date] if slate_date else []

    use_rows = raw_rows
    if slate_date:
        if date_matches:
            use_rows = date_matches
        elif not allow_latest_fallback:
            use_rows = []
        elif raw_rows:
            # Publishing another slate's rows under today's date is as damaging as
            # publishing an empty pack, so say so on stderr instead of silently
            # substituting them.
            print(
                f"[PIPELINE WARNING] No rows match slate_date '{slate_date}'; falling back to "
                f"all {len(raw_rows)} row(s) in {src_path}, which may belong to another slate. "
                "Pass --no-allow-latest-fallback to publish an empty pack instead.",
                file=sys.stderr,
            )

    rows: list[dict[str, Any]] = []
    for row in use_rows:
        if min_edge_pct is not None:
            # Signed comparison: a strongly negative edge is the worst bet on the
            # board, not a strong one. abs() let -9% clear --min-edge 5. Every other
            # edge gate in the repo compares the signed value (game_totals.py
            # MIN_EDGE_TOTALS, ultimate_alt.py MIN_EDGE_PCT), and CLAUDE.md
            # invariant 5 requires edge_pct > 0 for an actionable row.
            try:
                edge = float(row.get("edge_pct", 0) or 0)
            except (ValueError, TypeError):
                # An edge that cannot be read as a number has not cleared the bar.
                # Dropping matches the absent-column path, which already yields 0.
                continue
            if edge < min_edge_pct:
                continue

        clean_row = {fn: row.get(fn, "") for fn in FIELDNAMES}
        rows.append(clean_row)

    for idx, r in enumerate(rows, start=1):
        r["rank"] = str(idx)

    with open(out_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} rows to {out_path}")

    if len(rows) == 0:
        warning_msg = (
            f"[PIPELINE WARNING] Export produced 0 candidate rows for date '{slate_date}'. "
            f"Source={src_path} had {len(raw_rows)} total rows."
        )
        print(warning_msg, file=sys.stderr)
        if fail_on_empty:
            raise RuntimeError(warning_msg)

    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD")
    parser.add_argument("--src", default=None, help="Explicit path to prop_edges_latest.csv")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output CSV path")
    parser.add_argument("--min-edge", type=float, default=None, help="Minimum edge_pct filter")
    parser.add_argument(
        "--allow-latest-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use latest available active slate rows if requested date has no direct rows "
        "(default: enabled; --no-allow-latest-fallback publishes an empty pack instead of "
        "another slate's rows)",
    )
    parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="Halt with non-zero exit code if exported pack contains 0 candidate rows",
    )
    parser.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Disable automatic live prop pipeline trigger when sources are stale",
    )

    args = parser.parse_args()
    src = resolve_source(args.src, auto_refresh=not args.no_auto_refresh)
    out = Path(args.out)

    try:
        export_nba_pack(
            src,
            out,
            slate_date=args.date,
            min_edge_pct=args.min_edge,
            allow_latest_fallback=args.allow_latest_fallback,
            fail_on_empty=args.fail_on_empty,
        )
        return 0
    except Exception as exc:
        print(f"Error exporting NBA pack: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
