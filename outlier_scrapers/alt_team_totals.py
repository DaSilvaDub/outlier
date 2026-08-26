"""Alt team-total L10 board + parlay suggestions (WNBA + MLB).

Lists every full-game team-total OVER alt line that the team has cleared
90-100% of the time over its last 10 games (Outlier's per-line ``l10`` /
``l10Results`` stats), then suggests 2-leg parlays from the best qualifying
line per team. Legs sharing an event are flagged as SGP. Consumes the
normalized games feed; no API calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from itertools import combinations
from typing import Any

from outlier_scrapers.game_totals import (
    _best_book_offer,
    _to_float,
    build_market_ladder,
    is_full_game_total,
    logical_market_key,
    period_identity,
)
from outlier_scrapers.utils import (
    _american_to_decimal,
    _decimal_to_american,
    _local_date,
    _summary_stat_for_team,
    drop_locked_events,
    _write_csv,
    _price_text,
)
from outlier_scrapers.normalizer import implied_probability, percent_number
from outlier_scrapers.parlay_legs import encode_legs
from outlier_scrapers.paths import league_paths
from outlier_scrapers.registry import supported_leagues
from outlier_scrapers.team_totals import (
    is_team_total_proposition,
    team_total_propositions as _team_total_propositions,
)

MIN_HIT_PCT = 90.0
MAX_HIT_PCT = 100.0
PARLAY_LEGS = 2

ALT_TEAM_TOTALS_HEADER = [
    "league",
    "event_id",
    "event_starts_at",
    "matchup",
    "team",
    "market_id",
    "outcome_id",
    # The board is OVER-only by construction (see the position filter in
    # alt_team_total_rows).  Recording the side explicitly keeps a downstream
    # consumer -- notably feedback capture, which has to build a gradeable
    # selection -- from having to assume it.
    "position",
    "line",
    "l10_hits",
    "l10_total",
    "l10_pct",
    "l10_source",
    "best_book",
    "best_price",
    "decimal_price",
    "implied_prob",
    "books_count",
    "is_best_line",
    "include_overtime",
    "scope",
    "quality_flags",
    "as_of",
    "model_prob",
    "edge_pct",
    "recommended_units",
]

ALT_TEAM_TOTAL_PARLAYS_HEADER = [
    "parlay_id",
    "league",
    "num_legs",
    "legs",
    # Machine-readable leg identity; "legs" above is display text only.
    "legs_json",
    "event_ids",
    "is_sgp",
    "combined_decimal",
    "combined_american",
    "combined_implied_prob",
    "naive_l10_prob",
    "quality_flags",
    "as_of",
    "model_prob",
    "edge_pct",
    "recommended_units",
]

FLAG_SHORT_SAMPLE = "SHORT_SAMPLE"
FLAG_AMBIGUOUS_STATS_SIDE = "AMBIGUOUS_STATS_SIDE"
FLAG_INTEGER_LINE_PUSH_RISK = "INTEGER_LINE_PUSH_RISK"
FLAG_NO_PRICE = "NO_PRICE"


def team_total_propositions(league: str) -> frozenset[str]:
    """Backward-compatible access to the shared sport-aware token contract."""
    return _team_total_propositions(league)


def is_alt_team_total_record(rec: dict[str, Any], *, league: str) -> bool:
    """Full-game TEAM_PROP over/under rows for the league's team-total props."""
    if not is_full_game_total(rec):
        return False
    mt = str(rec.get("market_type") or "").upper()
    if mt != "TEAM_PROP":
        return False
    prop = rec.get("proposition") or rec.get("market")
    return is_team_total_proposition(prop, sport=league)


