"""Dedicated alternate-spread board for MLB and WNBA.

This is intentionally a thin specialization of the existing alt-bankroll
board so its active/pregame, full-game, book, odds, and L5/L10 policies cannot
drift. It consumes normalized games feeds only; no API calls are made.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from typing import Any

from outlier_scrapers.alt_bankroll_props import (
    ALT_BANKROLL_PROPS_HEADER,
    build_alt_bankroll_board,
)
from outlier_scrapers.paths import league_paths
from outlier_scrapers.registry import supported_leagues


ALT_SPREADS_HEADER = [*ALT_BANKROLL_PROPS_HEADER, "signed_line", "selection"]


def _line_number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(str(value).replace("−", "-").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _signed_line(value: Any) -> str | None:
    number = _line_number(value)
    if number is None:
        return None
    if abs(number) < 1e-12:
        return "0"
    rendered = f"{abs(number):g}"
    return f"+{rendered}" if number > 0 else f"-{rendered}"


def _spread_identity_is_valid(row: dict[str, Any]) -> bool:
    if str(row.get("market_type") or "").strip().upper() != "GAMELINE":
        return False
    if str(row.get("proposition") or "").strip().upper() != "SPREAD":
        return False

    required = ("event_id", "event_starts_at", "market_id", "outcome_id", "team")
    if any(not str(row.get(key) or "").strip() for key in required):
        return False

    position = str(row.get("position") or "").strip().upper()
    if position not in {"HOME", "AWAY"}:
        return False

    team = str(row.get("team") or "").strip()
    matchup = str(row.get("matchup") or "").strip()
    if " @ " not in matchup:
        return False
    away_team, _, home_team = matchup.partition(" @ ")
    expected_team = home_team.strip() if position == "HOME" else away_team.strip()
    return bool(expected_team) and team.casefold() == expected_team.casefold()


def build_alt_spread_rows(bankroll_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add explicit identity fields to valid spread rows, dropping ambiguous rows."""
    rows: list[dict[str, Any]] = []
    for row in bankroll_rows:
        if not _spread_identity_is_valid(row):
            continue
        signed_line = _signed_line(row.get("line"))
        if signed_line is None:
            continue
        spread = dict(row)
        spread["signed_line"] = signed_line
        spread["selection"] = f"{str(row['team']).strip()} {signed_line}"
        rows.append(spread)
    return rows


def build_alt_spreads_board(
    games_norm: dict[str, Any] | None,
    *,
    league: str,
    now: datetime | None = None,
    target_date: str | None = None,
) -> list[dict[str, Any]]:
    """Return strict, spread-only rows from the shared alt-bankroll board.

    MLB alt spreads are retired: the baseball product is pitcher Ks plus
    over game/team totals. WNBA still uses this board.
    """
    if str(league or "").strip().upper() == "MLB":
        return []
    bankroll_rows = build_alt_bankroll_board(
        games_norm,
        league=league,
        now=now,
        target_date=target_date,
    )
    return build_alt_spread_rows(bankroll_rows)


def export_alt_spreads_for_league(
    league: str, *, target_date: str | None = None
) -> dict[str, Any]:
    token = league.strip().upper()
    target_date = target_date or datetime.now().astimezone().strftime("%Y-%m-%d")
    paths = league_paths(token).ensure()
    games_path = paths.games_normalized_latest()
    if not games_path.exists():
        raise FileNotFoundError(f"missing normalized games feed: {games_path}")
    games_norm = json.loads(games_path.read_text("utf-8"))
    rows = build_alt_spreads_board(games_norm, league=token, target_date=target_date)
    return {
        "league": token,
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "target_date": target_date,
        "games_normalized_latest": str(games_path),
        "record_count": len(rows),
        "rows": rows,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dedicated alternate-spread board")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument("--date", help="Slate date (local YYYY-MM-DD); defaults to today.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.league.strip().upper()
    try:
        status = export_alt_spreads_for_league(token, target_date=args.date)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"{token}: error ({exc})")
        return 1
    print(f"{status['league']}: {status['record_count']} qualifying alternate spreads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
