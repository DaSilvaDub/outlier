"""Export NFL high-prob props JSON into a thin nfl_only.csv extra pack.

Reads data/NFL/normalized/nfl_high_prob_props_{date}.json when --date is given
and the file exists, otherwise nfl_high_prob_props_latest.json. Always writes a
header even when there are zero rows.
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
]


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


def resolve_source(date: str | None) -> Path:
    if date:
        dated = NORMALIZED / f"nfl_high_prob_props_{date}.json"
        if dated.exists():
            return dated
    latest = NORMALIZED / "nfl_high_prob_props_latest.json"
    return latest


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
    }


def export_nfl_only(date: str | None = None, out_path: Path | None = None) -> tuple[Path, int]:
    src = resolve_source(date)
    out = out_path or OUT_CSV
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    if src.exists():
        try:
            payload = json.loads(src.read_text(encoding="utf-8"))
            recs = [
                r for r in records_from_payload(payload)
                if r.get("scope") in (None, "", "full_game")
            ]
            rows = [row_from_record(r) for r in recs]
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARNING: could not read {src}: {exc}", file=sys.stderr)
    else:
        print(f"WARNING: source JSON missing: {src}", file=sys.stderr)

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{out} ({len(rows)} rows)")
    return out, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=None,
        help="Prefer nfl_high_prob_props_{date}.json when present; else latest",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional output CSV path (default: data/NFL/exports/nfl_only.csv)",
    )
    args = parser.parse_args()
    out = Path(args.out) if args.out else None
    export_nfl_only(date=args.date, out_path=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
