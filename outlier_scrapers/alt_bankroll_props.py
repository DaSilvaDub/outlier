"""Alt bankroll props L10 board (WNBA + MLB).

WNBA: full-game GAMELINE (Moneyline, Spread, Game Total) and TEAM_PROP lines
that cleared at least 75% over L5 and L10.

MLB: high-probability OVER game totals and team run totals only. Moneyline,
spread, and non-run team props are excluded from this board. Only prices from
Hard Rock, Fanatics, Midnite, DraftKings, or Novig, from -1000 through -110,
are included. Consumes the normalized games feed; no API calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import combinations
from typing import Any

from outlier_scrapers.game_totals import (
    _to_float,
    is_full_game_total,
    period_identity,
)
from outlier_scrapers.utils import (
    _american_to_decimal,
    _decimal_to_american,
    _local_date,
    _summary_stat_for_team,
    drop_locked_events,
)
from outlier_scrapers.normalizer import (
    ALLOWED_MLB_TEAM_PROPS,
    MLB_ALT_TEAM_OVER_MARKETS,
    implied_probability,
    percent_number,
)
from outlier_scrapers.paths import league_paths
from outlier_scrapers.registry import supported_leagues

MIN_L10_HIT_PCT = 75.0
MIN_L5_HIT_PCT = 75.0
MIN_AMERICAN_ODDS = -1000
MAX_AMERICAN_ODDS = -110
ALLOWED_GAMELINES = frozenset({"MONEYLINE", "SPREAD", "TOTAL"})

# Book-name variants collapse to these compact keys via "".join(alnum chars).lower().
# Hard Rock alone excluded many qualifying alt lines it simply doesn't carry
# but that Fanatics/Midnite/DraftKings/Novig do -- widened to these five so a
# real opportunity isn't missed just because one specific book skipped it.
# "Hardrock R" is deliberately NOT an alias here: it's a distinctly-named
# book in the raw feed, not a formatting variant of "Hard Rock" -- treating
# similarly-named books as identical would risk misattributing a price.
ALT_BANKROLL_ALLOWED_BOOKS = frozenset(
    {"hardrock", "fanatics", "midnite", "draftkings", "novig"}
)

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
    "home_l5_pct",
    "away_l5_pct",
    "home_l10_pct",
    "away_l10_pct",
    "best_book",
    "best_price",
    "decimal_price",
    "implied_prob",
    "include_overtime",
    "scope",
    "as_of",
]


def is_alt_bankroll_record(rec: dict[str, Any], *, league: str) -> bool:
    """Full-game supported GAMELINE or league-eligible TEAM_PROP record.

    MLB is OVER game-total / team-run-total only. WNBA keeps the broader
    moneyline + spread + total + team-prop board.
    """
    if not is_full_game_total(rec):
        return False
    mt = str(rec.get("market_type") or "").upper()
    proposition = str(rec.get("proposition") or rec.get("market") or "").upper()
    token = league.strip().upper()
    position = str(rec.get("position") or rec.get("side") or "").upper()
    if token == "MLB":
        if position != "OVER":
            return False
        if mt == "GAMELINE":
            return proposition == "TOTAL"
        if mt != "TEAM_PROP":
            return False
        market = str(rec.get("market") or proposition).strip().upper()
        return market in ALLOWED_MLB_TEAM_PROPS and market in MLB_ALT_TEAM_OVER_MARKETS
    if mt == "GAMELINE":
        return proposition in ALLOWED_GAMELINES
    if mt != "TEAM_PROP":
        return False
    return True


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
    """Extract exact recent hit rates for the record's team; fail closed.

    A game total depends on both teams' scoring, so each side's own recent
    rate is tracked separately rather than pooled into one blended figure —
    pooling can hide a weak team's rate behind a strong partner's (e.g. a
    90%/70% split pools to a passing 80%, even though the 70% side alone
    would fail the 75% bar). The reported l5_pct/l10_pct use the WEAKER
    (minimum) side so the strict gate reflects the shakier team, while
    home_l5_pct/away_l5_pct/home_l10_pct/away_l10_pct preserve the full
    per-team breakdown for transparency.
    """
    raw_stats = rec.get("stats")
    if not isinstance(raw_stats, dict):
        return None
    is_game_total = (
        str(rec.get("market_type") or "").upper() == "GAMELINE"
        and str(rec.get("proposition") or rec.get("market") or "").upper() == "TOTAL"
    )
    if is_game_total:
        home_blob = raw_stats.get("homeSummaryStat")
        away_blob = raw_stats.get("awaySummaryStat")
        home_stats = [home_blob] if isinstance(home_blob, dict) else []
        away_stats = [away_blob] if isinstance(away_blob, dict) else []
        home_hits, home_total, home_l5 = _window(home_stats, "l5")
        away_hits, away_total, away_l5 = _window(away_stats, "l5")
        home_l10_hits, home_l10_total, home_l10 = _window(home_stats, "l10")
        away_l10_hits, away_l10_total, away_l10 = _window(away_stats, "l10")
        if home_l5 is None or away_l5 is None or home_l10 is None or away_l10 is None:
            return None
        if home_l5 <= away_l5:
            l5_hits, l5_total, l5_pct = home_hits, home_total, home_l5
        else:
            l5_hits, l5_total, l5_pct = away_hits, away_total, away_l5
        if home_l10 <= away_l10:
            l10_hits, l10_total, l10_pct = home_l10_hits, home_l10_total, home_l10
        else:
            l10_hits, l10_total, l10_pct = away_l10_hits, away_l10_total, away_l10
        return {
            "l5_pct": l5_pct,
            "l10_pct": l10_pct,
            "l5_hits": l5_hits,
            "l5_total": l5_total,
            "l10_hits": l10_hits,
            "l10_total": l10_total,
            "home_l5_pct": home_l5,
            "away_l5_pct": away_l5,
            "home_l10_pct": home_l10,
            "away_l10_pct": away_l10,
        }
    stat, _flag = _summary_stat_for_team(rec)
    stats = [stat] if isinstance(stat, dict) else []
    if not stats:
        return None
    single_l5_hits, single_l5_total, single_l5_pct = _window(stats, "l5")
    single_l10_hits, single_l10_total, single_l10_pct = _window(stats, "l10")
    if single_l5_pct is None or single_l10_pct is None:
        return None
    return {
        "l5_pct": single_l5_pct,
        "l10_pct": single_l10_pct,
        "l5_hits": single_l5_hits,
        "l5_total": single_l5_total,
        "l10_hits": single_l10_hits,
        "l10_total": single_l10_total,
        "home_l5_pct": "",
        "away_l5_pct": "",
        "home_l10_pct": "",
        "away_l10_pct": "",
    }


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("−", "-"))
    except (TypeError, ValueError):
        return None


def _best_allowed_offer(rec: dict[str, Any]) -> tuple[str, int | float] | None:
    """Best (highest / least-negative) price among the allowed sportsbooks.

    Only considers offers that already fall within [MIN_AMERICAN_ODDS,
    MAX_AMERICAN_ODDS] -- picking the numerically-best price across all
    allowed books first and checking the window after could pick a
    non-qualifying book's price over a different book's qualifying one for
    the same row, silently dropping a real opportunity.
    """
    best: tuple[str, float] | None = None
    for offer in rec.get("books") or []:
        book_raw = str(offer.get("book") or offer.get("book_raw") or "").strip()
        compact = "".join(ch for ch in book_raw.lower() if ch.isalnum())
        if compact not in ALT_BANKROLL_ALLOWED_BOOKS:
            continue
        price = _number(
            offer.get("odds") if offer.get("odds") is not None else offer.get("odds_raw")
        )
        if price is None or not (MIN_AMERICAN_ODDS <= price <= MAX_AMERICAN_ODDS):
            continue
        display_book = "Hard Rock" if compact == "hardrock" else book_raw
        if best is None or price > best[1]:
            best = (display_book, price)
    if best is None:
        return None
    book, price = best
    return book, int(price) if price.is_integer() else price


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
        best_offer = _best_allowed_offer(rec)
        if best_offer is None:
            continue

        l_stats = extract_l5_l10(rec)
        if not l_stats:
            continue

        l5_pct = l_stats["l5_pct"]
        l10_pct = l_stats["l10_pct"]

        if not (MIN_L5_HIT_PCT <= l5_pct <= 100.0) or not (MIN_L10_HIT_PCT <= l10_pct <= 100.0):
            continue

        line = _to_float(rec.get("line"))
        best_book, best_price = best_offer
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
                "home_l5_pct": l_stats.get("home_l5_pct", ""),
                "away_l5_pct": l_stats.get("away_l5_pct", ""),
                "home_l10_pct": l_stats.get("home_l10_pct", ""),
                "away_l10_pct": l_stats.get("away_l10_pct", ""),
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


ALT_BANKROLL_PARLAYS_HEADER = [
    "league",
    "event_starts_at",
    "type",
    "leg_1_label",
    "leg_1_position",
    "leg_1_line",
    "leg_1_odds",
    "leg_2_label",
    "leg_2_position",
    "leg_2_line",
    "leg_2_odds",
    "parlay_odds",
]


def _bankroll_leg_label(row: dict[str, Any]) -> str:
    team = str(row.get("team") or "").strip()
    matchup = str(row.get("matchup") or "").strip()
    proposition = str(row.get("proposition") or row.get("market") or "").strip().upper()
    market_type = str(row.get("market_type") or "").strip().upper()
    if market_type == "GAMELINE" and proposition == "TOTAL":
        return f"{matchup or team} GAME TOTAL"
    return f"{team or matchup} {proposition or 'TEAM TOTAL'}"


def build_alt_bankroll_parlays(board: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-game 2-leg OVER parlays from high-probability MLB totals.

    One qualifying OVER per event (highest L10, then longest favorite).
    WNBA rows are ignored — this parlay surface is the MLB alt-over totals
    product only.
    """
    by_event: dict[str, dict[str, Any]] = {}
    for row in board:
        if str(row.get("league") or "").upper() != "MLB":
            continue
        if str(row.get("position") or "").upper() != "OVER":
            continue
        if row.get("decimal_price") in (None, ""):
            continue
        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            continue
        current = by_event.get(event_id)
        if current is None:
            by_event[event_id] = row
            continue
        if float(row["l10_pct"]) > float(current["l10_pct"]) or (
            float(row["l10_pct"]) == float(current["l10_pct"])
            and float(row["best_price"]) > float(current["best_price"])
        ):
            by_event[event_id] = row

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in by_event.values():
        day = _local_date(row.get("event_starts_at"))
        if day is None:
            continue
        by_day.setdefault(day, []).append(row)

    parlays: list[dict[str, Any]] = []
    for day, day_rows in by_day.items():
        for leg1, leg2 in combinations(day_rows, 2):
            if leg1.get("event_id") == leg2.get("event_id"):
                continue
            dec1 = _american_to_decimal(leg1.get("best_price"))
            dec2 = _american_to_decimal(leg2.get("best_price"))
            if dec1 is None or dec2 is None:
                continue
            parlay_decimal = dec1 * dec2
            parlays.append(
                {
                    "league": "MLB",
                    "event_starts_at": day,
                    "type": "Cross-Game",
                    "leg_1_label": _bankroll_leg_label(leg1),
                    "leg_1_position": leg1["position"],
                    "leg_1_line": leg1["line"],
                    "leg_1_odds": leg1["best_price"],
                    "leg_2_label": _bankroll_leg_label(leg2),
                    "leg_2_position": leg2["position"],
                    "leg_2_line": leg2["line"],
                    "leg_2_odds": leg2["best_price"],
                    "parlay_odds": _decimal_to_american(parlay_decimal),
                    "_sort_decimal": parlay_decimal,
                    "_l10_avg": (float(leg1["l10_pct"]) + float(leg2["l10_pct"])) / 2.0,
                }
            )

    parlays.sort(key=lambda row: (row["_l10_avg"], -row["_sort_decimal"]), reverse=True)
    for row in parlays:
        row.pop("_sort_decimal", None)
        row.pop("_l10_avg", None)
    return parlays


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
