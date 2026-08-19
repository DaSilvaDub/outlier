from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from .registry import SportConfig, normalize_market, normalize_team
from .schema import (
    ValidationError,
    validate_raw_schedule,
    validate_raw_player_props,
    validate_raw_games,
    validate_normalized_props,
    validate_normalized_games,
)

logger = logging.getLogger(__name__)

# Strict MLB player-prop whitelist (full-game only). House rule source of truth —
# keep in lockstep with scripts/sync_agent_docs.py COMMON_INVARIANTS_BLOCK §1.
# Pitcher strikeouts only. Batter Ks normalize to BSO and stay excluded.
ALLOWED_MLB_PLAYER_PROPS = frozenset({"SO"})

# Strict MLB team-prop whitelist (full-game only). GAMELINE moneyline/spread/total
# are not TEAM_PROP and are never gated by this set. House rule §2.
# Team run totals only (canonical R / TOTAL). Hits, team Ks, and walks drop.
ALLOWED_MLB_TEAM_PROPS = frozenset({"R", "TOTAL"})

# Alt-board side policy. Standard SO/total rows may still be either side;
# MLB alt parlays are high-probability OVER legs from different games.
MLB_ALT_PLAYER_OVER_MARKETS = frozenset({"SO"})
MLB_ALT_TEAM_OVER_MARKETS = frozenset({"R", "TOTAL"})


def _market_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").strip().upper()).strip("_")


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
        "DK": "DraftKings",
        "FANDUEL": "FanDuel",
        "FD": "FanDuel",
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
# Include Outlier periodLabel abbreviations (1H, 6I, F5, 1st 7I, 7-9I, …).
_SCOPE_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("first_3_innings", ("1st 3 innings", "first 3 innings", "1st 3i", "f3")),
    ("first_5_innings", ("1st 5 innings", "first 5 innings", "1st 5i", "f5")),
    ("first_inning", ("1st inning", "first inning", "1i")),
    ("first_half", ("1st half", "first half", "1h")),
    ("second_half", ("2nd half", "second half", "2h")),
    ("first_quarter", ("1st quarter", "first quarter", "1q")),
    ("second_quarter", ("2nd quarter", "second quarter", "2q")),
    ("third_quarter", ("3rd quarter", "third quarter", "3q")),
    ("fourth_quarter", ("4th quarter", "fourth quarter", "4q")),
)

# Compact periodLabel tokens that are partial-game but not covered above
# (single innings 2I–9I, ranges 4-6I / 7-9I, first-N like 1st 7I).
_PARTIAL_PERIOD_RE = re.compile(
    r"(?:"
    r"\b\d+\s*-\s*\d+\s*i\b"  # 4-6I, 7-9I
    r"|\b1st\s*\d+\s*i\b"  # 1st 7I
    r"|\bfirst\s*\d+\s*innings?\b"
    r"|\b\d+\s*i\b"  # 2I, 6I, 9I
    r"|\bf\d+\b"  # F7 etc.
    r")",
    re.IGNORECASE,
)


def detect_scope(*labels: Any) -> str:
    """Return the game-scope of a market based on its label(s).

    Outlier reuses the same ``proposition`` for full-game and partial-game
    markets (e.g. "Hits" vs "1st Inning Hits"), with the scope only in the
    human label / periodLabel. Returns ``"full_game"`` when no partial-game
    token is found.
    """
    text = " ".join(str(label or "").lower() for label in labels)
    for scope, needles in _SCOPE_CHECKS:
        if any(needle in text for needle in needles):
            return scope
    if text and _PARTIAL_PERIOD_RE.search(text):
        return "partial_period"
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
                event.get("scheduledTime")
                or event.get("startTime")
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
    ordered_books = (
        outcome.get("books") if isinstance(outcome.get("books"), list) else list(book_odds.keys())
    )
    books: list[dict[str, Any]] = []
    for book in ordered_books:
        book_token = str(book or "").strip()
        if not book_token:
            continue
        raw_book = book_odds.get(book_token) if isinstance(book_odds, dict) else None
        raw_odds = raw_book.get("odds") if isinstance(raw_book, dict) else None
        if raw_odds in (None, ""):
            continue
        books.append(
            {
                "book": normalize_book_label(book_token),
                "odds": _to_int(raw_odds),
                "odds_raw": str(raw_odds),
            }
        )
    return books