def extract_l10(rec: dict[str, Any]) -> dict[str, Any] | None:
    """L10 hit rate for one OVER outcome at its line.

    Prefers the exact ``l10Results`` boolean array; falls back to the ``l10``
    fraction. Returns None when no usable l10 signal exists.
    """
    stat, flag = _summary_stat_for_team(rec)
    if stat is None:
        return {"flag": flag} if flag else None
    results = stat.get("l10Results")
    if isinstance(results, list) and results:
        bools = [bool(v) for v in results]
        hits, total = sum(bools), len(bools)
        return {
            "hits": hits,
            "total": total,
            "pct": round(100.0 * hits / total, 3),
            "source": "l10Results",
            "flag": FLAG_SHORT_SAMPLE if total < 10 else None,
        }
    pct = percent_number(stat.get("l10"))
    if pct is None:
        return None
    return {"hits": None, "total": None, "pct": pct, "source": "l10", "flag": None}


def _is_integer_line(line: float) -> bool:
    return line == int(line)


def _event_started(rec: dict[str, Any], now: datetime) -> bool:
    """Pregame-only house rule: unverifiable start times count as started."""
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


def build_alt_team_total_board(
    games_norm: dict[str, Any] | None,
    *,
    league: str,
    now: datetime | None = None,
    target_date: str | None = None,
    min_hit_pct: float = MIN_HIT_PCT,
    max_hit_pct: float = MAX_HIT_PCT,
) -> list[dict[str, Any]]:
    """All qualifying alt team-total OVER lines for the slate.

    One row per (team-total market, line) whose l10 hit rate falls inside
    [min_hit_pct, max_hit_pct]; the highest qualifying line per logical market
    is stamped ``is_best_line=true``. ``target_date`` (local ``YYYY-MM-DD``)
    bounds the board to that slate — the games feed can span multiple days.
    Inactive markets are dropped fail-closed.
    """
    now = now or datetime.now().astimezone()
    records = (games_norm or {}).get("records") or []
    as_of = (games_norm or {}).get("generated_at") or ""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_alt_team_total_record(rec, league=league):
            continue
        if not str(rec.get("event_id") or "").strip():
            continue
        if not str(rec.get("team") or rec.get("team_raw") or "").strip():
            continue
        if rec.get("is_active") is False:
            continue
        if (
            target_date is not None
            and _local_date(rec.get("event_starts_at")) != target_date
        ):
            continue
        grouped.setdefault(logical_market_key(rec), []).append(rec)

    rows: list[dict[str, Any]] = []
    for market_records in grouped.values():
        identity = market_records[0]
        if _event_started(identity, now):
            continue
        ladder = build_market_ladder(market_records)
        overs = [
            r for r in market_records if str(r.get("position") or "").upper() == "OVER"
        ]

        qualifying: list[dict[str, Any]] = []
        seen_lines: set[float] = set()
        for rec in sorted(overs, key=lambda r: _to_float(r.get("line")) or 0.0):
            line = _to_float(rec.get("line"))
            if line is None or line in seen_lines:
                continue
            l10 = extract_l10(rec)
            if l10 is None or l10.get("pct") is None:
                continue
            pct = float(l10["pct"])
            if not (min_hit_pct <= pct <= max_hit_pct):
                seen_lines.add(line)
                continue
            seen_lines.add(line)

            flags = [l10["flag"]] if l10.get("flag") else []
            if _is_integer_line(line):
                flags.append(FLAG_INTEGER_LINE_PUSH_RISK)
            over_books = (ladder.get(line) or {}).get("over", {})
            best_book, best_price = _best_book_offer(over_books)
            decimal_price = _american_to_decimal(best_price)
            if decimal_price is None:
                flags.append(FLAG_NO_PRICE)

            qualifying.append(
                {
                    "league": league,
                    "event_id": str(identity.get("event_id") or ""),
                    "event_starts_at": identity.get("event_starts_at") or "",
                    "matchup": identity.get("matchup")
                    or identity.get("matchup_raw")
                    or "",
                    "team": identity.get("team") or identity.get("team_raw") or "",
                    "market_id": str(rec.get("market_id") or ""),
                    "outcome_id": str(rec.get("outcome_id") or ""),
                    "position": "OVER",
                    "line": line,
                    "l10_hits": l10.get("hits") if l10.get("hits") is not None else "",
                    "l10_total": l10.get("total")
                    if l10.get("total") is not None
                    else "",
                    "l10_pct": pct,
                    "l10_source": l10.get("source") or "",
                    "best_book": best_book,
                    "best_price": best_price if best_price is not None else "",
                    "decimal_price": round(decimal_price, 4)
                    if decimal_price is not None
                    else "",
                    "implied_prob": implied_probability(best_price)
                    if best_price not in (None, "")
                    else "",
                    "books_count": len(over_books),
                    "is_best_line": "false",
                    "include_overtime": identity.get("include_overtime")
                    if identity.get("include_overtime") is not None
                    else "",
                    "scope": period_identity(identity),
                    "quality_flags": ",".join(dict.fromkeys(flags)),
                    "as_of": as_of,
                }
            )

        if qualifying:
            max(qualifying, key=lambda r: r["line"])["is_best_line"] = "true"
            rows.extend(qualifying)

    rows.sort(key=lambda r: (r["event_id"], str(r["team"]).lower(), -float(r["line"])))
    return rows


