"""Export NFL high-prob props JSON into a thin nfl_only.csv extra pack.

With --date (and optional --window) reads exactly
data/NFL/normalized/nfl_high_prob_props_{date}[_{window}].json and refuses
anything else (F25). Without --date reads nfl_high_prob_props_latest.json.

Exit codes (the CSV is not written on any nonzero exit):
  0  exported (a valid empty slate writes a header-only CSV and says so)
  2  the requested dated file does not exist (latest is never substituted)
  3  the source is unreadable, not JSON, or has no records list
  4  the payload's date/window does not match the request

These are this script's own codes. Exit 3 here (corrupt input) is unrelated to
the pipeline's exit 3 (a PARTIAL run with a failed required stage).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = REPO_ROOT / "data" / "NFL" / "normalized"
EXPORTS = REPO_ROOT / "data" / "NFL" / "exports"
OUT_CSV = EXPORTS / "nfl_only.csv"

FIELDNAMES = [
    "sport",
    "matchup",
    "team",
    "opponent",
    "player",
    "market",
    "market_raw",
    "position",
    "selection",
    "line",
    "price",
    "book",
    "confidence_tier",
    "implied_probability",
    "l5_hit_rate",
    "l10_hit_rate",
    "l20_hit_rate",
    "season_hit_rate",
    "event_starts_at",
    "scope",
    "is_consensus_line",
    "calibration_tags",
    # F25: immutable identity and explicit status (appended; earlier columns unchanged).
    "run_id",
    "event_id",
    "player_id",
    "approval_status",
]

EXIT_OK = 0
EXIT_MISSING = 2
EXIT_CORRUPT = 3
EXIT_MISMATCH = 4


class ExportError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _best_book(rec: dict[str, Any]) -> str:
    books = rec.get("books") or []
    best = rec.get("best_odds")
    if not isinstance(books, list):
        return ""
    for entry in books:
        if not isinstance(entry, dict):
            continue
        if best is not None and entry.get("odds") == best:
            return str(entry.get("book") or "")
    if books and isinstance(books[0], dict):
        return str(books[0].get("book") or "")
    return ""


def _selection(rec: dict[str, Any]) -> str:
    player = str(rec.get("player_name") or "").strip()
    market = str(rec.get("market") or rec.get("market_raw") or "").strip()
    scope = str(rec.get("scope") or "").strip()
    if scope and scope != "full_game":
        market = f"{market} ({scope})"
    position = str(rec.get("position") or "").strip()
    line = rec.get("line")
    line_s = "" if line is None else str(line)
    parts = [p for p in (player, market, position, line_s) if p]
    if player and (market or position or line_s):
        rest = " ".join(p for p in (market, position, line_s) if p)
        return f"{player} - {rest}" if rest else player
    return " ".join(parts)


def _tags(rec: dict[str, Any]) -> str:
    tags = rec.get("calibration_tags")
    if isinstance(tags, list):
        return ";".join(str(t) for t in tags if t is not None)
    if tags is None:
        return ""
    return str(tags)


def resolve_source(date: str | None, window: str | None = None) -> Path:
    """The exact file for ``date`` (and ``window``); latest only without a date."""
    if date:
        suffix = f"{date}_{window}" if window else date
        return NORMALIZED / f"nfl_high_prob_props_{suffix}.json"
    return NORMALIZED / "nfl_high_prob_props_latest.json"


def _approval_status(rec: dict[str, Any]) -> str:
    """From the correlation guard's ``actionable`` flag, never from the tier."""
    flag = rec.get("actionable")
    if flag is True:
        return "actionable"
    if flag is False:
        return "inventory"
    return "unlabeled"


def load_validated_payload(
    src: Path, date: str | None, window: str | None = None
) -> dict[str, Any]:
    if not src.exists():
        raise ExportError(EXIT_MISSING, f"ERROR: source JSON missing: {src}")
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(EXIT_CORRUPT, f"ERROR: could not read {src}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ExportError(EXIT_CORRUPT, f"ERROR: {src} has no records list")
    if date is not None:
        got_date = str(payload.get("date") or "")
        got_window = payload.get("window") or None
        if got_date != date or got_window != (window or None):
            raise ExportError(
                EXIT_MISMATCH,
                f"ERROR: {src} is date={got_date or '?'} window={got_window}, "
                f"requested date={date} window={window}",
            )
    return payload


def records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("records", "props", "items", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def row_from_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "sport": "NFL",
        "matchup": rec.get("matchup") or "",
        "team": rec.get("team") or "",
        "opponent": rec.get("opponent") or "",
        "player": rec.get("player_name") or "",
        "market": rec.get("market") or "",
        "market_raw": rec.get("market_raw") or "",
        "position": rec.get("position") or "",
        "selection": _selection(rec),
        "line": rec.get("line") if rec.get("line") is not None else "",
        "price": rec.get("best_odds") if rec.get("best_odds") is not None else "",
        "book": _best_book(rec),
        "confidence_tier": rec.get("confidence_tier") or "",
        "implied_probability": (
            rec.get("implied_probability")
            if rec.get("implied_probability") is not None
            else ""
        ),
        "l5_hit_rate": rec.get("l5_hit_rate") if rec.get("l5_hit_rate") is not None else "",
        "l10_hit_rate": rec.get("l10_hit_rate") if rec.get("l10_hit_rate") is not None else "",
        "l20_hit_rate": rec.get("l20_hit_rate") if rec.get("l20_hit_rate") is not None else "",
        "season_hit_rate": (
            rec.get("season_hit_rate") if rec.get("season_hit_rate") is not None else ""
        ),
        "event_starts_at": rec.get("event_starts_at") or "",
        "scope": rec.get("scope") or "",
        "is_consensus_line": (
            rec.get("is_consensus_line")
            if rec.get("is_consensus_line") is not None
            else ""
        ),
        "calibration_tags": _tags(rec),
        "run_id": rec.get("run_id") or "",
        "event_id": rec.get("event_id") or "",
        "player_id": rec.get("player_id") or "",
        "approval_status": _approval_status(rec),
    }


def export_nfl_only(
    date: str | None = None, out_path: Path | None = None, window: str | None = None
) -> tuple[Path, int]:
    """Write the CSV; raises ``ExportError`` (nothing written) on bad input."""
    src = resolve_source(date, window)
    out = out_path or OUT_CSV
    payload = load_validated_payload(src, date, window)
    run_id = payload.get("run_id") or ""
    recs = [
        {**r, "run_id": run_id}
        for r in records_from_payload(payload)
        if r.get("scope") in (None, "", "full_game")
    ]
    rows = [row_from_record(r) for r in recs]

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if not rows:
        print(f"NOTE: {src} is a valid empty slate (0 rows)", file=sys.stderr)
    print(f"{out} ({len(rows)} rows)")
    return out, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=None,
        help="Export exactly nfl_high_prob_props_{date}.json; never falls back to latest",
    )
    parser.add_argument(
        "--window",
        default=None,
        help="With --date, export the windowed file nfl_high_prob_props_{date}_{window}.json",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output CSV path (default: data/NFL/exports/nfl_only.csv)",
    )
    args = parser.parse_args()
    out = Path(args.out) if args.out else None
    if args.window and not args.date:
        parser.error("--window requires --date")
    try:
        export_nfl_only(date=args.date, out_path=out, window=args.window)
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
