"""Atomic T-30 repricing of the morning recommendation snapshot."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from outlier_scrapers import feedback, pack, paths, probability_blend, refresh
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER, TEAM_TOTALS_HEADER
from outlier_scrapers.probable_pitchers import load_probable_pitcher_lookup
from outlier_scrapers.sizing import compute_sizing
from outlier_scrapers.utils import _parse_start, _write_csv

logger = logging.getLogger(__name__)

T30_STATUSES = (
    "KEEP",
    "REDUCE",
    "KILL_PRICE_MOVED",
    "KILL_LINE_MOVED",
    "KILL_INJURY",
    "KILL_STARTER_CHANGE",
    "KILL_STALE_SOURCE",
    "MARKET_MISSING",
)
T30_FIELDS = [
    *pack.CANDIDATES_HEADER,
    "t30_status",
    "t30_repriced_at",
    "t30_original_price",
    "t30_original_decimal_price",
    "t30_original_units",
    "t30_notes",
]
T30_MIN_EDGE = 0.02
T30_SOURCE_MAX_AGE = timedelta(minutes=20)


class T30Error(RuntimeError):
    """Raised when a T-30 pass cannot be safely evaluated or published."""


@dataclass(frozen=True)
class T30RunResult:
    completed: bool
    reason: str
    first_lock_at: datetime
    due_at: datetime
    status_counts: dict[str, int]


@dataclass(frozen=True)
class _StreamState:
    records: list[dict[str, Any]]
    ev_records: list[dict[str, Any]]
    status: dict[str, Any]


RefreshRunner = Callable[[list[str] | None], int]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "y"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise T30Error(f"Required T-30 input is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_originals(pack_dir: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    recommendations = pack_dir / "original_recommendations.csv"
    context_path = pack_dir / "original_t30_context.json"
    if recommendations.exists() != context_path.exists():
        raise T30Error(
            "Incomplete T-30 original snapshot: recommendation and context files must coexist."
        )
    rows = _read_csv(recommendations)
    context = _read_json(context_path)
    if not context:
        raise T30Error(f"Required T-30 context is missing or invalid: {context_path}")
    return rows, context


def _pack_date(pack_dir: Path | None) -> str | None:
    if pack_dir is None:
        return None
    try:
        datetime.strptime(pack_dir.name, "%Y-%m-%d")
    except ValueError:
        return None
    return pack_dir.name


def _start_local_date(start: datetime) -> str:
    return start.astimezone().strftime("%Y-%m-%d")


def _parsed_starts(values: Sequence[Any]) -> list[datetime]:
    return [parsed for value in values if (parsed := _parse_start(value)) is not None]


def _slate_date(starts: Sequence[datetime], pack_date: str | None) -> str | None:
    if pack_date:
        return pack_date
    if not starts:
        return None
    counts: dict[str, int] = {}
    for start in starts:
        day = _start_local_date(start)
        counts[day] = counts.get(day, 0) + 1
    return max(counts, key=lambda day: (counts[day], day))


def first_lock_at(
    rows: Sequence[dict[str, Any]],
    context: dict[str, Any],
    *,
    pack_dir: Path | None = None,
) -> datetime:
    event_starts = context.get("event_starts")
    context_starts = (
        _parsed_starts(list(event_starts.values()))
        if isinstance(event_starts, dict)
        else []
    )
    row_starts = _parsed_starts([row.get("_event_starts_at") for row in rows])
    slate_date = _slate_date(context_starts or row_starts, _pack_date(pack_dir))

    def on_slate(start: datetime) -> bool:
        return slate_date is None or _start_local_date(start) == slate_date

    starts = [start for start in context_starts if on_slate(start)]
    if not starts:
        starts = [start for start in row_starts if on_slate(start)]
    if not starts:
        if slate_date:
            raise T30Error(f"Cannot schedule T-30: no event starts on slate date {slate_date}.")
        raise T30Error("Cannot schedule T-30: original snapshot has no parseable first lock.")
    return min(starts)


def _completed_manifest(pack_dir: Path) -> bool:
    t30 = _read_json(pack_dir / "manifest.json").get("t30_reprice")
    return isinstance(t30, dict) and t30.get("status") == "completed"


def should_run_t30(
    pack_dir: Path,
    rows: Sequence[dict[str, Any]],
    context: dict[str, Any],
    *,
    now: datetime,
    force: bool = False,
) -> tuple[bool, str, datetime, datetime]:
    first_lock = first_lock_at(rows, context, pack_dir=pack_dir)
    due = first_lock - timedelta(minutes=30)
    if not rows:
        return False, "no_recommendations", first_lock, due
    if force:
        return True, "forced", first_lock, due
    if _completed_manifest(pack_dir):
        return False, "already_completed", first_lock, due
    if now < due:
        return False, "not_due", first_lock, due
    if now >= first_lock:
        raise T30Error(
            f"T-30 window has closed: first lock was {first_lock.isoformat()}."
        )
    return True, "due", first_lock, due


def _stream_for_row(row: dict[str, Any]) -> str:
    from outlier_scrapers.slate_quality import is_player_prop

    return "props" if is_player_prop(row) else "games"


def _refresh_leagues(
    leagues: Sequence[str],
    runner: RefreshRunner,
    *,
    pack_date: str | None = None,
) -> dict[str, int]:
    results: dict[str, int] = {}
    for league in leagues:
        argv = [
            "--league",
            league,
            "--props",
            "--games",
            "--line-movement",
            "--game-line-movement",
            "--probable-pitchers",
        ]
        if pack_date:
            argv.extend(["--date", pack_date])
        results[league] = runner(argv)
    return results


def _load_stream_state(league: str, stream: str) -> _StreamState:
    league_paths = paths.league_paths(league)
    low = league.lower()
    prefix = "games_" if stream == "games" else ""
    payload = _read_json(league_paths.normalized / f"{low}_{prefix}line_movement_latest.json")
    status_name = (
        "games_line_movement_status_latest.json"
        if stream == "games"
        else "line_movement_status_latest.json"
    )
    status = _read_json(league_paths.reports / status_name)
    raw_records = payload.get("records")
    raw_ev_records = payload.get("ev_records")
    records = (
        [row for row in raw_records if isinstance(row, dict)]
        if isinstance(raw_records, list)
        else []
    )
    ev_records = (
        [row for row in raw_ev_records if isinstance(row, dict)]
        if isinstance(raw_ev_records, list)
        else []
    )
    return _StreamState(records=records, ev_records=ev_records, status=status)


def _source_is_stale(
    state: _StreamState,
    market_id: str,
    *,
    now: datetime,
) -> bool:
    status_value = _text(state.status.get("status")).lower()
    if status_value not in {"ok", "partial"}:
        return True
    generated_at = _parse_start(state.status.get("generated_at"))
    if generated_at is None:
        return True
    age = now - generated_at.astimezone(now.tzinfo)
    if age < timedelta(minutes=-2) or age > T30_SOURCE_MAX_AGE:
        return True
    for error in state.status.get("fetch_errors") or []:
        if isinstance(error, dict) and _text(error.get("market_id")) == market_id:
            return True
    return False


def _row_side(row: dict[str, Any]) -> str:
    selection = _text(row.get("selection")).upper()
    if "UNDER" in selection:
        return "UNDER"
    if "OVER" in selection:
        return "OVER"
    return selection


def _same_line(left: Any, right: Any) -> bool:
    left_number = _float(left)
    right_number = _float(right)
    if left_number is None or right_number is None:
        return left in (None, "") and right in (None, "")
    return abs(left_number - right_number) < 1e-9


def _same_outcome(row: dict[str, Any], record: dict[str, Any]) -> bool:
    outcome_id = _text(row.get("outcome_id"))
    if outcome_id:
        return _text(record.get("outcome_id")) == outcome_id
    return _text(record.get("side")).upper() == _row_side(row)


def _teams_for_row(row: dict[str, Any]) -> set[str]:
    teams = {_text(row.get("team")).upper(), _text(row.get("opponent")).upper()}
    matchup = _text(row.get("matchup"))
    if " @ " in matchup:
        teams.update(part.strip().upper() for part in matchup.split(" @ ", 1))
    return {team for team in teams if team}


def _starter_changed(
    row: dict[str, Any], original_context: dict[str, Any], current_context: dict[str, Any]
) -> bool:
    league = _text(row.get("sport")).upper()
    event_id = _text(row.get("event_id"))
    original_lineups = ((original_context.get("lineups_by_league") or {}).get(league) or {}).get(
        event_id
    )
    current_lineups = ((current_context.get("lineups_by_league") or {}).get(league) or {}).get(
        event_id
    )
    if original_lineups and current_lineups and original_lineups != current_lineups:
        return True
    original_by_team = (
        (original_context.get("probable_pitchers_by_league") or {}).get(league) or {}
    )
    current_by_team = (
        (current_context.get("probable_pitchers_by_league") or {}).get(league) or {}
    )
    for team in _teams_for_row(row):
        original = original_by_team.get(team)
        current = current_by_team.get(team)
        if not isinstance(original, dict) or not isinstance(current, dict):
            continue
        original_pitcher = _text(original.get("pitcher"))
        current_pitcher = _text(current.get("pitcher"))
        if original_pitcher and original_pitcher != current_pitcher:
            return True
        if bool(original.get("confirmed")) and not bool(current.get("confirmed")):
            return True
    return False


def _injury_changed(
    row: dict[str, Any], original_context: dict[str, Any], current_context: dict[str, Any]
) -> bool:
    league = _text(row.get("sport")).upper()
    event_id = _text(row.get("event_id"))
    original = _text(
        ((original_context.get("injuries_by_league") or {}).get(league) or {}).get(event_id)
    )
    current = _text(
        ((current_context.get("injuries_by_league") or {}).get(league) or {}).get(event_id)
    )
    return bool(current) and current != original


def _late_context_is_stale(row: dict[str, Any], current_context: dict[str, Any]) -> bool:
    league = _text(row.get("sport")).upper()
    event_id = _text(row.get("event_id"))
    injury_leagues = set(current_context.get("injury_stale_leagues") or [])
    injury_events = set(current_context.get("injury_stale_events") or [])
    lineup_events = set(current_context.get("lineup_stale_events") or [])
    starter_leagues = set(current_context.get("starter_stale_leagues") or [])
    known_events_by_league = current_context.get("injury_known_events_by_league")
    known_events = (
        set(known_events_by_league.get(league) or [])
        if isinstance(known_events_by_league, dict)
        else None
    )
    known_starters_by_league = current_context.get("starter_known_teams_by_league")
    known_starter_teams = (
        set(known_starters_by_league.get(league) or [])
        if isinstance(known_starters_by_league, dict)
        else None
    )
    return (
        league in injury_leagues
        or f"{league}:{event_id}" in injury_events
        or f"{league}:{event_id}" in lineup_events
        or (known_events is not None and event_id not in known_events)
        or (league == "MLB" and league in starter_leagues)
        or (
            league == "MLB"
            and known_starter_teams is not None
            and not _teams_for_row(row).issubset(known_starter_teams)
        )
    )


def _kill(row: dict[str, Any], status: str, repriced_at: str, note: str) -> dict[str, Any]:
    updated = dict(row)
    updated["actionable"] = "false"
    updated["board"] = "A_FLAGGED"
    updated["recommended_units_pre_news"] = ""
    updated["t30_status"] = status
    updated["t30_repriced_at"] = repriced_at
    updated["t30_notes"] = note
    flags = [flag for flag in _text(updated.get("data_quality_flags")).split(";") if flag]
    flags.append(f"t30:{status}")
    updated["data_quality_flags"] = ";".join(dict.fromkeys(flags))
    return updated


def reprice_row(
    original: dict[str, Any],
    *,
    state: _StreamState,
    original_context: dict[str, Any],
    current_context: dict[str, Any],
    blend_artifact: dict[str, Any] | None,
    now: datetime,
    refresh_failed: bool = False,
) -> dict[str, Any]:
    row = dict(original)
    repriced_at = now.isoformat()
    original_units = _float(original.get("recommended_units_pre_news")) or 0.0
    row.update(
        {
            "t30_original_price": original.get("price", ""),
            "t30_original_decimal_price": original.get("decimal_price", ""),
            "t30_original_units": original.get("recommended_units_pre_news", ""),
        }
    )
    market_id = _text(row.get("market_id"))
    event_id = _text(row.get("event_id"))
    event_reference = next(
        (
            record
            for record in [*state.ev_records, *state.records]
            if _text(record.get("event_id")) == event_id
        ),
        {},
    )
    for field in ("team", "opponent", "matchup"):
        if not row.get(field) and event_reference.get(field):
            row[field] = event_reference[field]
    if (
        refresh_failed
        or _source_is_stale(state, market_id, now=now)
        or _late_context_is_stale(row, current_context)
    ):
        return _kill(row, "KILL_STALE_SOURCE", repriced_at, "current source failed freshness")
    if _starter_changed(row, original_context, current_context):
        return _kill(row, "KILL_STARTER_CHANGE", repriced_at, "probable starter changed")
    if _injury_changed(row, original_context, current_context):
        return _kill(row, "KILL_INJURY", repriced_at, "late injury context changed")

    market_records = [
        record for record in state.ev_records if _text(record.get("market_id")) == market_id
    ]
    if not market_records:
        return _kill(row, "MARKET_MISSING", repriced_at, "market absent from refreshed feed")
    outcome_records = [record for record in market_records if _same_outcome(row, record)]
    if not outcome_records:
        side = _row_side(row)
        side_records = [
            record for record in market_records if _text(record.get("side")).upper() == side
        ]
        if side_records and any(
            not _same_line(row.get("line"), record.get("current_line"))
            for record in side_records
        ):
            return _kill(row, "KILL_LINE_MOVED", repriced_at, "outcome moved to another line")
        return _kill(row, "MARKET_MISSING", repriced_at, "outcome absent from refreshed feed")
    exact_records = [
        record
        for record in outcome_records
        if _same_line(row.get("line"), record.get("current_line"))
        and record.get("is_active", True) is not False
    ]
    if not exact_records:
        return _kill(row, "KILL_LINE_MOVED", repriced_at, "original line is no longer available")

    original_book = _text(row.get("book")).casefold()
    book_records = [
        record
        for record in exact_records
        if _text(record.get("book")).casefold() == original_book
        and _float(record.get("book_decimal_odds")) is not None
    ]
    if not book_records:
        return _kill(row, "KILL_PRICE_MOVED", repriced_at, "original book price unavailable")
    best = max(book_records, key=lambda record: _float(record.get("book_decimal_odds")) or 0.0)
    decimal_price = _float(best.get("book_decimal_odds"))
    devig_decimal = _float(best.get("devig_decimal"))
    push_prob = _float(row.get("push_prob"))
    if decimal_price is None or not devig_decimal or push_prob is None:
        return _kill(row, "KILL_PRICE_MOVED", repriced_at, "price cannot be safely resized")

    market_probability = 1.0 / devig_decimal
    row["book"] = best.get("book")
    row["price"] = best.get("book_odds")
    row["decimal_price"] = decimal_price
    row["as_of"] = repriced_at
    row["market_consensus_prob"] = market_probability
    row["model_prob"] = market_probability
    row["model_prob_source"] = (
        "local_devig"
        if _text(best.get("ev_source")).upper() == "LOCAL"
        else "outlier_devig"
    )
    row["final_blended_prob"] = market_probability
    starts_at = _parse_start(row.get("_event_starts_at"))
    if starts_at is not None:
        hours = (starts_at - now.astimezone(starts_at.tzinfo)).total_seconds() / 3600.0
        row["hours_before_game"] = round(hours, 4)
        row["time_before_game"] = probability_blend.time_before_game_bucket(hours)
    row["odds_range"] = probability_blend.odds_range(row.get("price"))
    pack.apply_learned_probability_blend(row, blend_artifact)
    max_units = _float(row.get("max_units")) or 3.0
    sizing = compute_sizing(
        decimal_price=decimal_price,
        model_prob=market_probability,
        push_prob=push_prob,
        max_units=max_units,
        min_edge=T30_MIN_EDGE,
    )
    row["model_prob"] = market_probability
    row["implied_prob"] = sizing.implied_prob
    row["edge_pct"] = sizing.edge_pct
    row["kelly_025_units"] = sizing.kelly_025_units
    row["max_units"] = sizing.max_units
    row["outlier_ev_pct"] = best.get("calculated_ev_pct")
    new_units = sizing.recommended_units_pre_news
    if sizing.edge_pct is None or sizing.edge_pct < T30_MIN_EDGE or not new_units or new_units <= 0:
        return _kill(row, "KILL_PRICE_MOVED", repriced_at, "edge fell below T-30 threshold")

    capped_units = min(original_units, new_units)
    if capped_units <= 0:
        return _kill(row, "KILL_PRICE_MOVED", repriced_at, "no stake remains after repricing")
    row["recommended_units_pre_news"] = capped_units
    from outlier_scrapers.slate_quality import apply_predictor_gates

    apply_predictor_gates(row)
    units_after = _float(row.get("recommended_units_pre_news"))
    if str(row.get("actionable") or "").lower() != "true" or units_after is None or units_after <= 0:
        return _kill(row, "KILL_PRICE_MOVED", repriced_at, "predictor gates rejected restake")
    status = "REDUCE" if units_after < original_units - 1e-9 else "KEEP"
    row["recommended_units_pre_news"] = units_after
    row["board"] = "A"
    row["t30_status"] = status
    row["t30_repriced_at"] = repriced_at
    row["t30_notes"] = "stake capped at original recommendation"
    return row


def _build_current_context(leagues: Sequence[str], *, now: datetime) -> dict[str, Any]:
    injuries: dict[str, dict[str, str]] = {}
    lineups: dict[str, dict[str, Any]] = {}
    probable: dict[str, dict[str, dict[str, Any]]] = {}
    injury_stale_leagues: set[str] = set()
    injury_stale_events: set[str] = set()
    lineup_stale_events: set[str] = set()
    starter_stale_leagues: set[str] = set()
    injury_known_events_by_league: dict[str, list[str]] = {}
    starter_known_teams_by_league: dict[str, list[str]] = {}
    for league in leagues:
        league_paths = paths.league_paths(league)
        games = pack.load_json(
            league_paths.normalized / f"{league.lower()}_games_latest.json"
        )
        injuries[league] = pack.build_injuries(games)
        game_events = ((games or {}).get("context") or {}).get("events") or {}
        lineups[league] = {
            str(event_id): event.get("lineups")
            for event_id, event in game_events.items()
            if isinstance(event, dict) and isinstance(event.get("lineups"), dict)
        }
        injury_known_events_by_league[league] = sorted(
            str(event_id) for event_id in game_events if str(event_id)
        )
        games_status = _read_json(league_paths.reports / "games_status_latest.json")
        games_generated_at = _parse_start(games_status.get("generated_at"))
        games_age = (
            now - games_generated_at.astimezone(now.tzinfo)
            if games_generated_at is not None
            else None
        )
        if (
            _text(games_status.get("status")).lower() not in {"ok", "partial"}
            or games_age is None
            or games_age < timedelta(minutes=-2)
            or games_age > T30_SOURCE_MAX_AGE
        ):
            injury_stale_leagues.add(league)
        for error in games_status.get("fetch_errors") or []:
            if not isinstance(error, dict):
                continue
            event_id = _text(error.get("event_id"))
            step = _text(error.get("step"))
            if event_id and step == "injuries":
                injury_stale_events.add(f"{league}:{event_id}")
            if event_id and step == "matchup":
                lineup_stale_events.add(f"{league}:{event_id}")

        probable_payload = _read_json(league_paths.probable_pitchers_latest())
        probable_rows = probable_payload.get("by_team")
        probable[league] = (
            probable_rows if isinstance(probable_rows, dict) else load_probable_pitcher_lookup(league)
        )
        starter_known_teams_by_league[league] = sorted(str(team) for team in probable[league])
        if league == "MLB":
            probable_generated_at = _parse_start(probable_payload.get("generated_at"))
            probable_age = (
                now - probable_generated_at.astimezone(now.tzinfo)
                if probable_generated_at is not None
                else None
            )
            if (
                probable_age is None
                or probable_age < timedelta(minutes=-2)
                or probable_age > T30_SOURCE_MAX_AGE
            ):
                starter_stale_leagues.add(league)
    return {
        "injuries_by_league": injuries,
        "lineups_by_league": lineups,
        "probable_pitchers_by_league": probable,
        "injury_stale_leagues": sorted(injury_stale_leagues),
        "injury_stale_events": sorted(injury_stale_events),
        "lineup_stale_events": sorted(lineup_stale_events),
        "starter_stale_leagues": sorted(starter_stale_leagues),
        "injury_known_events_by_league": injury_known_events_by_league,
        "starter_known_teams_by_league": starter_known_teams_by_league,
    }


def _candidate_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    line = _float(row.get("line"))
    line_token = "" if line is None else f"{line:.10g}"
    return (
        _text(row.get("sport")).upper(),
        _text(row.get("event_id")),
        _text(row.get("market_id")),
        _text(row.get("outcome_id")),
        line_token,
    )


def _total_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    line = _float(row.get("line"))
    return (
        _text(row.get("sport")).upper(),
        _text(row.get("event_id")),
        _text(row.get("market_id")),
        "" if line is None else f"{line:.10g}",
        _row_side(row),
    )


def _update_specialized_totals(staging_dir: Path, updated_rows: list[dict[str, Any]]) -> None:
    by_total_identity = {_total_identity(row): row for row in updated_rows}
    copy_fields = (
        "price",
        "decimal_price",
        "book",
        "market_consensus_prob",
        "independent_model_prob",
        "final_blended_prob",
        "edge_pct",
        "implied_prob",
        "actionable",
        "recommended_units_pre_news",
        "sizing_flags",
        "push_prob",
        "as_of",
    )
    for filename, header in (
        ("game_totals.csv", GAME_TOTALS_HEADER),
        ("team_totals.csv", TEAM_TOTALS_HEADER),
    ):
        path = staging_dir / filename
        if not path.exists():
            continue
        current_rows = _read_csv(path)
        for current in current_rows:
            repriced = by_total_identity.get(_total_identity(current))
            if repriced is None:
                continue
            for field in copy_fields:
                current[field] = repriced.get(field, "")
            current["best_price"] = repriced.get("price", "")
            current["quality_flags"] = repriced.get("data_quality_flags", "")
        _write_csv(path, header, current_rows)


def _write_staged_outputs(
    staging_dir: Path,
    updated_rows: list[dict[str, Any]],
    *,
    first_lock: datetime,
    due: datetime,
    now: datetime,
    refresh_results: dict[str, int],
) -> dict[str, int]:
    _write_csv(staging_dir / "t30_reprice.csv", T30_FIELDS, updated_rows)
    by_identity = {_candidate_identity(row): row for row in updated_rows}
    candidates = _read_csv(staging_dir / "candidates.csv")
    current_candidates = [
        {**row, **by_identity.get(_candidate_identity(row), {})} for row in candidates
    ]
    _write_csv(staging_dir / "candidates.csv", pack.CANDIDATES_HEADER, current_candidates)
    _update_specialized_totals(staging_dir, updated_rows)

    counts = dict(sorted(Counter(_text(row.get("t30_status")) for row in updated_rows).items()))
    summary = {
        "status": "completed",
        "repriced_at": now.isoformat(),
        "first_lock_at": first_lock.isoformat(),
        "due_at": due.isoformat(),
        "refresh_exit_codes": refresh_results,
        "status_counts": counts,
    }
    (staging_dir / "t30_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_path = staging_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["t30_reprice"] = summary
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return counts


def run_t30_reprice(
    pack_dir: Path,
    *,
    feedback_db: Path = feedback.DEFAULT_DB_PATH,
    now: datetime | None = None,
    force: bool = False,
    refresh_runner: RefreshRunner = refresh.main,
) -> T30RunResult:
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise T30Error(f"Pack directory does not exist: {pack_dir}")
    current_time = now or datetime.now().astimezone()
    if current_time.tzinfo is None:
        raise T30Error("T-30 current time must be timezone-aware.")
    originals, original_context = _load_originals(pack_dir)
    should_run, reason, first_lock, due = should_run_t30(
        pack_dir, originals, original_context, now=current_time, force=force
    )
    if not should_run:
        return T30RunResult(False, reason, first_lock, due, {})

    leagues = sorted({_text(row.get("sport")).upper() for row in originals if row.get("sport")})
    if not leagues:
        raise T30Error("Original recommendation snapshot contains no leagues.")
    refresh_results = _refresh_leagues(
        leagues, refresh_runner, pack_date=_pack_date(pack_dir)
    )
    states = {
        (league, stream): _load_stream_state(league, stream)
        for league in leagues
        for stream in ("props", "games")
    }
    current_context = _build_current_context(leagues, now=current_time)
    blend_artifact = probability_blend.load_weight_artifact(
        probability_blend.DEFAULT_WEIGHTS_PATH
    )
    updated_rows = [
        reprice_row(
            original,
            state=states[(_text(original.get("sport")).upper(), _stream_for_row(original))],
            original_context=original_context,
            current_context=current_context,
            blend_artifact=blend_artifact,
            now=current_time,
            refresh_failed=refresh_results[_text(original.get("sport")).upper()] != 0,
        )
        for original in originals
    ]

    staging_dir = pack_dir.parent / f".{pack_dir.name}.t30-staging-{uuid.uuid4().hex}"
    shutil.copytree(pack_dir, staging_dir)
    conn = None
    backup_dir: Path | None = None
    published = False
    counts: dict[str, int] = {}
    try:
        counts = _write_staged_outputs(
            staging_dir,
            updated_rows,
            first_lock=first_lock,
            due=due,
            now=current_time,
            refresh_results=refresh_results,
        )
        conn = feedback.open_database(feedback_db)
        conn.execute("BEGIN IMMEDIATE")
        feedback.capture_t30_pack(
            staging_dir,
            feedback_db,
            recorded_pack_path=pack_dir,
            connection=conn,
        )
        backup_dir = pack._swap_staged_pack(staging_dir, pack_dir)
        published = True
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        if published:
            pack._restore_published_pack(pack_dir, backup_dir)
        if staging_dir.exists():
            pack._retry_rmtree(staging_dir)
        raise
    finally:
        if conn is not None:
            conn.close()
    if backup_dir is not None and backup_dir.exists():
        try:
            pack._retry_rmtree(backup_dir)
        except OSError as exc:
            logger.warning("Could not remove prior T-30 pack backup %s: %s", backup_dir, exc)
    return T30RunResult(True, reason, first_lock, due, counts)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Refresh and atomically reprice the T-30 card.")
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--feedback-db", type=Path, default=feedback.DEFAULT_DB_PATH)
    parser.add_argument("--force", action="store_true", help="Bypass timing/idempotency gates.")
    args = parser.parse_args(argv)

    from outlier_scrapers.daily_job import _acquire_writer_lock, _release_writer_lock

    lock_dir = _acquire_writer_lock()
    if lock_dir is None:
        logger.error("Writer lock conflict at packs/.daily_job_lock.")
        return 2
    try:
        result = run_t30_reprice(
            args.pack_dir,
            feedback_db=args.feedback_db,
            force=args.force,
        )
    except Exception as exc:
        logger.error("T-30 repricing failed: %s", exc)
        return 1
    finally:
        _release_writer_lock(lock_dir)
    if result.completed:
        logger.info("T-30 repricing completed: %s", result.status_counts)
    else:
        logger.info("T-30 repricing skipped: %s", result.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