PARLAY_BLOCKING_FLAGS = (
    FLAG_INTEGER_LINE_PUSH_RISK,
    FLAG_NO_PRICE,
    FLAG_SHORT_SAMPLE,
)


def _parlay_eligible(row: dict[str, Any]) -> bool:
    if row.get("is_best_line") != "true":
        return False
    flags = str(row.get("quality_flags") or "")
    if any(flag in flags for flag in PARLAY_BLOCKING_FLAGS):
        return False
    return row.get("decimal_price") not in (None, "")



def _leg_text(row: dict[str, Any]) -> str:
    record = (
        f"{row['l10_hits']}/{row['l10_total']}"
        if row.get("l10_hits") != ""
        else f"{row['l10_pct']}%"
    )
    price_text = _price_text(row.get("best_price"))
    return f"{row['team']} o{row['line']:g} ({record}, {price_text} {row['best_book']})"


def build_alt_team_total_parlays(
    rows: list[dict[str, Any]],
    *,
    num_legs: int = PARLAY_LEGS,
) -> list[dict[str, Any]]:
    """2-leg combinations of best-line legs (one per team), same league only.

    Combined odds are the naive product of leg prices — indicative only, since
    a book's priced SGP will differ; ``naive_l10_prob`` likewise treats legs
    as independent even though same-game legs are correlated.
    """
    legs = [r for r in rows if _parlay_eligible(r)]
    legs.sort(key=lambda r: (r["event_id"], str(r["team"]).lower()))
    parlays: list[dict[str, Any]] = []
    for combo in combinations(legs, num_legs):
        teams = {str(leg["team"]).lower() for leg in combo}
        if len(teams) < len(combo):
            continue
        leagues = {leg["league"] for leg in combo}
        if len(leagues) > 1:
            continue
        combined_decimal = 1.0
        naive_prob = 1.0
        for leg in combo:
            combined_decimal *= float(leg["decimal_price"])
            naive_prob *= float(leg["l10_pct"]) / 100.0
        event_ids = [str(leg["event_id"]) for leg in combo]
        is_sgp = len(set(event_ids)) < len(event_ids)
        if is_sgp and str(combo[0].get("league") or "").upper() == "MLB":
            continue
        american = _decimal_to_american(combined_decimal)
        flags = ["SGP_CORRELATED_LEGS"] if is_sgp else []
        parlays.append(
            {
                "parlay_id": "+".join(str(leg["outcome_id"]) for leg in combo),
                "league": combo[0]["league"],
                "num_legs": len(combo),
                "legs": " + ".join(_leg_text(leg) for leg in combo),
                "legs_json": encode_legs(combo),
                "event_ids": ",".join(dict.fromkeys(event_ids)),
                "is_sgp": "true" if is_sgp else "false",
                "combined_decimal": round(combined_decimal, 4),
                "combined_american": american if american is not None else "",
                "combined_implied_prob": round(100.0 / combined_decimal, 3),
                "naive_l10_prob": round(naive_prob * 100.0, 3),
                "quality_flags": ",".join(flags),
                "as_of": combo[0].get("as_of") or "",
            }
        )
    parlays.sort(key=lambda p: -float(p["naive_l10_prob"]))
    return parlays