def _books_from_game_outcome(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-book prices for a game/team outcome.

    The Games API returns ``odds: [{book, american, decimal}]`` (flat list),
    unlike the props ``bookOdds`` map. We emit the same normalized book shape as
    ``_books_from_outcome`` (``book``/``odds``/``odds_raw``) so downstream card
    helpers stay feed-agnostic, plus ``decimal`` when present.
    """
    odds = outcome.get("odds")
    if not isinstance(odds, list):
        return []
    books: list[dict[str, Any]] = []
    for entry in odds:
        if not isinstance(entry, dict):
            continue
        book_token = str(entry.get("book") or "").strip()
        american = _to_int(entry.get("american"))
        if not book_token or american is None:
            continue
        books.append(
            {
                "book": normalize_book_label(book_token),
                "odds": american,
                "odds_raw": str(entry.get("american")),
                "decimal": _to_float(entry.get("decimal")),
            }
        )
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

        outcome_id = str(outcome.get("outcomeId") or outcome.get("id") or "").strip()
        side = str(outcome.get("position") or "").strip().upper()
        line = _to_float(outcome.get("line"))
        player_raw = parse_player_name(outcome)
        market_raw = parse_market_descriptor(outcome)
        # Stable outcome identity is required for exact joins to EV, movement,
        # insights, enrichment, feedback, and settlement records.  Drop an
        # unidentifiable source row instead of allowing a weaker market/side
        # fallback to make it appear usable downstream.
        if not outcome_id or side not in {"OVER", "UNDER"} or line is None or not player_raw:
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

        # House rule §1: pitcher strikeouts (SO) only. Every other player prop
        # is dropped at generation, including former whitelist members.
        if config.league_id == "MLB":
            if market not in ALLOWED_MLB_PLAYER_PROPS:
                continue
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
            "outcome_id": outcome_id,
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
            # ``position`` is the normalized schema name.  Keep ``side`` as a
            # compatibility alias for existing cards and movement consumers.
            "position": side,
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
                "outcome_id": outcome_id,
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
    # Schema validation gates
    schedule_errors = validate_raw_schedule(schedule_payload)
    if schedule_errors:
        critical = [err for err in schedule_errors if err.startswith("Schedule payload") or "'events' must be a list" in err]
        if critical:
            raise ValidationError(f"Critical schedule schema violation: {'; '.join(critical)}")
        for err in schedule_errors:
            logger.warning("Schedule schema warning: %s", err)

    props_errors = validate_raw_player_props(props_payload)
    if props_errors:
        critical = [err for err in props_errors if err.startswith("Player props payload") or "'props' must be a list" in err]
        if critical:
            raise ValidationError(f"Critical player props schema violation: {'; '.join(critical)}")
        for err in props_errors:
            logger.warning("Player props schema warning: %s", err)

    rows = normalize_player_props(props_payload, schedule_payload, config)

    normalized_errors = validate_normalized_props(rows)
    if normalized_errors:
        raise ValidationError(
            "Critical normalized props schema violation: " + "; ".join(normalized_errors)
        )

    pagination = (
        props_payload.get("_page_summary")
        if isinstance(props_payload.get("_page_summary"), dict)
        else None
    )
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
            "version": "1.1",
            "intended_use": "Standalone Outlier props data for sport-specific betting triage.",
            "dedupe_key": "league+event_id+(market_id|market_raw)+player_raw+side+line",
        },
    }


def game_sides(proposition: Any) -> tuple[str, ...]:
    token = str(proposition or "").strip().upper()
    if token in {"SPREAD", "MONEYLINE"}:
        return ("HOME", "AWAY")
    if token == "MONEYLINE_THREE_WAY":
        return ("HOME", "AWAY", "DRAW")
    if token == "TOTAL":
        return ("OVER", "UNDER")
    return ()


def normalize_games(
    *,
    config: SportConfig,
    schedule_payload: dict[str, Any],
    events_payloads: list[dict[str, Any]],
    source_url: str,
) -> dict[str, Any]:
    # Schema validation gates
    schedule_errors = validate_raw_schedule(schedule_payload)
    if schedule_errors:
        critical = [err for err in schedule_errors if err.startswith("Schedule payload") or "'events' must be a list" in err]
        if critical:
            raise ValidationError(f"Critical schedule schema violation: {'; '.join(critical)}")
        for err in schedule_errors:
            logger.warning("Schedule schema warning: %s", err)

    games_errors = validate_raw_games({"events": events_payloads})
    if games_errors:
        critical = [err for err in games_errors if err.startswith("Games payload") or "'events' must be a list" in err]
        if critical:
            raise ValidationError(f"Critical games schema violation: {'; '.join(critical)}")
        for err in games_errors:
            logger.warning("Games schema warning: %s", err)

    schedule_index = build_schedule_index(schedule_payload, config)

    rows: list[dict[str, Any]] = []
    events_context: dict[str, Any] = {}
    teams_context: dict[str, Any] = {}
    insights_context: dict[str, Any] = {}

    for event_data in events_payloads:
        event_id = str(event_data.get("eventId") or "").strip()
        if not event_id:
            continue
        event_info = schedule_index.get(event_id, {})

        matchup = event_data.get("matchup")
        if isinstance(matchup, dict):
            events_context[event_id] = {
                # Payload key is snake_case ("matchup_type"). The matchup
                # endpoint does NOT return a "teamRankings" field — it only
                # provides "lineups", a {home, away} dict each carrying
                # teamId/alias/status/players (verified live 2026-06-22).
                "matchup_type": matchup.get("matchup_type"),
                "lineups": matchup.get("lineups") or {},
                # Surface the schedule's team ids so build_injuries can join an
                # event to its teams' injuries. Sourced from the schedule (not
                # lineups) because MLB matchups ship empty lineups.
                "home_team_id": event_info.get("home_team_id"),
                "away_team_id": event_info.get("away_team_id"),
                "home": event_info.get("home"),
                "away": event_info.get("away"),
            }

        insights = event_data.get("insights")
        if isinstance(insights, list):
            insights_context[event_id] = insights

        for injury in event_data.get("injuries") or []:
            if not isinstance(injury, dict):
                continue
            team_id = str(injury.get("teamId") or injury.get("team_id") or "")
            if team_id:
                teams_context.setdefault(team_id, {}).setdefault("injuries", []).append(injury)

        for market in event_data.get("markets") or []:
            if not isinstance(market, dict):
                continue

            market_type = str(market.get("marketType") or "")
            proposition = str(market.get("proposition") or "")
            market_label = market.get("label")
            market_raw = parse_market_descriptor(market)
            first_inning_text = " ".join(
                str(value or "")
                for value in (proposition, market_label, market_raw, market.get("periodLabel"))
            ).upper()
            is_first_inning_game_prop = bool(
                re.search(
                    r"\b(?:NRFI|YRFI|FIRST[ _-]?INNING|1ST[ _-]?(?:INNING|INN)|1I)\b",
                    first_inning_text,
                )
            )
            # Player props are normalized by the player-prop path below.  Only
            # first-inning game props are admitted here for NRFI/YRFI; unrelated
            # GAME_PROP markets remain excluded until their model contracts exist.
            if market_type == "PLAYER_PROP" or (
                market_type == "GAME_PROP" and not is_first_inning_game_prop
            ):
                continue
            # periodLabel is often the only partial-game signal (e.g. "6I", "F5")
            # while label/raw stay "Total".
            scope = detect_scope(market_label, market_raw, market.get("periodLabel"))

            canonical_market = None
            if scope == "full_game":
                canonical_market = normalize_market(config, proposition) or normalize_market(
                    config, market_raw
                )

            if config.league_id == "MLB" and market_type == "TEAM_PROP":
                if canonical_market not in ALLOWED_MLB_TEAM_PROPS:
                    continue

            market_id = str(market.get("marketId") or "")

            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                outcome_id = str(outcome.get("outcomeId") or outcome.get("id") or "")

                position = (
                    str(outcome.get("position") or outcome.get("label") or "").strip().upper()
                )
                line = _to_float(outcome.get("line"))

                team, team_raw = None, None
                if market_type == "GAMELINE" and position in {"HOME", "AWAY"}:
                    team_key = position.lower()
                    team_raw_key = f"{team_key}_raw"
                    team = event_info.get(team_key)
                    team_raw = event_info.get(team_raw_key)
                elif market_type == "TEAM_PROP":
                    market_team_id = str(outcome.get("teamId") or market.get("teamId") or "")
                    if market_team_id:
                        if market_team_id == event_info.get("home_team_id"):
                            team = event_info.get("home")
                            team_raw = event_info.get("home_raw")
                        elif market_team_id == event_info.get("away_team_id"):
                            team = event_info.get("away")
                            team_raw = event_info.get("away_raw")
                    else:
                        label_lower = str(market_label or "").lower()
                        home_name = str(event_info.get("home_raw") or "").lower()
                        away_name = str(event_info.get("away_raw") or "").lower()
                        if home_name and home_name in label_lower:
                            team = event_info.get("home")
                            team_raw = event_info.get("home_raw")
                        elif away_name and away_name in label_lower:
                            team = event_info.get("away")
                            team_raw = event_info.get("away_raw")

                books = _books_from_game_outcome(outcome)
                public_money = None
                if "publicMoney" in market:
                    pm_array = market.get("publicMoney")
                    if isinstance(pm_array, list):
                        for pm in pm_array:
                            if str(pm.get("position") or "").strip().upper() == position:
                                public_money = pm
                                break
                if not public_money:
                    public_money = outcome.get("publicMoney")

                row = {
                    "league": config.league_id,
                    "event_id": event_id or None,
                    "event_starts_at": event_info.get("starts_at"),
                    "market_id": market_id or None,
                    "outcome_id": outcome_id or None,
                    "proposition": proposition or None,
                    "position": position or None,
                    "line": line,
                    "market": canonical_market,
                    "market_raw": market_raw or proposition or None,
                    "market_type": market_type or None,
                    "scope": scope,
                    "period_label": market.get("periodLabel"),
                    "periods": market.get("periods"),
                    "include_overtime": market.get("includeOvertime"),
                    "is_active": market.get("isActive"),
                    "team": team,
                    "team_raw": team_raw,
                    "matchup": event_info.get("matchup"),
                    "matchup_raw": event_info.get("matchup_raw"),
                    "books": books,
                    "public_money": public_money,
                    "stats": outcome.get("stats") or {},
                }
                rows.append(row)

    normalized_errors = validate_normalized_games(rows)
    if normalized_errors:
        for err in normalized_errors:
            logger.warning("Normalized games schema warning: %s", err)

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "league": config.league_id,
        "source_url": source_url,
        "source_method": "api",
        "record_count": len(rows),
        "primary_record_array": "records",
        "records": rows,
        "context": {
            "events": events_context,
            "teams": teams_context,
            "insights": insights_context,
        },
        "data_contract": {
            "dataset": "outlier_games",
            "version": "1.0",
            "intended_use": "Standalone Outlier games data.",
            "dedupe_key": "league+event_id+market_id+outcome_id",
        },
    }
