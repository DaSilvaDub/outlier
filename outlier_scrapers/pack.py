"""AI Research Desk pack writer.

Transforms the pipeline's player + game triage cards into a daily betting pack
(candidates.csv, briefing.md, dossiers/) for manual paste into ChatGPT / Gemini /
Claude. Pure transform + file I/O; consumes ``outlier_scrapers.sizing``.

Sizing inputs (fair prob, book decimal odds) are sourced from the normalized
``ev_records`` joined to each card side by ``outcome_id``. This join also recovers
identity (event_id, market_type) for game cards, whose top-level identity is
currently empty upstream.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import paths
from outlier_scrapers.sizing import compute_sizing

logger = logging.getLogger(__name__)

# Canonical §2b column order. ``sizing_flags`` carries ineligibility reasons so
# the numeric ``edge_pct`` column is never polluted with strings.
CANDIDATES_HEADER = [
    "sport",
    "event_id",
    "market_id",
    "market_type",
    "player_id",
    "selection",
    "line",
    "price",
    "decimal_price",
    "book",
    "as_of",
    "model_prob",
    "push_prob",
    "implied_prob",
    "edge_pct",
    "kelly_025_units",
    "max_units",
    "recommended_units_pre_news",
    "sizing_flags",
    "outlier_ev_pct",
    "outlier_kelly_pct",
    "line_open",
    "line_now",
    "public_money_pct",
    "money_pct",
    "injury_flags",
    "research_leverage",
    "source_timestamps",
]

NO_PUSH_MARKETS = {"MONEYLINE", "ML", "ML_3WAY", "MONEYLINE_3WAY"}


def american_to_decimal(american: float | int | str | None) -> float | None:
    """Convert American odds to decimal. +150 -> 2.5, -200 -> 1.5, +100 -> 2.0."""
    if american is None or american == "":
        return None
    try:
        val = float(american)
    except (ValueError, TypeError):
        return None
    if val > 0:
        return (val / 100.0) + 1.0
    if val < 0:
        return (100.0 / abs(val)) + 1.0
    return 2.0


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def index_ev_by_outcome(ev_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_outcome: dict[str, list[dict[str, Any]]] = {}
    for rec in ev_records:
        oid = rec.get("outcome_id")
        if oid:
            by_outcome.setdefault(str(oid), []).append(rec)
    return by_outcome


def match_ev_records(
    market_id: str | None,
    outcome_id: str | None,
    headline_side: str | None,
    line: float | None,
    ev_records: list[dict[str, Any]],
    by_outcome: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Exact outcome_id first; else market_id + side + current_line == card line."""
    if outcome_id and str(outcome_id) in by_outcome:
        return by_outcome[str(outcome_id)]
    if market_id is None:
        return []
    return [
        r
        for r in ev_records
        if r.get("market_id") == market_id
        and r.get("side") == headline_side
        and r.get("current_line") == line
    ]


def is_no_push_market(market_token: str | None, line: float | None) -> bool:
    """True for markets that cannot push (no real push_prob needed)."""
    token = (market_token or "").upper()
    if token in NO_PUSH_MARKETS:
        return True
    if line is None:
        # No line (e.g. moneyline) -> cannot push on a number.
        return True
    if float(line) % 1 != 0:
        # Half-point / fractional line -> cannot push.
        return True
    # Whole-number spread/total/integer prop -> push-capable.
    return False


def get_research_leverage(market_token: str | None, scope: str | None, sport: str) -> str:
    token = (market_token or "").upper()
    scope_l = (scope or "").lower()
    if sport.upper() == "MLB":
        if (
            token == "TOTAL"
            or scope_l in ("first_5_innings", "first_3_innings")
            or "nrfi" in scope_l
        ):
            return "HIGH"
        if token in ("SPREAD", "MONEYLINE", "RUN_LINE", "GAMELINE"):
            return "LOW"
    return "MED"


def format_source_timestamps(ts_dict: dict[str, str | None]) -> str:
    return json.dumps({k: v for k, v in ts_dict.items() if v})


