"""AI Research Desk pack writer.

Transforms the pipeline's player + game triage cards into a daily betting pack
(candidates.csv, briefing.md, dossiers/) for the AI research desk.

Includes Tier 1 fixes: display dedup selection, player_id + push_prob, strict date,
full round-robin+global-fill quota, candidate-scoped freshness, decisions.csv scaffold.
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
from outlier_scrapers.registry import (
    classify_foreign_market,
    get_sport_config,
    team_display_name,
)
from outlier_scrapers.sizing import compute_sizing

logger = logging.getLogger(__name__)

CANDIDATES_HEADER = [
    "sport",
    "event_id",
    "market_id",
    "market_type",
    "player_id",
    "matchup",
    "team",
    "team_name",
    "opponent",
    "opp_name",
    "home_away",
    "market_label",
    "selection",
    "line",
    "priced_line",
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
    "data_quality_flags",
    "outlier_ev_pct",
    "outlier_kelly_pct",
    "local_ev_pct",
    "local_kelly_pct",
    "line_open",
    "line_now",
    "public_money_pct",
    "money_pct",
    "injury_flags",
    "research_leverage",
    "source_timestamps",
]

NO_PUSH_MARKETS = {"MONEYLINE", "ML", "ML_3WAY", "MONEYLINE_3WAY"}

# House rule: HR / HRR (H+R+RBI) / BB (walks) prop markets are excluded from
# packs entirely (low hit-rate longshot markets). Covers both the normalized
# short codes (registry.py) and the raw proposition tokens.
EXCLUDED_MARKETS = {
    "HR", "HOME_RUNS",
    "HRR", "HITSRUNSRBIS", "HITS_RUNS_RBIS",
    "BB", "WALKS",
}

# House rule: plus-money longshots (e.g. a Hits Over at +181) are hard-filtered
# from packs. Any candidate priced at +LONGSHOT_AMERICAN_PRICE or longer is dropped.
LONGSHOT_AMERICAN_PRICE = 150

def american_to_decimal(american: float | int | str | None) -> float | None:
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

def is_excluded_market(market_token: str | None, market_type: str | None) -> bool:
    for tok in (market_token, market_type):
        if tok and str(tok).strip().upper() in EXCLUDED_MARKETS:
            return True
    return False

def is_longshot_price(price: Any) -> bool:
    if price in (None, ""):
        return False
    try:
        val = float(str(price).replace("+", ""))
    except (ValueError, TypeError):
        return False
    return val >= LONGSHOT_AMERICAN_PRICE

def is_no_push_market(market_token: str | None, line: float | None) -> bool:
    token = (market_token or "").upper()
    if token in NO_PUSH_MARKETS:
        return True
    if line is None:
        return True
    try:
        if float(line) % 1 != 0:
            return True
    except (ValueError, TypeError):
        pass
    return False

def get_research_leverage(market_token: str | None, scope: str | None, sport: str) -> str:
    token = (market_token or "").upper()
    scope_l = (scope or "").lower()
    if sport.upper() == "MLB":
        if token == "TOTAL" or scope_l in ("first_5_innings", "first_3_innings") or "nrfi" in scope_l:
            return "HIGH"
        if token in ("SPREAD", "MONEYLINE", "RUN_LINE", "GAMELINE"):
            return "LOW"
    return "MED"

def format_source_timestamps(ts_dict: dict[str, str | None]) -> str:
    return json.dumps({k: v for k, v in ts_dict.items() if v})

def build_event_starts(props_payload: dict | None, games_payload: dict | None) -> dict[str, str]:
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
            if isinstance(info, dict):
                sa = info.get("starts_at") or info.get("event_starts_at")
                if sa and str(eid) not in starts:
                    starts[str(eid)] = sa
    return starts

def build_injuries(games_payload: dict | None) -> dict[str, str]:
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

def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None

def _fmt_line(line: Any) -> str:
    if line in (None, ""):
        return ""
    try:
        f = float(line)
    except (ValueError, TypeError):
        return str(line)
    return str(int(f)) if f == int(f) else str(f)

def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def _home_away(matchup: Any, team: Any) -> str:
    """Resolve whether ``team`` is HOME or AWAY within an ``AWAY @ HOME`` matchup.

    Returns "HOME"/"AWAY" when the team code matches one side, else "" (unknown).
    Comparison is code-level; matchup strings are built from canonical aliases in
    the normalizer, so an exact token match is reliable.
    """
    matchup_s = str(matchup or "").strip()
    team_s = str(team or "").strip().upper()
    if not matchup_s or not team_s or " @ " not in matchup_s:
        return ""
    away_tok, home_tok = (p.strip().upper() for p in matchup_s.split(" @ ", 1))
    if team_s == home_tok:
        return "HOME"
    if team_s == away_tok:
        return "AWAY"
    return ""

def _priced_line_from_ev(ev_records: list[dict[str, Any]], record_id: Any) -> Any:
    """The line an EV alt-line fallback was actually priced at (``current_line``)."""
    if not record_id:
        return None
    for rec in ev_records:
        if rec.get("record_id") == record_id:
            return rec.get("current_line")
    return None

# A single player prop line above this is a data artifact, not a real market
# (no MLB/WNBA single-player line approaches it). Deliberately generous so a
# high-but-real line — e.g. a starter's ~130 pitches-thrown — never trips it.
PLAYER_PROP_LINE_CEILING = 300.0

def market_validation_flags(
    sport: str,
    card: dict[str, Any],
    ref: dict[str, Any],
    market_token: Any,
    market_type: Any,
    player_id: Any,
    line: Any,
) -> list[str]:
    """Non-fatal data-quality flags for a candidate's market/line.

    Deterministic checks only (no hard drops): a cross-sport market artifact and
    a clearly-impossible line. Returns flag strings for ``data_quality_flags``.
    """
    flags: list[str] = []
    market_value = _coalesce(
        card.get("proposition"), ref.get("proposition"), card.get("market_raw"), market_token
    )
    foreign = classify_foreign_market(sport, market_value)
    if foreign:
        flags.append(f"cross_sport_market:{foreign}")
    if line not in (None, ""):
        line_val = _to_float(line)
        # ``line_val != line_val`` is an import-free NaN test: a NaN line parses
        # without error but compares False against everything, so it would slip
        # past the ceiling check unflagged.
        if line_val is None or line_val != line_val:
            flags.append("non_numeric_line")
        else:
            is_player_prop = str(market_type or "").upper() == "PLAYER_PROP" or bool(player_id)
            if is_player_prop and abs(line_val) > PLAYER_PROP_LINE_CEILING:
                flags.append("implausible_line")
    return flags

def build_selection(name: Any, label: Any, side: Any, line: Any) -> str:
    name_s = str(name or "").strip()
    label_s = str(label or "").strip()
    side_s = str(side or "").strip()
    if name_s and label_s:
        ln = label_s.lower()
        nn = name_s.lower()
        if ln.startswith(nn) or ln.startswith(nn + " ") or ln.startswith(nn + "-") or ln.startswith(nn + " -"):
            core = label_s
        else:
            core = f"{name_s} {label_s}".strip()
    else:
        core = " ".join(p for p in (name_s, label_s) if p)
    # Append side only if not already present in core (prevents "Foo OVER OVER 1.5").
    # This keeps display dedup while preserving verbatim spirit for research quotes.
    parts = [core] if core else []
    if side_s:
        if side_s.lower() not in (core or "").lower():
            parts.append(side_s)
    fl = _fmt_line(line)
    if fl:
        parts.append(fl)
    return " ".join(parts) if parts else (side_s if side_s else "")

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
    event_id = card.get("event_id") or ref.get("event_id")
    market_token = card.get("market") or ref.get("market")
    market_type = card.get("market_type") or ref.get("market_type") or market_token
    if is_excluded_market(market_token, market_type):
        return None
    scope = card.get("scope") or ref.get("scope")
    row = {k: "" for k in CANDIDATES_HEADER}
    row["sport"] = sport
    row["event_id"] = event_id
    row["market_id"] = market_id
    row["market_type"] = market_type
    row["player_id"] = card.get("player_id") or ref.get("player_id")
    name = card.get("player") or ref.get("player") or card.get("matchup") or ref.get("matchup")
    label = ref.get("market_label") or card.get("market_label") or market_token
    row["selection"] = build_selection(name, label, headline_side, line)
    # Human-readable context the normalizer already resolved. Surfacing it stops
    # the reasoning desk from guessing teams/markets off the hash event_id or a
    # terse code (e.g. LAS vs LVA, PT = Pitches Thrown).
    matchup = card.get("matchup") or ref.get("matchup")
    team = card.get("team") or ref.get("team")
    opponent = card.get("opponent") or ref.get("opponent")
    try:
        config = get_sport_config(sport, allow_disabled=True)
    except ValueError:
        config = None
    row["matchup"] = matchup
    row["team"] = team
    row["team_name"] = team_display_name(config, team) if (config and team) else None
    row["opponent"] = opponent
    row["opp_name"] = team_display_name(config, opponent) if (config and opponent) else None
    row["home_away"] = _home_away(matchup, team)
    row["market_label"] = _coalesce(
        card.get("market_label"), ref.get("market_label"), card.get("market_raw"), market_token
    )
    row["line"] = line
    row["research_leverage"] = get_research_leverage(market_token, scope, sport)
    row["injury_flags"] = injuries.get(str(event_id), "") if event_id else ""
    row["source_timestamps"] = format_source_timestamps(source_ts)
    movement = side_view.get("movement") or {}
    row["line_open"] = movement.get("open_line")
    row["line_now"] = movement.get("current_line")
    public_money = side_view.get("public_money") or {}
    row["public_money_pct"] = _coalesce(public_money.get("public_money_pct"), public_money.get("percentage"))
    row["money_pct"] = _coalesce(public_money.get("money_pct"), public_money.get("money"))
    if ev_summary:
        if ev_summary.get("ev_source") == "LOCAL":
            row["local_ev_pct"] = ev_summary.get("best_ev_pct")
            row["local_kelly_pct"] = ev_summary.get("kelly_pct")
        else:
            row["outlier_ev_pct"] = ev_summary.get("best_ev_pct")
            row["outlier_kelly_pct"] = ev_summary.get("kelly_pct")
    no_push = is_no_push_market(market_token, line)
    push_prob = 0.0 if no_push else None
    row["push_prob"] = push_prob
    usable = [r for r in matched if r.get("book_decimal_odds") is not None]
    eligible = bool(ev_summary) and not (ev_summary or {}).get("is_alt_line_fallback") and bool(usable)
    if eligible:
        best_record_id = ev_summary.get("best_record_id")
        if best_record_id:
            usable = [r for r in usable if r.get("record_id") == best_record_id]
        if not usable:
            return None
        best = sorted(usable, key=lambda r: (r.get("book_decimal_odds") or 0.0, r.get("calculated_ev_pct") or 0.0), reverse=True)[0]
        row["book"] = best.get("book")
        row["price"] = best.get("book_odds")
        row["decimal_price"] = best.get("book_decimal_odds")
        row["as_of"] = odds_ts
        devig = (ev_summary or {}).get("devig_decimal")
        model_prob = (1.0 / devig) if devig else None
        row["model_prob"] = model_prob
        if push_prob is None:
            row["sizing_flags"] = "push_capable_no_prob"
        else:
            sizing = compute_sizing(decimal_price=row["decimal_price"], model_prob=model_prob, push_prob=push_prob)
            row["implied_prob"] = sizing.implied_prob
            row["edge_pct"] = sizing.edge_pct
            row["kelly_025_units"] = sizing.kelly_025_units
            row["max_units"] = sizing.max_units
            row["recommended_units_pre_news"] = sizing.recommended_units_pre_news
    else:
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
    if is_longshot_price(row.get("price")):
        return None

    if row.get("decimal_price") is not None and row["decimal_price"] <= 1.20:
        return None

    # Surface card-level quality flags and, for an EV alt-line fallback, the line
    # the EV/price was actually derived from (e.g. shown 9.0 but priced at 8.5),
    # so the desk sees the mismatch instead of silently trusting the shown line.
    dq_flags = [str(f) for f in (card.get("flags") or [])]
    if (ev_summary or {}).get("is_alt_line_fallback"):
        priced = _priced_line_from_ev(ev_records, ev_summary.get("best_record_id"))
        if priced is not None and _to_float(priced) != _to_float(line):
            row["priced_line"] = priced
            dq_flags = [f for f in dq_flags if f != "ev_line_fallback"]
            dq_flags.append(f"ev_line_fallback:priced_at={_fmt_line(priced)}")
    dq_flags += market_validation_flags(
        sport, card, ref, market_token, market_type, row.get("player_id"), line
    )
    row["data_quality_flags"] = ";".join(dict.fromkeys(dq_flags))

    row["_board"] = "board_a" if card.get("board") == "A" else "board_b"
    row["_rank_value"] = card.get("rank_value") or 0.0
    row["_event_starts_at"] = event_starts.get(str(event_id)) if event_id else None
    row["_slug"] = _slug(card.get("matchup") or ref.get("matchup"))

    # Preserve any _raw_* or other upstream passthrough fields from card/ref/ev (AGENTS.md).
    for src in (card, ref, (ev_summary or {})):
        if isinstance(src, dict):
            for k, v in src.items():
                if k.startswith("_") and k not in row:
                    row[k] = v

    return row

def _local_date(iso_ts: str | None) -> str | None:
    if not iso_ts:
        return None
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
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
    for card in (cards_payload.get("board_a") or []) + (cards_payload.get("board_b") or []):
        row = build_row(card, ev_records, by_outcome, sport, odds_ts, norm_ts, source_ts, event_starts, injuries)
        if row is not None:
            row["_stream"] = stream
            rows.append(row)
    return rows

def select_date(
    rows: list[dict[str, Any]], requested: str | None
) -> tuple[list[dict[str, Any]], str]:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    dated = {_local_date(r.get("_event_starts_at")) for r in rows}
    dated.discard(None)
    if not dated:
        return rows, (requested or today)
    if requested and requested in dated:
        target = requested
    else:
        target = today if today in dated else max(dated)
    kept = [r for r in rows if _local_date(r.get("_event_starts_at")) in (target, None)]
    if requested and requested not in dated:
        logger.warning("Requested date %s has no events; emitting empty pack for that date.", requested)
    return kept, target

def _parse_start(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else None

def drop_locked_events(
    rows: list[dict[str, Any]], now: datetime | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """House rule: only pack markets proven to be pregame.

    Once an event locks, its markets go live: alt-line ladders re-center on the
    in-game state, settled lines 404 off the API, and EV disappears — numbers
    that read downstream as corrupt pregame lines (see slate 2026-07-06). Rows
    without a parseable start time are also dropped because their pregame state
    cannot be verified. Returns (kept, dropped) as new lists; rows are not
    mutated.
    """
    now = now or datetime.now().astimezone()
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        start = _parse_start(row.get("_event_starts_at"))
        if start is None or start <= now:
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped

def rank_rows(rows: list[dict[str, Any]], top_ev_n: int, top_signal_n: int) -> list[dict[str, Any]]:
    # Immutability: do not mutate caller's rows. Create new objects (AGENTS.md).
    rows = [
        ({**r, "_stream": "props"} if "_stream" not in r else r)
        for r in rows
    ]
    board_a = [r for r in rows if r.get("_board") == "board_a"]
    board_b = [r for r in rows if r.get("_board") == "board_b"]
    def _key(r: dict[str, Any]) -> tuple[float, str]:
        return (-(r.get("_rank_value") or 0.0), str(r.get("market_id") or ""))
    def bucket_key(r: dict[str, Any]) -> tuple[str, str]:
        return (str(r.get("sport") or ""), str(r.get("_stream") or "props"))
    def round_robin_then_fill(cands: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        if not cands or limit <= 0:
            return []
        from collections import defaultdict, deque
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for r in cands:
            buckets[bucket_key(r)].append(r)
        for k in buckets:
            buckets[k].sort(key=_key)
        selected: list[dict[str, Any]] = []
        deques = {k: deque(v) for k, v in buckets.items() if v}
        while len(selected) < limit and deques:
            for k in list(deques.keys()):
                if not deques[k]:
                    deques.pop(k, None)
                    continue
                selected.append(deques[k].popleft())
                if len(selected) >= limit:
                    break
            for k in list(deques):
                if not deques[k]:
                    deques.pop(k, None)
        if len(selected) < limit:
            seen = {id(x) for x in selected}
            remain = [r for r in cands if id(r) not in seen]
            remain.sort(key=_key)
            selected.extend(remain[: limit - len(selected)])
        return selected
    ev = round_robin_then_fill(board_a, top_ev_n)
    sig = round_robin_then_fill(board_b, top_signal_n)
    return ev + sig

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

def _matchup_display(row: dict[str, Any]) -> str:
    """A human matchup line for a row, preferring full names over codes."""
    away_name = home_name = None
    matchup = str(row.get("matchup") or "").strip()
    ha = row.get("home_away")
    if ha == "HOME":
        home_name, away_name = row.get("team_name"), row.get("opp_name")
    elif ha == "AWAY":
        away_name, home_name = row.get("team_name"), row.get("opp_name")
    if away_name and home_name:
        return f"{away_name} @ {home_name} ({matchup})" if matchup else f"{away_name} @ {home_name}"
    return matchup or "unknown matchup"


def build_dossier(rows: list[dict[str, Any]], sport: str) -> str:
    matchup = _matchup_display(rows[0]) if rows else "unknown matchup"
    lines = [f"## {sport} game dossier — {matchup}", ""]
    lines += MLB_QUESTIONS if sport.upper() == "MLB" else WNBA_QUESTIONS
    lines += ["", "### Markets in play"]
    for r in rows:
        team = r.get("team_name") or r.get("team") or ""
        ctx = f" [{team}]" if team else ""
        lines.append(
            f"- {r.get('market_type')}: {r.get('selection')}{ctx} @ {r.get('line')} ({r.get('price')})"
        )
    return "\n".join(lines)

ROLE_BLOCK = [
    "LEDGER CONTEXT (all passes):",
    "- Each candidate carries authoritative context: team / team_name, opponent / opp_name,"
    " home_away, matchup, and market_label. Use these verbatim — do NOT infer a player's team,"
    " the opponent, home/away, or what a market means from the event_id hash or a terse code"
    " (e.g. LAS is Los Angeles Sparks not Las Vegas; PT is Pitches Thrown).",
    "- If a row has priced_line set (or a data_quality_flags entry like"
    " ev_line_fallback:priced_at=…), the EV/price were derived at priced_line, not the shown"
    " line — reconcile to priced_line before quoting an edge and note the mismatch.",
    "- data_quality_flags may also carry cross_sport_market:<LEAGUE> (the market belongs to"
    " another sport — treat the row as a data artifact and stand it down) or implausible_line /"
    " non_numeric_line (the line is likely corrupt — verify before quoting).",
    "",
    "HOUSE RULES (all passes):",
    "- HR / HRR (H+R+RBI) / BB (walks) markets are excluded from this desk entirely."
    " If one appears in the pack, treat it as a data error and stand it down.",
    f"- Plus-money longshots priced +{LONGSHOT_AMERICAN_PRICE} or longer (e.g. a Hits Over at +181)"
    " are filtered from this pack. If one appears, treat it as a data error and stand it down.",
    "- Lines are PREGAME-only: candidates whose event already started (first lock in the past)"
    " are filtered from this pack. If a card's as_of/source timestamps fall at or after its"
    " event's first lock, its lines are LIVE/in-play — treat the whole event as a data error"
    " and stand it down.",
    "",
    "REASONING PASSES (A, D):",
    "- Use this pack ONLY. Do not use memory or the web.",
    "- Never invent or recall odds/lines. Every verdict quotes the exact market_id + line/price from the pack.",
    "- If you need info not in the pack, list it under NEEDS — do not guess.",
    "",
    "RESEARCH PASSES (B, C):",
    "- You MAY use current web sources (last 24h).",
    "- Do NOT invent, quote, or update any betting line/price. The pack's lines are the only lines.",
    "- Tie every finding back to a quoted market_id + line/price from the pack.",
    "- Every news item must carry: claim, source name, SOURCE TIER (see §2e), and timestamp.",
    "",
    "WEB DISCOVERY (research passes + manual injury/lineup validation):",
    "- Search first to locate sources; fetch a page only after search returns a specific URL.",
    "- Use targeted queries (e.g. site:wnba.com, site:mlb.com, team name + injury report + date).",
    "- Do not guess URL paths (/injuries, /lineups, /news) without search confirmation.",
    "- Cap page fetches: at most 1-2 per game after search narrows the target.",
    "- Prefer Tier-1: official league/team injury reports, confirmed lineups, NWS weather.",
]

DERIVED_PACK_OUTPUTS = (
    "chatgpt_a.md",
    "gemini_b.md",
    "chatgpt_c.md",
    "claude_d.md",
    "claude_e.md",
    "daily_betting_report.md",
    "manual_betting_report.md",
    "mlb_betting_report.md",
    "reasoning_status.json",
    "manifest.json",
)

FRESH_COVERAGE_WARN = 0.9

def _summarize_lm_status(report: dict[str, Any] | None, label: str) -> tuple[bool, str]:
    if report is None:
        return False, f"- {label}: NO STATUS (not run / missing report)"
    status = report.get("status") or "unknown"
    fetched = report.get("markets_fetched")
    requested = report.get("markets_requested")
    errors = report.get("fetch_error_count") or 0
    age = report.get("props_age_hours")
    gen_at = report.get("generated_at")
    reasons: list[str] = []
    if gen_at:
        try:
            dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00")).astimezone()
            if (datetime.now().astimezone() - dt).total_seconds() > 6 * 3600:
                reasons.append("stale (>6h old)")
        except Exception:
            pass
    else:
        reasons.append("stale (missing timestamp)")
    if status != "ok":
        reasons.append(f"status={status}")
    if report.get("props_is_stale"):
        age_txt = f"{round(age, 1)}h" if isinstance(age, (int, float)) else "?h"
        reasons.append(f"props {age_txt} stale")
    if errors:
        error_ids = [str(m) for m in (report.get("error_market_ids") or []) if m]
        if error_ids:
            shown = ", ".join(error_ids[:8])
            more = f" (+{len(error_ids) - 8} more)" if len(error_ids) > 8 else ""
            reasons.append(f"{errors} fetch errors [missing markets: {shown}{more}]")
        else:
            reasons.append(f"{errors} fetch errors")
    if (
        isinstance(fetched, int)
        and isinstance(requested, int)
        and requested
        and fetched / requested < FRESH_COVERAGE_WARN
    ):
        reasons.append(f"{fetched}/{requested} markets")
    if not reasons:
        cov = f" ({fetched}/{requested})" if requested else ""
        return True, f"- {label}: OK{cov}"
    return False, f"- {label}: CAVEAT — " + "; ".join(reasons)

def build_freshness_section(leagues: Sequence[str]) -> list[str]:
    lines = ["### Freshness / Coverage"]
    all_ok = True
    for raw in leagues:
        lg = raw.strip().upper()
        if not lg:
            continue
        reports = paths.league_paths(lg).reports
        for stream, fname in (
            ("games line-movement", "games_line_movement_status_latest.json"),
            ("props line-movement", "line_movement_status_latest.json"),
        ):
            ok, line = _summarize_lm_status(load_json(reports / fname), f"{lg} {stream}")
            all_ok = all_ok and ok
            lines.append(line)
    if not all_ok:
        lines.append(
            "Streams marked CAVEAT: their line-movement/signal are context-only (not authoritative). "
            "Prefer EV sizing where available; soften conclusions that rely on movement/steam for those streams. "
            "Current odds may still be fresh from the props/games fetch."
        )
    return lines

def build_briefing(
    rows: list[dict[str, Any]], target_date: str, freshness_lines: list[str] | None = None
) -> str:
    lines = [f"SLATE: {target_date}", ""]
    if freshness_lines:
        lines += freshness_lines + [""]
    lines += ROLE_BLOCK + ["", "### Top EV cards"]
    for r in rows:
        if r.get("_board") == "board_a":
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"({r.get('price')}) edge={r.get('edge_pct')} units={r.get('recommended_units_pre_news')} "
                f"| {_matchup_display(r)}"
            )
    lines += ["", "### Top signal cards"]
    for r in rows:
        if r.get("_board") == "board_b":
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"| {_matchup_display(r)}"
            )
    lines += ["", "### Slate index"]
    seen: set[str] = set()
    for r in rows:
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_matchup_display(r)} | event {eid} "
                f"| first lock: {r.get('_event_starts_at') or 'n/a'}"
            )
    return "\n".join(lines)


def _format_game_totals_md(totals_rows: list[dict[str, Any]]) -> str:
    lines = ["# Game totals projection board", ""]
    if not totals_rows:
        lines.append("_No eligible totals markets._")
        return "\n".join(lines)
    for row in totals_rows:
        flags = row.get("quality_flags") or ""
        lines.append(
            f"- [{row.get('sport')}] {row.get('market_id')}: {row.get('selection')} "
            f"@ {row.get('line')} ({row.get('price')}) edge={row.get('edge_pct')} "
            f"actionable={row.get('actionable')} flags={flags}"
        )
    return "\n".join(lines)


def write_pack(
    rows: list[dict[str, Any]],
    out_dir: Path,
    freshness_lines: list[str] | None = None,
    *,
    games_norm_by_league: dict[str, Any] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in DERIVED_PACK_OUTPUTS:
        (out_dir / name).unlink(missing_ok=True)
    dossiers_dir = out_dir / "dossiers"
    if dossiers_dir.exists():
        for stale_dossier in dossiers_dir.glob("*.md"):
            stale_dossier.unlink()
    with open(out_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATES_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "briefing.md").write_text(
        build_briefing(rows, out_dir.name, freshness_lines), encoding="utf-8"
    )
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
    with open(out_dir / "decisions.csv", "w", newline="", encoding="utf-8") as df:
        df.write("date,market_id,event_id,selection,decision,line_taken,price_taken,units,rationale\n")

    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER, build_game_totals

    totals_rows: list[dict[str, Any]] = []
    for lg, payload in (games_norm_by_league or {}).items():
        totals_rows.extend(build_game_totals(rows, payload, sport=lg))
    sections_dir = out_dir / "sections"
    sections_dir.mkdir(exist_ok=True)
    with open(out_dir / "game_totals.csv", "w", newline="", encoding="utf-8") as tf:
        writer = csv.DictWriter(tf, fieldnames=GAME_TOTALS_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(totals_rows)
    (sections_dir / "game_totals.md").write_text(_format_game_totals_md(totals_rows), encoding="utf-8")

def build_pack(
    leagues: Sequence[str], requested_date: str | None, top_ev_n: int, top_signal_n: int
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    games_norm_by_league: dict[str, Any] = {}
    for raw_league in leagues:
        lg = raw_league.strip().upper()
        if not lg:
            continue
        lp = paths.league_paths(lg)
        cards_dir = lp.root / "cards"
        norm = lp.normalized
        low = lg.lower()
        games_norm = load_json(norm / f"{low}_games_latest.json")
        games_norm_by_league[lg] = games_norm
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
    kept, locked = drop_locked_events(kept)
    if locked:
        locked_ids = sorted({str(r.get("market_id")) for r in locked})
        sample = locked_ids[:10]
        logger.warning(
            "Dropped %d non-pregame candidate(s) — event started or start time is invalid; "
            "market sample=%s%s",
            len(locked),
            sample,
            " ..." if len(locked_ids) > len(sample) else "",
        )
    final_rows = rank_rows(kept, top_ev_n, top_signal_n)
    return final_rows, target_date, games_norm_by_league

def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="Build the daily AI research-desk pack.")
    parser.add_argument("--leagues", default="MLB,WNBA")
    parser.add_argument("--date")
    parser.add_argument("--top-ev-n", type=int, default=15)
    parser.add_argument("--top-signal-n", type=int, default=10)
    args = parser.parse_args(argv)
    leagues = args.leagues.split(",")
    final_rows, target_date, games_norm = build_pack(
        leagues, args.date, args.top_ev_n, args.top_signal_n
    )
    freshness = build_freshness_section(leagues)
    out_dir = paths.PROJECT_ROOT / "packs" / target_date
    write_pack(final_rows, out_dir, freshness, games_norm_by_league=games_norm)
    logger.info("Wrote %d rows to %s", len(final_rows), out_dir)
    return out_dir

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

