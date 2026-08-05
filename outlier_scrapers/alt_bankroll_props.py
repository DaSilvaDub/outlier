"""Alt bankroll props L10 board (WNBA + MLB).

Lists full-game GAMELINE (Moneyline, Spread, Game Total) and eligible TEAM_PROP
lines that the team has cleared 100% of the time over its last 5 games and at
least 90% of the time over its last 10 games. Only Hard Rock prices from -1000
through -500 are included. Consumes the normalized games feed; no API calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from outlier_scrapers.game_totals import (
    _to_float,
    is_full_game_total,
    period_identity,
)
from outlier_scrapers.utils import (
    _american_to_decimal,
    _local_date,
    _summary_stat_for_team,
    drop_locked_events,
)
from outlier_scrapers.normalizer import (
    ALLOWED_MLB_TEAM_PROPS,
    implied_probability,
    percent_number,
)
from outlier_scrapers.paths import league_paths
from outlier_scrapers.registry import supported_leagues

MIN_L10_HIT_PCT = 90.0
MIN_L5_HIT_PCT = 100.0
MIN_AMERICAN_ODDS = -1000
MAX_AMERICAN_ODDS = -500
ALLOWED_GAMELINES = frozenset({"MONEYLINE", "SPREAD", "TOTAL"})

ALT_BANKROLL_PROPS_HEADER = [
    "league",
    "event_id",
    "event_starts_at",
    "matchup",
    "team",
    "market_type",
    "proposition",
    "position",
    "market_id",
    "outcome_id",
    "line",
    "l5_hits",
    "l5_total",
    "l5_pct",
    "l10_hits",
    "l10_total",
    "l10_pct",
    "best_book",
    "best_price",
    "decimal_price",
    "implied_prob",
    "include_overtime",
    "scope",
    "as_of",
]


def is_alt_bankroll_record(rec: dict[str, Any], *, league: str) -> bool:
    """Full-game supported GAMELINE or league-eligible TEAM_PROP record."""
    if not is_full_game_total(rec):
        return False
    mt = str(rec.get("market_type") or "").upper()
    proposition = str(rec.get("proposition") or rec.get("market") or "").upper()
    if mt == "GAMELINE":
        return proposition in ALLOWED_GAMELINES
    if mt != "TEAM_PROP":
        return False
    if league.strip().upper() != "MLB":
        return True
    market = str(rec.get("market") or "").strip().upper()
    return market in ALLOWED_MLB_TEAM_PROPS


def _window(
    stats: list[dict[str, Any]], name: str
) -> tuple[int | str, int | str, float | None]:
    observations: list[bool] = []
    all_have_results = True
    percentages: list[float] = []
    for stat in stats:
        results = stat.get(f"{name}Results")
        bools = [value for value in results or [] if isinstance(value, bool)]
        if bools:
            observations.extend(bools)
            percentages.append(100.0 * sum(bools) / len(bools))
            continue
        all_have_results = False
        pct = percent_number(stat.get(name))
        if pct is not None:
            percentages.append(float(pct))
    if not percentages:
        return "", "", None
    if all_have_results and observations:
        return (
            sum(observations),
            len(observations),
            round(100.0 * sum(observations) / len(observations), 3),
        )
    return "", "", round(sum(percentages) / len(percentages), 3)


def extract_l5_l10(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Extract exact recent hit rates for the record's team; fail closed."""
    raw_stats = rec.get("stats")
    if not isinstance(raw_stats, dict):
        return None
    is_game_total = (
        str(rec.get("market_type") or "").upper() == "GAMELINE"
        and str(rec.get("proposition") or rec.get("market") or "").upper() == "TOTAL"
    )
    if is_game_total:
        stats = [
            blob
            for blob in (
                raw_stats.get("homeSummaryStat"),
                raw_stats.get("awaySummaryStat"),
            )
            if isinstance(blob, dict)
        ]
    else:
        stat, _flag = _summary_stat_for_team(rec)
        stats = [stat] if isinstance(stat, dict) else []
    if not stats:
        return None
    l5_hits, l5_total, l5_pct = _window(stats, "l5")
    l10_hits, l10_total, l10_pct = _window(stats, "l10")
    if l5_pct is None or l10_pct is None:
        return None
    return {
        "l5_pct": l5_pct,
        "l10_pct": l10_pct,
        "l5_hits": l5_hits,
        "l5_total": l5_total,
        "l10_hits": l10_hits,
        "l10_total": l10_total,
    }


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("−", "-"))
    except (TypeError, ValueError):
        return None