def build_event_starts(props_payload: dict | None, games_payload: dict | None) -> dict[str, str]:
    """event_id -> ISO start time, from whichever normalized payload carries it."""
    starts: dict[str, str] = {}
    if props_payload:
        for rec in props_payload.get("records", []):
            eid = rec.get("event_id")
            sa = (rec.get("sport_context") or {}).get("event_starts_at")
            if eid and sa and eid not in starts:
                starts[str(eid)] = sa
    if games_payload:
        events = (games_payload.get("context") or {}).get("events") or {}
        for eid, info in events.items():
            sa = (
                info.get("starts_at") or info.get("event_starts_at")
                if isinstance(info, dict)
                else None
            )
            if sa and str(eid) not in starts:
                starts[str(eid)] = sa
    return starts


def build_injuries(games_payload: dict | None) -> dict[str, str]:
    """event_id -> injury summary, joining context.events teams to context.teams."""
    out: dict[str, str] = {}
    if not games_payload:
        return out
    ctx = games_payload.get("context") or {}
    teams = ctx.get("teams") or {}
    events = ctx.get("events") or {}
    for eid, info in events.items():
        if not isinstance(info, dict):
            continue
        team_ids = [info.get("home_team_id"), info.get("away_team_id")]
        flags: list[str] = []
        for tid in team_ids:
            inj = (teams.get(str(tid)) or {}).get("injuries") if tid else None
            for item in inj or []:
                if isinstance(item, dict):
                    flags.append(str(item.get("player") or item.get("description") or item))
                else:
                    flags.append(str(item))
        if flags:
            out[str(eid)] = " | ".join(flags)
    return out


def _slug(text: str | None) -> str:
    if not text:
        return "unknown"
    return "".join(ch if ch.isalnum() else "-" for ch in str(text).lower()).strip("-") or "unknown"


def _fmt_line(line: Any) -> str:
    if line in (None, ""):
        return ""
    try:
        f = float(line)
    except (ValueError, TypeError):
        return str(line)
    return str(int(f)) if f == int(f) else str(f)


def build_selection(name: Any, label: Any, side: Any, line: Any) -> str:
    """Human-readable selection, e.g. 'A. Judge HITS Over 5.5' / 'Run Line OVER 8.5'."""
    parts = [str(p) for p in (name, label, side) if p]
    fl = _fmt_line(line)
    if fl:
        parts.append(fl)
    return " ".join(parts) if parts else (str(side) if side else "")


