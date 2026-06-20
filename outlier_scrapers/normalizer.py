from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .registry import SportConfig, normalize_market, normalize_team


def _to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        # Normalize the unicode minus (U+2212) so odds like "−110" parse the
        # same way implied_probability() already handles them.
        text = str(value).replace("−", "-").replace(",", "").strip()
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def percent_number(value: Any) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return round(number, 3)


def implied_probability(american_odds: Any) -> float | None:
    text = str(american_odds or "").strip().upper().replace("−", "-")
    if not text:
        return None
    if text in {"EVEN", "EVENS"}:
        return 50.0
    price = _to_int(text)
    if not price:
        return None
    probability = 100.0 / (price + 100.0) if price > 0 else abs(price) / (abs(price) + 100.0)
    return round(probability * 100.0, 3)


def format_line_text(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return str(value or "").strip()
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def normalize_book_label(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    aliases = {
        "HARD_ROCK": "Hard Rock",
        "HARDROCK": "Hard Rock",
        "DRAFTKINGS": "DraftKings",
        "FANDUEL": "FanDuel",
        "BETMGM": "BetMGM",
        "CAESARS": "Caesars",
        "ESPN_BET": "ESPN Bet",
        "ESPNBET": "ESPN Bet",
        "FANATICS": "Fanatics",
        "PRIZEPICKS": "PrizePicks",
        "UNDERDOG": "Underdog",
    }
    compact = re.sub(r"[^A-Z0-9]", "", token.upper())
    return aliases.get(compact, token.replace("_", " ").title())


def parse_market_descriptor(outcome: dict[str, Any]) -> str:
    market_label = str(outcome.get("marketLabel") or "").strip()
    if " - " in market_label:
        return market_label.split(" - ", 1)[1].strip()
    proposition = str(outcome.get("proposition") or "").strip()
    return proposition.replace("_", " ").title() if proposition else ""


def parse_player_name(outcome: dict[str, Any]) -> str:
    market_label = str(outcome.get("marketLabel") or "").strip()
    if " - " in market_label:
        return market_label.split(" - ", 1)[0].strip()
    player_payload = outcome.get("player")
    if isinstance(player_payload, dict):
        return str(player_payload.get("name") or "").strip()
    return str(outcome.get("playerName") or outcome.get("name") or "").strip()


# Order matters: check the more specific "1st 3 innings" before "1st".
_SCOPE_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("first_3_innings", ("1st 3 innings", "first 3 innings")),
    ("first_5_innings", ("1st 5 innings", "first 5 innings")),
    ("first_inning", ("1st inning", "first inning")),
    ("first_half", ("1st half", "first half")),
    ("second_half", ("2nd half", "second half")),
    ("first_quarter", ("1st quarter", "first quarter")),
    ("second_quarter", ("2nd quarter", "second quarter")),
    ("third_quarter", ("3rd quarter", "third quarter")),
    ("fourth_quarter", ("4th quarter", "fourth quarter")),
)


def detect_scope(*labels: Any) -> str:
    """Return the game-scope of a market based on its label(s).

    Outlier reuses the same ``proposition`` for full-game and partial-game
    markets (e.g. "Hits" vs "1st Inning Hits"), with the scope only in the
    human label. Returns ``"full_game"`` when no partial-game token is found.
    """
    text = " ".join(str(label or "").lower() for label in labels)
    for scope, needles in _SCOPE_CHECKS:
        if any(needle in text for needle in needles):
            return scope
    return "full_game"


def build_schedule_index(
    schedule_payload: dict[str, Any],
    config: SportConfig,
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    events = schedule_payload.get("events", [])
    if not isinstance(events, list):
        return index
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("eventId") or event.get("id") or "").strip()
        away_payload = event.get("away") if isinstance(event.get("away"), dict) else {}
        home_payload = event.get("home") if isinstance(event.get("home"), dict) else {}
        away_raw = (
            away_payload.get("alias")
            or away_payload.get("abbr")
            or away_payload.get("name")
            or away_payload.get("teamName")
            or ""
        )
        home_raw = (
            home_payload.get("alias")
            or home_payload.get("abbr")
            or home_payload.get("name")
            or home_payload.get("teamName")
            or ""
        )
        away = normalize_team(config, away_raw)
        home = normalize_team(config, home_raw)
        if not event_id:
            continue
        matchup = f"{away or away_raw} @ {home or home_raw}".strip()
        index[event_id] = {
            "event_id": event_id,
            "matchup": matchup,
            "matchup_raw": matchup,
            "away": away,
            "away_raw": str(away_raw or "").strip() or None,
            "home": home,
            "home_raw": str(home_raw or "").strip() or None,
            "away_team_id": str(away_payload.get("teamId") or away_payload.get("id") or "").strip(),
            "home_team_id": str(home_payload.get("teamId") or home_payload.get("id") or "").strip(),
            "starts_at": (
                event.get("startTime")
                or event.get("startDate")
                or event.get("date")
                or event.get("scheduled")
            ),
        }
    return index


def build_team_index(
    schedule_payload: dict[str, Any],
    config: SportConfig,
) -> dict[str, dict[str, Any]]:
    """Map every teamId seen anywhere in the schedule to its alias.

    Used as a fallback when a prop's eventId is not in the schedule index
    (playerProps can cover more games than the schedule endpoint returns).
    """
    index: dict[str, dict[str, Any]] = {}
    events = schedule_payload.get("events", [])
    if not isinstance(events, list):
        return index
    for event in events:
        if not isinstance(event, dict):
            continue
        for side in ("away", "home"):
            payload = event.get(side) if isinstance(event.get(side), dict) else {}
            team_id = str(payload.get("teamId") or payload.get("id") or "").strip()
            if not team_id:
                continue
            raw = (
                payload.get("alias")
                or payload.get("abbr")
                or payload.get("name")
                or payload.get("teamName")
                or ""
            )
            index[team_id] = {
                "team": normalize_team(config, raw),
                "team_raw": str(raw or "").strip() or None,
            }
    return index


def _extract_team_context(
    outcome: dict[str, Any],
    event_info: dict[str, Any],
    team_index: dict[str, dict[str, Any]],
    config: SportConfig,
) -> tuple[str | None, str | None, str | None, str | None]:
    team_id = str(outcome.get("teamId") or "").strip()
    opp_id = str(outcome.get("oppTeamId") or "").strip()
    away_team_id = str(event_info.get("away_team_id") or "").strip()
    home_team_id = str(event_info.get("home_team_id") or "").strip()
    if team_id and team_id == away_team_id:
        return (
            event_info.get("away"),
            event_info.get("away_raw"),
            event_info.get("home"),
            event_info.get("home_raw"),
        )
    if team_id and team_id == home_team_id:
        return (
            event_info.get("home"),
            event_info.get("home_raw"),
            event_info.get("away"),
            event_info.get("away_raw"),
        )

    # Event-specific lookup missed (event not in schedule index). Fall back to
    # the global teamId -> alias index using teamId/oppTeamId.
    team = team_raw = opponent = opponent_raw = None
    if team_id and team_id in team_index:
        team = team_index[team_id]["team"]
        team_raw = team_index[team_id]["team_raw"]
    if opp_id and opp_id in team_index:
        opponent = team_index[opp_id]["team"]
        opponent_raw = team_index[opp_id]["team_raw"]

    if team is None and team_raw is None:
        raw_team = (
            outcome.get("team")
            or outcome.get("teamAlias")
            or outcome.get("teamAbbr")
            or outcome.get("teamName")
        )
        team = normalize_team(config, raw_team)
        team_raw = str(raw_team or "").strip() or None
    return team, team_raw, opponent, opponent_raw


def _books_from_outcome(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    book_odds = outcome.get("bookOdds") if isinstance(outcome.get("bookOdds"), dict) else {}
    ordered_books = outcome.get("books") if isinstance(outcome.get("books"), list) else list(book_odds.keys())
    books: list[dict[str, Any]] = []
    for book in ordered_books:
        book_token = str(book or "").strip()
        if not book_token:
            continue
        raw_book = book_odds.get(book_token) if isinstance(book_odds, dict) else None
        raw_odds = raw_book.get("odds") if isinstance(raw_book, dict) else None
        if raw_odds in (None, ""):
            continue
        books.append({"book": normalize_book_label(book_token), "odds": _to_int(raw_odds), "odds_raw": str(raw_odds)})
    return books


def _dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    market_identity = str(row.get("market_id") or row.get("market_raw") or "").strip()
    return (
        str(row.get("league") or ""),
        str(row.get("event_id") or ""),
        market_identity,
        str(row.get("player_raw") or row.get("player") or ""),
        str(row.get("side") or ""),
        str(row.get("line") or ""),
    )


def normalize_player_props(
    props_payload: dict[str, Any],
    schedule_payload: dict[str, Any],
    config: SportConfig,
) -> list[dict[str, Any]]:
    schedule_index = build_schedule_index(schedule_payload, config)
    team_index = build_team_index(schedule_payload, config)
    props = props_payload.get("props", [])
    if not isinstance(props, list):
        return []

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in props:
        if not isinstance(record, dict):
            continue
        outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else {}
        stats = record.get("stats") if isinstance(record.get("stats"), dict) else {}
        if outcome.get("active") is False:
            continue

        side = str(outcome.get("position") or "").strip().upper()
        line = _to_float(outcome.get("line"))
        player_raw = parse_player_name(outcome)
        market_raw = parse_market_descriptor(outcome)
        if side not in {"OVER", "UNDER"} or line is None or not player_raw:
            continue

        event_id = str(outcome.get("eventId") or "").strip()
        event_info = schedule_index.get(event_id, {})
        team, team_raw, opponent, opponent_raw = _extract_team_context(
            outcome, event_info, team_index, config
        )

        # Canonical market is full-game only. Scoped variants (1st inning, 1st
        # half, etc.) share the same proposition, so we gate them to market=None
        # and record the scope; market_raw still carries the full label.
        scope = detect_scope(outcome.get("marketLabel"), market_raw)
        if scope == "full_game":
            market = normalize_market(config, outcome.get("proposition")) or normalize_market(
                config, market_raw
            )
        else:
            market = None
        books = _books_from_outcome(outcome)

        raw_opp_rank = stats.get("oppRank") or outcome.get("oppRank")
        if config.opp_rank_applicable:
            opp_rank = _to_int(raw_opp_rank)
            opp_rank_signal = "available" if opp_rank is not None else "missing"
        else:
            opp_rank = None
            opp_rank_signal = "not_applicable"

        row = {
            "league": config.league_id,
            "event_id": event_id or None,
            "market_id": outcome.get("marketId"),
            "player": player_raw,
            "player_raw": player_raw,
            "team": team,
            "team_raw": team_raw,
            "opponent": opponent,
            "opponent_raw": opponent_raw,
            "matchup": event_info.get("matchup"),
            "matchup_raw": event_info.get("matchup_raw"),
            "market": market,
            "market_raw": market_raw or str(outcome.get("proposition") or "").strip() or None,
            "side": side,
            "line": line,
            "books": books,
            "best_odds": _to_int(outcome.get("bestOdds")),
            "ip_pct": implied_probability(outcome.get("bestOdds")),
            "l5_pct": percent_number(stats.get("l5")),
            "l10_pct": percent_number(stats.get("l10")),
            "l20_pct": percent_number(stats.get("l20")),
            "h2h_pct": percent_number(stats.get("h2h")),
            "season_pct": percent_number(stats.get("curSeason")),
            "previous_season_pct": percent_number(stats.get("prevSeason")),
            "opp_rank": opp_rank,
            "opp_rank_signal": opp_rank_signal,
            "sport_context": {
                "proposition": outcome.get("proposition"),
                "market_label": outcome.get("marketLabel"),
                "scope": scope,
                "outcome_id": outcome.get("outcomeId") or outcome.get("id"),
                "orf": record.get("orf"),
                "orf_score": record.get("orfScore"),
                "raw_opp_rank": raw_opp_rank,
                "event_starts_at": event_info.get("starts_at"),
            },
        }
        key = _dedupe_key(row)
        existing = deduped.get(key)
        if existing is None or len(row["books"]) > len(existing.get("books", [])):
            deduped[key] = row

    return sorted(
        deduped.values(),
        key=lambda row: (
            str(row.get("matchup_raw") or ""),
            str(row.get("player_raw") or ""),
            str(row.get("market_raw") or ""),
            str(row.get("side") or ""),
            float(row.get("line") or 0.0),
        ),
    )


def build_normalized_payload(
    *,
    config: SportConfig,
    props_payload: dict[str, Any],
    schedule_payload: dict[str, Any],
    source_url: str,
) -> dict[str, Any]:
    rows = normalize_player_props(props_payload, schedule_payload, config)
    pagination = props_payload.get("_page_summary") if isinstance(props_payload.get("_page_summary"), dict) else None
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "league": config.league_id,
        "source_url": source_url,
        "source_method": "api",
        "record_count": len(rows),
        "pagination": pagination,
        "primary_record_array": "records",
        "records": rows,
        "data_contract": {
            "dataset": "outlier_player_props",
            "version": "1.0",
            "intended_use": "Standalone Outlier props data for sport-specific betting triage.",
            "dedupe_key": "league+event_id+(market_id|market_raw)+player_raw+side+line",
        },
    }