def _hard_rock_offer(rec: dict[str, Any]) -> tuple[str, int | float] | None:
    for offer in rec.get("books") or []:
        book = str(offer.get("book") or offer.get("book_raw") or "").strip()
        if "".join(ch for ch in book.lower() if ch.isalnum()) != "hardrock":
            continue
        price = _number(
            offer.get("odds") if offer.get("odds") is not None else offer.get("odds_raw")
        )
        if price is None:
            return None
        return "Hard Rock", int(price) if price.is_integer() else price
    return None


def _event_started(rec: dict[str, Any], now: datetime) -> bool:
    kept, _dropped = drop_locked_events(
        [
            {
                "event_id": rec.get("event_id"),
                "_event_starts_at": rec.get("event_starts_at"),
            }
        ],
        now=now,
    )
    return not kept


def build_alt_bankroll_board(
    games_norm: dict[str, Any] | None,
    *,
    league: str,
    now: datetime | None = None,
    target_date: str | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now().astimezone()
    records = (games_norm or {}).get("records") or []
    as_of = (games_norm or {}).get("generated_at") or ""

    rows: list[dict[str, Any]] = []
    for rec in records:
        token = league.strip().upper()
        if str(rec.get("league") or token).strip().upper() != token:
            continue
        if not is_alt_bankroll_record(rec, league=token):
            continue
        if not str(rec.get("event_id") or "").strip():
            continue
        if rec.get("is_active") is False:
            continue
        if target_date is not None and _local_date(rec.get("event_starts_at")) != target_date:
            continue
        if _event_started(rec, now):
            continue
        hard_rock_offer = _hard_rock_offer(rec)
        if hard_rock_offer is None:
            continue

        l_stats = extract_l5_l10(rec)
        if not l_stats:
            continue

        l5_pct = l_stats["l5_pct"]
        l10_pct = l_stats["l10_pct"]

        if l5_pct != MIN_L5_HIT_PCT or not (MIN_L10_HIT_PCT <= l10_pct <= 100.0):
            continue

        line = _to_float(rec.get("line"))
        best_book, best_price = hard_rock_offer
        if not (MIN_AMERICAN_ODDS <= float(best_price) <= MAX_AMERICAN_ODDS):
            continue
        decimal_price = _american_to_decimal(best_price)

        rows.append(
            {
                "league": token,
                "event_id": str(rec.get("event_id") or ""),
                "event_starts_at": rec.get("event_starts_at") or "",
                "matchup": rec.get("matchup") or rec.get("matchup_raw") or "",
                "team": rec.get("team") or rec.get("team_raw") or "",
                "market_type": rec.get("market_type") or "",
                "proposition": rec.get("proposition") or rec.get("market") or "",
                "position": rec.get("position") or "",
                "market_id": str(rec.get("market_id") or ""),
                "outcome_id": str(rec.get("outcome_id") or ""),
                "line": line if line is not None else "",
                "l5_hits": l_stats.get("l5_hits"),
                "l5_total": l_stats.get("l5_total"),
                "l5_pct": l5_pct,
                "l10_hits": l_stats.get("l10_hits"),
                "l10_total": l_stats.get("l10_total"),
                "l10_pct": l10_pct,
                "best_book": best_book,
                "best_price": best_price if best_price is not None else "",
                "decimal_price": round(decimal_price, 4) if decimal_price is not None else "",
                "implied_prob": implied_probability(best_price) if best_price not in (None, "") else "",
                "include_overtime": rec.get("include_overtime") if rec.get("include_overtime") is not None else "",
                "scope": period_identity(rec),
                "as_of": as_of,
            }
        )

    rows.sort(key=lambda r: (r["event_id"], str(r["team"]).lower(), str(r["proposition"])))
    return rows


def export_alt_bankroll_props_for_league(
    league: str, *, target_date: str | None = None
) -> dict[str, Any]:
    token = league.strip().upper()
    target_date = target_date or datetime.now().astimezone().strftime("%Y-%m-%d")
    paths = league_paths(token).ensure()
    games_path = paths.games_normalized_latest()
    if not games_path.exists():
        raise FileNotFoundError(f"missing normalized games feed: {games_path}")
    games_norm = json.loads(games_path.read_text("utf-8"))

    rows = build_alt_bankroll_board(games_norm, league=token, target_date=target_date)

    status = {
        "league": token,
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "target_date": target_date,
        "games_normalized_latest": str(games_path),
        "record_count": len(rows),
        "rows": rows,
    }
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alt bankroll props L10 board")
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument("--date", help="Slate date (local YYYY-MM-DD); defaults to today.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.league.strip().upper()
    try:
        status = export_alt_bankroll_props_for_league(token, target_date=args.date)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"{token}: error ({exc})")
        return 1
    print(f"{status['league']}: {status['record_count']} qualifying alt bankroll lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