def format_alt_team_totals_md(
    rows: list[dict[str, Any]],
    parlays: list[dict[str, Any]],
    title: str = "# Alt team totals (90-100% L10)",
) -> str:
    lines = [title, ""]
    if not rows:
        lines.append("_No team-total alt lines in the 90-100% L10 band._")
        return "\n".join(lines)
    lines.append("## Qualifying alt lines")
    for row in rows:
        best = " **best**" if row.get("is_best_line") == "true" else ""
        record = (
            f"{row['l10_hits']}/{row['l10_total']}"
            if row.get("l10_hits") != ""
            else f"{row['l10_pct']}%"
        )
        flags = f" flags={row['quality_flags']}" if row.get("quality_flags") else ""
        lines.append(
            f"- [{row['league']}] {row['team']} OVER {row['line']:g} "
            f"({record} L10) {_price_text(row['best_price'])} {row['best_book']} "
            f"| {row['matchup']}{best}{flags}"
        )
    lines += ["", "## Parlay suggestions (indicative pricing)"]
    if not parlays:
        lines.append("_Fewer than two parlay-eligible teams._")
    for parlay in parlays:
        sgp = " [SGP]" if parlay.get("is_sgp") == "true" else ""
        lines.append(
            f"- {parlay['legs']} => dec {parlay['combined_decimal']} "
            f"({_price_text(parlay['combined_american'])}) "
            f"naive L10 {parlay['naive_l10_prob']}% (uncorrelated){sgp}"
        )
    return "\n".join(lines)



def export_alt_team_totals_for_league(
    league: str, *, target_date: str | None = None
) -> dict[str, Any]:
    token = league.strip().upper()
    target_date = target_date or datetime.now().astimezone().strftime("%Y-%m-%d")
    paths = league_paths(token).ensure()
    games_path = paths.games_normalized_latest()
    if not games_path.exists():
        raise FileNotFoundError(f"missing normalized games feed: {games_path}")
    games_norm = json.loads(games_path.read_text("utf-8"))

    rows = build_alt_team_total_board(games_norm, league=token, target_date=target_date)
    parlays = build_alt_team_total_parlays(rows)

    board_csv = paths.reports / f"{token.lower()}_alt_team_totals_latest.csv"
    parlays_csv = paths.reports / f"{token.lower()}_alt_team_total_parlays_latest.csv"
    board_md = paths.reports / f"{token.lower()}_alt_team_totals_latest.md"
    _write_csv(board_csv, ALT_TEAM_TOTALS_HEADER, rows)
    _write_csv(parlays_csv, ALT_TEAM_TOTAL_PARLAYS_HEADER, parlays)
    board_md.write_text(
        format_alt_team_totals_md(rows, parlays) + "\n", encoding="utf-8"
    )

    status = {
        "league": token,
        "status": "ok",
        "generated_at": datetime.now().astimezone().isoformat(),
        "target_date": target_date,
        "games_normalized_latest": str(games_path),
        "board_csv": str(board_csv),
        "parlays_csv": str(parlays_csv),
        "record_count": len(rows),
        "parlay_count": len(parlays),
        "best_line_teams": sum(1 for r in rows if r.get("is_best_line") == "true"),
    }
    (paths.reports / "alt_team_totals_status_latest.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Alt team-total L10 board + parlay suggestions"
    )
    parser.add_argument("--league", choices=supported_leagues(), required=True)
    parser.add_argument(
        "--date",
        help="Slate date (local YYYY-MM-DD); defaults to today.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.league.strip().upper()
    try:
        status = export_alt_team_totals_for_league(token, target_date=args.date)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError) as exc:
        paths = league_paths(token).ensure()
        report = {
            "league": token,
            "status": "error",
            "generated_at": datetime.now().astimezone().isoformat(),
            "error": str(exc)[:300],
        }
        (paths.reports / "alt_team_totals_status_latest.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"{token}: error ({exc})")
        return 1
    print(
        f"{status['league']}: {status['record_count']} qualifying alt lines, "
        f"{status['best_line_teams']} teams, {status['parlay_count']} parlays"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