def build_row(
    card: dict[str, Any],
    ev_records: list[dict[str, Any]],
    by_outcome: dict[str, list[dict[str, Any]]],
    sport: str,
    odds_ts: str | None,
    norm_ts: str | None,
    source_ts: dict[str, str | None],
    event_starts: dict[str, str],
    injuries: dict[str, str],
) -> dict[str, Any] | None:
    headline_side = card.get("headline_side")
    if headline_side is None:
        return None
    side_view = (card.get("sides") or {}).get(headline_side)
    if not side_view:
        return None

    market_id = card.get("card_id") or card.get("market_id")
    outcome_id = side_view.get("outcome_id")
    line = side_view.get("line")
    ev_summary = side_view.get("ev")

    matched = match_ev_records(market_id, outcome_id, headline_side, line, ev_records, by_outcome)
    ref = matched[0] if matched else {}

    # Identity: prefer the card; fall back to the matched ev_record (game cards
    # have empty top-level identity upstream).
    event_id = card.get("event_id") or ref.get("event_id")
    market_token = card.get("market") or ref.get("market")
    market_type = card.get("market_type") or ref.get("market_type") or market_token
    scope = card.get("scope") or ref.get("scope")

    row = {k: "" for k in CANDIDATES_HEADER}
    row["sport"] = sport
    row["event_id"] = event_id
    row["market_id"] = market_id
    row["market_type"] = market_type
    row["player_id"] = card.get("player_id")
    name = card.get("player") or ref.get("player") or card.get("matchup") or ref.get("matchup")
    label = ref.get("market_label") or card.get("market_label") or market_token
    row["selection"] = build_selection(name, label, headline_side, line)
    row["line"] = line
    row["research_leverage"] = get_research_leverage(market_token, scope, sport)
    row["injury_flags"] = injuries.get(str(event_id), "") if event_id else ""
    row["source_timestamps"] = format_source_timestamps(source_ts)

    movement = side_view.get("movement") or {}
    row["line_open"] = movement.get("open_line")
    row["line_now"] = movement.get("current_line")
    public_money = side_view.get("public_money") or {}
    row["public_money_pct"] = public_money.get("public_money_pct") or public_money.get("percentage")
    row["money_pct"] = public_money.get("money_pct") or public_money.get("money")

    if ev_summary:
        row["outlier_ev_pct"] = ev_summary.get("best_ev_pct")
        row["outlier_kelly_pct"] = ev_summary.get("kelly_pct")

    no_push = is_no_push_market(market_token, line)
    push_prob = 0.0 if no_push else None

    # Eligibility: real EV summary, not an alt-line fallback, with a usable
    # decimal book price in the matched ev_record set.
    usable = [r for r in matched if r.get("book_decimal_odds") is not None]
    eligible = (
        bool(ev_summary) and not (ev_summary or {}).get("is_alt_line_fallback") and bool(usable)
    )

    if eligible:
        best = sorted(
            usable,
            key=lambda r: (r.get("book_decimal_odds") or 0.0, r.get("calculated_ev_pct") or 0.0),
            reverse=True,
        )[0]
        row["book"] = best.get("book")
        row["price"] = best.get("book_odds")
        row["decimal_price"] = best.get("book_decimal_odds")
        row["as_of"] = odds_ts
        devig = (ev_summary or {}).get("devig_decimal")
        model_prob = (1.0 / devig) if devig else None
        row["model_prob"] = model_prob
        if push_prob is None:
            # Push-capable whole-number line, no real push_prob -> sizing-ineligible.
            row["sizing_flags"] = "push_capable_no_prob"
        else:
            sizing = compute_sizing(
                decimal_price=row["decimal_price"], model_prob=model_prob, push_prob=push_prob
            )
            row["implied_prob"] = sizing.implied_prob
            row["edge_pct"] = sizing.edge_pct
            row["kelly_025_units"] = sizing.kelly_025_units
            row["max_units"] = sizing.max_units
            row["recommended_units_pre_news"] = sizing.recommended_units_pre_news
    else:
        # No-EV / ineligible: anchor display odds only, no sizing.
        if ev_summary and (ev_summary or {}).get("is_alt_line_fallback"):
            row["sizing_flags"] = "ev_line_fallback"
        elif ev_summary:
            row["sizing_flags"] = "no_book_decimal"
        best_odds = side_view.get("best_odds")
        row["price"] = best_odds
        row["decimal_price"] = american_to_decimal(best_odds)
        row["as_of"] = odds_ts if ev_summary else norm_ts
        per_book = side_view.get("per_book_odds") or {}
        if isinstance(per_book, dict) and per_book:
            row["book"] = next(iter(per_book.keys()), None)
        elif isinstance(per_book, list) and per_book and isinstance(per_book[0], dict):
            row["book"] = per_book[0].get("book")

    # Routing metadata (not emitted to CSV).
    row["_board"] = "board_a" if card.get("board") == "A" else "board_b"
    row["_rank_value"] = card.get("rank_value") or 0.0
    row["_event_starts_at"] = event_starts.get(str(event_id)) if event_id else None
    row["_slug"] = _slug(card.get("matchup") or ref.get("matchup"))
    return row


def _local_date(iso_ts: str | None) -> str | None:
    if not iso_ts:
        return None
    try:
        return (
            datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
        )
    except (ValueError, TypeError):
        return None


