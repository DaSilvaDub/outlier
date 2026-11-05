"""Export fresh, nonempty NBA producer rows for exactly one Eastern slate date.

The upstream explicit slate_date is authoritative. Offset-aware game timestamps
must agree in America/New_York. Legacy naive display timestamps do not override
that date. Never launch producers implicitly or recycle an nba_only.csv export.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "NBA" / "exports" / "nba_only.csv"
NBA_PIPELINE_REPO = Path(r"C:\Users\dasil\Dev\GitHub\nba-props-pipeline")
ET = ZoneInfo("America/New_York")
MAX_AGE_HOURS = 12.0
FIELDNAMES = [
    "rank", "slate_date", "matchup", "matchup_abbr", "player", "market", "line",
    "rg_projection", "rg_minutes", "rg_opp", "shotquality_projection", "sq_agreement",
    "baseline_source", "recommended_side", "recommended_odds", "over_odds", "under_odds",
    "edge_value", "edge_pct", "projection_sources", "game_time",
]
CANDIDATE_SOURCES = [
    Path(r"G:\My Drive\Sports_Analytics\01_Active\NBA_Active\PROP EDGES\prop_edges_latest.csv"),
    Path(r"C:\Users\dasil\Dev\GitHub\Sports_Analytics\Sports_Analytics\01_Active\NBA_Active\PROP EDGES\prop_edges_latest.csv"),
    NBA_PIPELINE_REPO / "output" / "PROP EDGES" / "prop_edges_latest.csv",
    NBA_PIPELINE_REPO / "output" / "MATCHUP" / "PROP EDGES" / "prop_edges_latest.csv",
]


class ExportError(RuntimeError):
    """No admissible source; output must remain unchanged."""


def slate_day(value: str | None, now: datetime | None = None) -> str:
    if value is None:
        return (now or datetime.now(timezone.utc)).astimezone(ET).date().isoformat()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise ExportError(f"Invalid slate date: {value}") from exc
    if parsed.isoformat() != value:
        raise ExportError(f"Invalid slate date: {value}")
    return value


def check_disk_space(min_gb: float = 20.0) -> None:
    target = Path("C:/") if os.name == "nt" else REPO_ROOT
    if shutil.disk_usage(target).free <= min_gb * 1024**3:
        raise ExportError(f"Insufficient disk space: more than {min_gb:g} GiB required")


def _fresh(timestamp: datetime, now: datetime, max_age_hours: float) -> bool:
    age = now - timestamp.astimezone(timezone.utc)
    return -timedelta(minutes=5) <= age <= timedelta(hours=max_age_hours)


def is_source_stale(path: Path, max_age_hours: float = MAX_AGE_HOURS) -> bool:
    try:
        return not _fresh(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                          datetime.now(timezone.utc), max_age_hours)
    except OSError:
        return True


def read_source(path: Path, date: str, *, now: datetime | None = None,
                max_age_hours: float = MAX_AGE_HOURS) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    if path.resolve() == DEFAULT_OUT.resolve() or path.name.lower() == "nba_only.csv":
        raise ExportError("An exported nba_only.csv cannot serve as a producer source")
    before = path.stat()
    if not path.is_file() or not _fresh(datetime.fromtimestamp(before.st_mtime, timezone.utc),
                                      now, max_age_hours):
        raise ExportError(f"Missing, stale or future-dated source: {path}")
    # Honor producer time when available: copying a CSV must not refresh its age.
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        metadata = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict):
            raise ExportError(f"Invalid producer metadata: {sidecar}")
        generated = datetime.fromisoformat(str(metadata.get("generated_at", "")).replace("Z", "+00:00"))
        if generated.tzinfo is None or not _fresh(generated, now, max_age_hours):
            raise ExportError(f"Stale or ambiguous producer timestamp: {sidecar}")
        if "hardrock_fetched_at" in metadata:
            try:
                quoted = datetime.fromisoformat(
                    str(metadata["hardrock_fetched_at"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError) as exc:
                raise ExportError(f"Malformed hardrock_fetched_at timestamp: {sidecar}") from exc
            if quoted.tzinfo is None or not _fresh(quoted, now, max_age_hours):
                raise ExportError(f"Stale, future or ambiguous hardrock_fetched_at: {sidecar}")
        if metadata.get("slate_date") != date:
            raise ExportError(f"Producer slate-date mismatch: {sidecar}")
    payload = path.read_text(encoding="utf-8-sig")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ExportError(f"Source changed while reading: {path}")
    reader = csv.DictReader(io.StringIO(payload), strict=True)
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not {"slate_date", "player", "market", "line"} <= set(fields):
        raise ExportError(f"Missing or duplicate source columns: {path}")
    selected = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ExportError(f"Malformed source CSV row: {path}")
        row_date = slate_day(row["slate_date"].strip())
        if row_date != date:
            continue
        if not row["player"].strip() or not row["market"].strip():
            raise ExportError(f"Missing player/market in source: {path}")
        try:
            if not math.isfinite(float(row["line"])):
                raise ValueError("nonfinite line")
        except ValueError as exc:
            raise ExportError(f"Invalid market line in source: {path}") from exc
        game_time = row.get("game_time", "").strip()
        if len(game_time) >= 10 and game_time[:4].isdigit() and game_time[4:5] == "-":
            tip = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
            if tip.tzinfo is not None and tip.astimezone(ET).date().isoformat() != row_date:
                raise ExportError(f"Game timestamp disagrees with Eastern slate date: {path}")
        row["slate_date"] = row_date
        selected.append(row)
    if not selected:
        raise ExportError(f"0 matching rows for Eastern slate {date}: {path}")
    return selected


def resolve_source(explicit_src: str | None = None, auto_refresh: bool = False,
                   slate_date: str | None = None, *, candidates: list[Path] | None = None) -> Path:
    if auto_refresh:
        raise ExportError("Exporter cannot launch producers; run checked orchestration explicitly")
    date = slate_day(slate_date)
    errors = []
    paths = [Path(explicit_src)] if explicit_src else (candidates if candidates is not None else CANDIDATE_SOURCES)
    for path in paths:
        try:
            read_source(path, date)
            return path
        except (OSError, ValueError, csv.Error, ExportError) as exc:
            errors.append(f"{path}: {exc}")
    raise ExportError("No fresh nonempty exact-date NBA source. " + "; ".join(errors))


def export_nba_pack(src_path: Path | None, out_path: Path, slate_date: str | None = None,
                    min_edge_pct: float | None = None, allow_latest_fallback: bool = False,
                    fail_on_empty: bool = True) -> int:
    del fail_on_empty  # Compatibility only: empty output always fails.
    if allow_latest_fallback:
        raise ExportError("Cross-date latest fallback is prohibited")
    date = slate_day(slate_date)
    if src_path is None or src_path.resolve() == out_path.resolve():
        raise ExportError("Missing source or source equals output")
    rows = read_source(src_path, date)
    if min_edge_pct is not None:
        if not math.isfinite(min_edge_pct) or min_edge_pct < 0:
            raise ExportError("Minimum edge must be finite and nonnegative")
        try:
            rows = [row for row in rows if math.isfinite(float(row.get("edge_pct", "")))
                    and abs(float(row["edge_pct"])) >= min_edge_pct]
        except ValueError as exc:
            raise ExportError("Missing or malformed requested edge metric") from exc
    if not rows:
        raise ExportError(f"0 matching rows for Eastern slate {date}")
    check_disk_space()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=out_path.parent, prefix=".nba-export-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
            writer.writeheader()
            for rank, row in enumerate(rows, start=1):
                output = {key: row.get(key, "") for key in FIELDNAMES}
                output["rank"] = str(rank)
                writer.writerow(output)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(out_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f"Exported {len(rows)} exact-date rows for {date} from {src_path} to {out_path}")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Eastern slate YYYY-MM-DD (default: today ET)")
    parser.add_argument("--src", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--min-edge", type=float, default=None)
    parser.add_argument("--allow-latest-fallback", action="store_true", help="Rejected: cross-date fallback is unsafe")
    parser.add_argument("--fail-on-empty", action="store_true", help="Compatibility: empty always fails")
    parser.add_argument("--no-auto-refresh", action="store_true", help="Compatibility: producers never invoked")
    args = parser.parse_args(argv)
    try:
        if args.allow_latest_fallback:
            raise ExportError("Cross-date latest fallback is prohibited")
        date = slate_day(args.date)
        source = resolve_source(args.src, slate_date=date)
        export_nba_pack(source, Path(args.out), date, args.min_edge)
        return 0
    except (ExportError, OSError, ValueError, csv.Error) as exc:
        print(f"Error exporting NBA pack: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
