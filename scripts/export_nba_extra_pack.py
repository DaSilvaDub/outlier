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
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from datetime import UTC, date, datetime, timedelta
import shutil
import subprocess  # nosec B404 - only runs fixed local pipeline scripts, never shell or user input
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
    "game_status",
    "event_status",
    "event_id",
    "game_id",
    "team",
    "opponent",
    "quote_timestamp",
    "odds_timestamp",
    "odds_as_of",
    "quote_as_of",
    "provider_event_id",
    "provider",
    "source_name",
    "bookmaker",
    "captured_at_utc",
    "quote_timestamp_basis",
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
                [python_exe, str(edges_script), "--no-rg"],
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


def _aware_utc(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(UTC)


def publication_rejection(
    row: dict[str, Any], slate_date: str | None, now: datetime | None = None
) -> str | None:
    """Return why this exact pregame quote cannot be published, or None.

    File modification times never establish quote validity. A provider quote
    timestamp is required; explicit stale flags always block publication.
    """
    cutoff = _aware_utc(now or datetime.now(UTC))
    if not slate_date or row.get("slate_date") != slate_date:
        return "wrong_slate"
    try:
        date.fromisoformat(slate_date)
        tip = _aware_utc(row.get("game_time"))
    except (ValueError, TypeError):
        return "unverified_game_time"
    if tip.astimezone(ZoneInfo("America/New_York")).date().isoformat() != slate_date:
        return "wrong_game_date"
    if tip <= cutoff:
        return "game_started"
    game_status = str(row.get("game_status") or row.get("event_status", "")).strip().upper()
    if game_status in {
        "LIVE",
        "IN_PROGRESS",
        "IN PROGRESS",
        "FINAL",
        "COMPLETED",
        "CANCELLED",
        "POSTPONED",
    }:
        return "game_unavailable"
    for key in ("stale", "odds_stale", "quote_stale", "is_stale"):
        if str(row.get(key, "")).strip().lower() in {"true", "1", "yes"}:
            return "stale_odds"
    if str(row.get("provider", "")).strip().casefold() == "bettingpros":
        return "consensus_price"
    verified_quote_time = False
    for key in ("quote_timestamp", "odds_timestamp", "odds_as_of", "quote_as_of"):
        if row.get(key):
            try:
                quote_time = _aware_utc(row[key])
            except (ValueError, TypeError):
                return "unverified_quote_time"
            if quote_time > cutoff or cutoff - quote_time > timedelta(hours=12):
                return "stale_odds"
            verified_quote_time = True
    if not verified_quote_time:
        return "awaiting_odds"
    try:
        line = float(row.get("line", ""))
        odds = float(row.get("recommended_odds", ""))
    except (TypeError, ValueError):
        return "awaiting_odds"
    side = str(row.get("recommended_side", "")).strip().upper()
    if side not in {"OVER", "UNDER"} or not math.isfinite(line):
        return "awaiting_odds"
    if not math.isfinite(odds) or abs(odds) < 100 or not odds.is_integer():
        return "awaiting_odds"
    # If both fields are supplied they must describe the same exact quote.
    side_price = row.get(f"{side.lower()}_odds")
    if side_price not in (None, ""):
        try:
            if float(side_price) != odds:
                return "price_mismatch"
        except (TypeError, ValueError):
            return "price_mismatch"
    return None


def publication_eligible(
    row: dict[str, Any], slate_date: str | None, now: datetime | None = None
) -> bool:
    return publication_rejection(row, slate_date, now) is None


def _atomic_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_nba_pack(
    src_path: Path | None,
    out_path: Path,
    slate_date: str | None = None,
    min_edge_pct: float | None = None,
    allow_latest_fallback: bool = False,
    fail_on_empty: bool = False,
    now: datetime | None = None,
) -> int:
    """Publish only verified quotes for the requested, unstarted Eastern slate.

    The deprecated fallback argument is accepted for older callers but cannot
    weaken the publication gate.
    """
    import io

    check_disk_space(min_gb=5.0)
    cutoff = _aware_utc(now or datetime.now(UTC))
    if not slate_date:
        slate_date = cutoff.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    date.fromisoformat(slate_date)
    raw_rows: list[dict[str, Any]] = []
    if src_path and src_path.exists():
        with open(src_path, mode="r", encoding="utf-8-sig", errors="replace") as stream:
            raw_rows = list(csv.DictReader(stream))

    rejected: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        reason = publication_rejection(row, slate_date, cutoff)
        if reason:
            rejected[reason] += 1
            continue
        if min_edge_pct is not None:
            try:
                edge = float(row.get("edge_pct", 0) or 0)
            except (ValueError, TypeError):
                rejected["invalid_edge"] += 1
                continue
            if not math.isfinite(edge) or edge < min_edge_pct:
                rejected["below_min_edge"] += 1
                continue
        rows.append({field: row.get(field, "") for field in FIELDNAMES})
    for index, row in enumerate(rows, start=1):
        row["rank"] = str(index)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    contents = buffer.getvalue()
    status = {
        "slate_date": slate_date,
        "as_of_utc": cutoff.isoformat(),
        "row_count": len(rows),
        "actionable_rows": len(rows),
        "status": "ready" if rows else "awaiting_odds",
        "source_rows": len(raw_rows),
        "rejected_counts": dict(rejected),
        "csv_sha256": hashlib.sha256(contents.encode("utf-8")).hexdigest(),
    }
    _atomic_text(out_path, contents)
    _atomic_text(out_path.with_suffix(".status.json"), json.dumps(status, indent=2) + "\n")
    print(f"Exported {len(rows)} rows to {out_path} (status={status['status']})")
    if not rows:
        warning = (
            f"[AWAITING ODDS] Export produced 0 candidate rows for date '{slate_date}'. "
            f"Source={src_path} had {len(raw_rows)} total rows; rejected={dict(rejected)}."
        )
        print(warning, file=sys.stderr)
        if fail_on_empty:
            raise RuntimeError(warning)
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
        default=False,
        help="Deprecated compatibility flag; other slate dates are always rejected",
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