def process_stream(
    cards_payload: dict | None,
    lm_payload: dict | None,
    norm_payload: dict | None,
    enrichment_payload: dict | None,
    sport: str,
    stream: str,
    event_starts: dict[str, str],
    injuries: dict[str, str],
) -> list[dict[str, Any]]:
    if not cards_payload:
        return []

    cards_key = "cards" if stream == "props" else "games_cards"
    lm_key = "line_movement" if stream == "props" else "games_line_movement"
    norm_key = "props" if stream == "props" else "games"
    source_ts: dict[str, str | None] = {cards_key: cards_payload.get("generated_at")}
    if lm_payload:
        source_ts[lm_key] = lm_payload.get("generated_at")
    if norm_payload:
        source_ts[norm_key] = norm_payload.get("generated_at")
    if enrichment_payload:
        source_ts["games_enrichment"] = enrichment_payload.get("generated_at")

    odds_ts = lm_payload.get("generated_at") if lm_payload else None
    norm_ts = norm_payload.get("generated_at") if norm_payload else None
    ev_records = lm_payload.get("ev_records", []) if lm_payload else []
    by_outcome = index_ev_by_outcome(ev_records)

    rows: list[dict[str, Any]] = []
    for board in ("board_a", "board_b"):
        for card in cards_payload.get(board, []):
            row = build_row(
                card,
                ev_records,
                by_outcome,
                sport,
                odds_ts,
                norm_ts,
                source_ts,
                event_starts,
                injuries,
            )
            if row is not None:
                rows.append(row)
    return rows


def select_date(
    rows: list[dict[str, Any]], requested: str | None
) -> tuple[list[dict[str, Any]], str]:
    """Filter rows by slate date. Falls back to the latest slate date present when
    the requested date has none. Rows with no resolvable date are never dropped."""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    dated = {_local_date(r.get("_event_starts_at")) for r in rows}
    dated.discard(None)

    if not dated:
        # No slate dates available upstream -> keep everything, name by request/today.
        return rows, (requested or today)

    target = requested or today
    if target not in dated:
        target = max(dated)
        logger.warning("No events on %s; falling back to latest slate date %s", requested, target)

    kept = [r for r in rows if _local_date(r.get("_event_starts_at")) in (target, None)]
    return kept, target


def rank_rows(rows: list[dict[str, Any]], top_ev_n: int, top_signal_n: int) -> list[dict[str, Any]]:
    board_a = [r for r in rows if r["_board"] == "board_a"]
    board_b = [r for r in rows if r["_board"] == "board_b"]

    def _key(r: dict[str, Any]) -> tuple[float, str]:
        return (-(r["_rank_value"] or 0.0), str(r.get("market_id") or ""))

    board_a.sort(key=_key)
    board_b.sort(key=_key)
    return board_a[:top_ev_n] + board_b[:top_signal_n]


MLB_QUESTIONS = [
    "- **Starters:** both confirmed SPs, days rest, recent form, pitch-count limit / opener.",
    "- **Bullpen:** who threw the last 1-2 days, closer availability, gassed pen.",
    "- **Lineup:** posted lineup card, key bats in/out, platoon edge, regulars resting.",
    "- **Weather/park:** wind speed + direction, temp, rain risk, roof, park, altitude.",
    "- **Umpire:** home-plate ump strike-zone tendency.",
    "- *Markets:* full game, **F5**, run line, total, **NRFI/YRFI**, strikeout props, H+R+RBI.",
]
WNBA_QUESTIONS = [
    "- **Availability:** injury report status (out/quest/prob), load management, rest.",
    "- **Lineup/rotation:** confirmed starters, rotation changes, minutes restrictions.",
    "- **Schedule/fatigue:** back-to-back, travel/time-zone, schedule density.",
    "- **Usage shift:** if a star sits, who absorbs usage -> which prop **overs** light up.",
    "- **Game script:** pace matchup, blowout risk, foul-trouble tendencies.",
    "- *Markets:* spread, total, points/reb/ast, **PRA**, 3PM, alt lines.",
]


def build_dossier(rows: list[dict[str, Any]], sport: str) -> str:
    lines = [f"## {sport} game dossier", ""]
    lines += MLB_QUESTIONS if sport.upper() == "MLB" else WNBA_QUESTIONS
    lines += ["", "### Markets in play"]
    for r in rows:
        lines.append(
            f"- {r.get('market_type')}: {r.get('selection')} @ {r.get('line')} ({r.get('price')})"
        )
    return "\n".join(lines)


ROLE_BLOCK = [
    "REASONING PASSES (A, D):",
    "- Use this pack ONLY. Do not use memory or the web.",
    "- Never invent or recall odds/lines. Every verdict quotes the exact market_id + line/price from the pack.",
    "- If you need info not in the pack, list it under NEEDS — do not guess.",
    "- Flag any edge that looks like a data artifact (stale line, injury already priced, wrong side of a key number).",
    "",
    "RESEARCH PASSES (B, C):",
    "- You MAY use current web sources (last 24h).",
    "- Do NOT invent, quote, or update any betting line/price. The pack's lines are the only lines.",
    "- Tie every finding back to a quoted market_id + line/price from the pack.",
    "- Every news item must carry: claim, source name, SOURCE TIER (see §2e), and timestamp.",
]


def build_briefing(rows: list[dict[str, Any]], target_date: str) -> str:
    lines = [f"SLATE: {target_date}", ""] + ROLE_BLOCK + ["", "### Top EV cards"]
    for r in rows:
        if r["_board"] == "board_a":
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"({r.get('price')}) edge={r.get('edge_pct')} units={r.get('recommended_units_pre_news')}"
            )
    lines += ["", "### Top signal cards"]
    for r in rows:
        if r["_board"] == "board_b":
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')}"
            )
    lines += ["", "### Slate index"]
    seen: set[str] = set()
    for r in rows:
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} event {eid} | first lock: {r.get('_event_starts_at') or 'n/a'}"
            )
    return "\n".join(lines)


def write_pack(rows: list[dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATES_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    (out_dir / "briefing.md").write_text(build_briefing(rows, out_dir.name), encoding="utf-8")

    dossiers_dir = out_dir / "dossiers"
    dossiers_dir.mkdir(exist_ok=True)
    by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        eid = r.get("event_id")
        if eid:
            by_event.setdefault((r["sport"], str(eid)), []).append(r)
    for (sport, eid), erows in by_event.items():
        slug = erows[0].get("_slug", "unknown")
        (dossiers_dir / f"{sport}_{eid}_{slug}.md").write_text(
            build_dossier(erows, sport), encoding="utf-8"
        )


def build_pack(
    leagues: Sequence[str], requested_date: str | None, top_ev_n: int, top_signal_n: int
) -> tuple[list[dict[str, Any]], str]:
    all_rows: list[dict[str, Any]] = []
    for raw_league in leagues:
        lg = raw_league.strip().upper()
        if not lg:
            continue
        lp = paths.league_paths(lg)
        cards_dir = lp.root / "cards"
        norm = lp.normalized
        low = lg.lower()

        games_norm = load_json(norm / f"{low}_games_latest.json")
        props_norm = load_json(norm / f"{low}_props_latest.json")
        event_starts = build_event_starts(props_norm, games_norm)
        injuries = build_injuries(games_norm)

        props_rows = process_stream(
            load_json(cards_dir / f"{low}_cards_latest.json"),
            load_json(norm / f"{low}_line_movement_latest.json"),
            props_norm,
            None,
            lg,
            "props",
            event_starts,
            injuries,
        )
        games_rows = process_stream(
            load_json(cards_dir / f"{low}_games_cards_latest.json"),
            load_json(norm / f"{low}_games_line_movement_latest.json"),
            games_norm,
            load_json(norm / f"{low}_games_enrichment_latest.json"),
            lg,
            "games",
            event_starts,
            injuries,
        )
        if not load_json(cards_dir / f"{low}_games_cards_latest.json"):
            logger.warning("%s: no game-cards stream found", lg)
        all_rows.extend(props_rows)
        all_rows.extend(games_rows)

    kept, target_date = select_date(all_rows, requested_date)
    final_rows = rank_rows(kept, top_ev_n, top_signal_n)
    return final_rows, target_date


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the daily AI research-desk pack.")
    parser.add_argument("--leagues", default="MLB,WNBA")
    parser.add_argument("--date")
    parser.add_argument("--top-ev-n", type=int, default=15)
    parser.add_argument("--top-signal-n", type=int, default=10)
    args = parser.parse_args(argv)

    leagues = args.leagues.split(",")
    final_rows, target_date = build_pack(leagues, args.date, args.top_ev_n, args.top_signal_n)
    out_dir = paths.PROJECT_ROOT / "packs" / target_date
    write_pack(final_rows, out_dir)
    logger.info("Wrote %d rows to %s", len(final_rows), out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
