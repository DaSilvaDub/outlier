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
import math
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import feed_health, paths, probability_blend
from outlier_scrapers.game_totals import is_full_game_total
from outlier_scrapers.registry import (
    classify_foreign_market,
    get_sport_config,
    team_display_name,
)
from outlier_scrapers.sizing import compute_historical_edge, compute_sizing
from outlier_scrapers.schema import ValidationError, validate_candidate_row
from outlier_scrapers.utils import _local_date, _parse_start, drop_locked_events, _write_csv
from outlier_scrapers.team_totals import is_team_total_proposition

logger = logging.getLogger(__name__)

CANDIDATES_HEADER = [
    "sport",
    "event_id",
    "_event_starts_at",
    "market_id",
    "outcome_id",
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
    "model_prob_source",
    "market_consensus_prob",
    "independent_model_prob",
    "independent_push_prob",
    "independent_edge_pct",
    "final_blended_prob",
    "blend_market_weight",
    "blend_model_weight",
    "blend_weight_source",
    "blend_model_version",
    "blend_segment",
    "push_prob",
    "implied_prob",
    "edge_pct",
    "historical_edge_pct",
    "kelly_025_units",
    "max_units",
    "recommended_units_pre_news",
    "sizing_flags",
    "data_quality_flags",
    "data_quality_tier",
    "odds_range",
    "time_before_game",
    "hours_before_game",
    "board",
    "signal_flags",
    "hit_rate_component",
    "insight_component",
    "movement_component",
    "orf_component",
    "public_money_component",
    "actionable",
    "outlier_ev_pct",
    "outlier_kelly_pct",
    "local_ev_pct",
    "local_kelly_pct",
    "line_open",
    "line_now",
    "public_money_pct",
    "money_pct",
    "public_money_divergence_pct",
    "injury_flags",
    "research_leverage",
    "projection_distribution",
    "projection_mean",
    "projection_variance",
    "projection_quantiles",
    "projection_model_version",
    "projection_feature_hash",
    "projection_quality_flags",
    "source_timestamps",
]

NO_PUSH_MARKETS = {"MONEYLINE", "ML", "ML_3WAY", "MONEYLINE_3WAY"}

# House rule: HR prop markets are excluded from
# packs entirely (low hit-rate longshot markets). Covers both the normalized
# short codes (registry.py) and the raw proposition tokens.
EXCLUDED_MARKETS = {
    "HR",
    "HOME_RUNS",
    "WALKS_ALLOWED",
    "WALKSALLOWED",
    "PITCHER_WALKS",
    "PITCHING_WALKS",
    "WALKS ALLOWED",
}

# House rule: plus-money longshots (e.g. a Hits Over at +181) are hard-filtered
# from packs. Any candidate priced at +LONGSHOT_AMERICAN_PRICE or longer is dropped.
LONGSHOT_AMERICAN_PRICE = 150

# data_quality_flags that ROLE_BLOCK explicitly tells every reasoning pass to
# "stand the market/row down" on: the line or market itself is proven or
# presumed corrupt, so a stake recommendation would contradict our own
# instruction to the desk. Exact-match flags; cross_sport_market carries a
# dynamic ":<LEAGUE>" suffix and is matched by prefix below.
DISQUALIFYING_DQ_FLAGS = {
    "spread_sign_conflict",
    "movement_line_mismatch",
    "implausible_line",
    "non_numeric_line",
    "ev_line_fallback",
    "edge_suspect_stale_line",
    "edge_suspect_thin_liquidity",
    "SOURCE_INTEGRITY_FLAG",
    "LOCKED_OR_UNVERIFIED_EVENT",
    "SIDE_RESOLUTION_CONFLICT",
    "UNINDEXED_SLATE_GAME",
}
CROSS_SPORT_DQ_PREFIX = "cross_sport_market:"


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
        if not tok:
            continue
        upper_tok = str(tok).strip().upper()
        norm_tok = upper_tok.replace(" ", "_").replace("-", "_")
        clean_tok = norm_tok.replace("_", "")
        if (
            upper_tok in EXCLUDED_MARKETS
            or norm_tok in EXCLUDED_MARKETS
            or clean_tok in EXCLUDED_MARKETS
        ):
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
                    formatted = _format_injury(item)
                    if formatted:
                        flags.append(formatted)
                else:
                    flags.append(str(item))
        if flags:
            out[str(eid)] = " | ".join(flags)
    return out


# Cap free-text analysis so multi-player event strings stay pack-readable.
_INJURY_ANALYSIS_MAX_CHARS = 160


def _injury_return_date(injury: dict[str, Any]) -> str:
    """Normalize API returnDate / return_date to YYYY-MM-DD when possible.

    Returns empty string if missing or unparseable to avoid unnormalized text
    (e.g., "TBD", "2026/09/04") in ``ret YYYY-MM-DD`` flags.
    """
    raw = injury.get("returnDate") or injury.get("return_date") or ""
    text = str(raw).strip()
    if not text:
        return ""
    # Common shapes: "2026-09-04", "2026-09-04T00:00:00-0700"
    if (
        len(text) >= 10
        and text[4] == "-"
        and text[7] == "-"
        and text[:4].isdigit()
        and text[5:7].isdigit()
        and text[8:10].isdigit()
    ):
        return text[:10]
    return ""


def _format_injury(item: dict[str, Any]) -> str:
    """Render one injury from the live schema for pack ``injury_flags``.

    Preferred shape::

        First Last (Status; Body; ret YYYY-MM-DD): analysis…

    Nested fields (when present): ``injury.status``, body from
    ``injury.injury``, ``injury.returnDate``, ``injury.analysis``.
    Missing pieces are omitted. Falls back to legacy ``player`` /
    ``description`` and never dumps the raw dict.
    """
    name = " ".join(part for part in (item.get("firstName"), item.get("lastName")) if part).strip()
    if not name:
        name = str(item.get("player") or item.get("description") or "").strip()

    raw_injury = item.get("injury")
    injury: dict[str, Any] = raw_injury if isinstance(raw_injury, dict) else {}
    raw_status = injury.get("status")
    status = str(raw_status).strip() if isinstance(raw_status, (str, int, float)) else ""
    # API body/diagnosis lives under nested key "injury" (e.g. "Right Forearm Strain").
    raw_body = injury.get("injury")
    body = str(raw_body).strip() if isinstance(raw_body, (str, int, float)) else ""
    ret = _injury_return_date(injury)
    raw_analysis = injury.get("analysis")
    analysis = str(raw_analysis).strip() if isinstance(raw_analysis, (str, int, float)) else ""

    paren_bits: list[str] = []
    if status:
        paren_bits.append(status)
    if body:
        paren_bits.append(body)
    if ret:
        paren_bits.append(f"ret {ret}")

    if name and paren_bits:
        core = f"{name} ({'; '.join(paren_bits)})"
    else:
        core = name

    if core and analysis:
        if len(analysis) > _INJURY_ANALYSIS_MAX_CHARS:
            analysis = analysis[: _INJURY_ANALYSIS_MAX_CHARS - 3].rstrip() + "..."
        core = f"{core}: {analysis}"
    return core


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
    # NaN/inf can reach here from an upstream feed (market_validation_flags
    # already flags it non_numeric_line) — int(f) raises ValueError on either,
    # which would crash pack generation for the whole slate over one bad row.
    # Fall back to the raw repr, same as an unparseable string above.
    if not math.isfinite(f):
        return str(line)
    return str(int(f)) if f == int(f) else str(f)


def _opportunity_key(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    """Return the normalized identity used to mark full-board rows as selected."""

    return (
        str(_coalesce(row.get("sport"), "")),
        str(_coalesce(row.get("event_id"), "")),
        str(_coalesce(row.get("market_id"), "")),
        str(_coalesce(row.get("outcome_id"), "")),
        str(_coalesce(row.get("selection"), "")),
        _fmt_line(row.get("line")),
    )


# Propositions where the line is a signed margin (point spread / run line / puck
# line) rather than a magnitude. A positive value here means the side is getting
# a cushion, not that it's the favorite — the same "1.5" that's unambiguous on a
# TOTAL is easy to mis-sign on a SPREAD, and different readers guess differently.
SIGNED_MARGIN_PROPOSITIONS = {"SPREAD"}


def _fmt_signed_line(line: Any, proposition: Any) -> str:
    """``_fmt_line`` plus an explicit leading '+' for positive signed-margin lines.

    Negative lines already render with '-' via ``_fmt_line``; only the positive
    case is ambiguous (a bare "1.5" reads as a magnitude, not "+1.5"), so that's
    the only case rewritten. Non-spread markets (totals, props) are untouched.
    """
    fl = _fmt_line(line)
    if not fl or str(proposition or "").strip().upper() not in SIGNED_MARGIN_PROPOSITIONS:
        return fl
    val = _to_float(line)
    if val is not None and val > 0 and not fl.startswith(("+", "-")):
        return f"+{fl}"
    return fl


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _is_original_recommendation(row: dict[str, Any]) -> bool:
    """Return whether a published candidate is an actionable morning recommendation."""

    units = _to_float(row.get("recommended_units_pre_news"))
    return (
        str(row.get("board") or "A").upper() == "A"
        and str(row.get("actionable") or "").lower() == "true"
        and units is not None
        and units > 0
    )


def _freeze_t30_originals(
    out_dir: Path,
    rows: list[dict[str, Any]],
    *,
    games_norm_by_league: dict[str, Any] | None,
    props_norm_by_league: dict[str, Any] | None,
    totals_rows: Sequence[dict[str, Any]] = (),
    target_date: str | None = None,
) -> None:
    """Freeze the first published recommendations and late-news baseline."""

    recommendations_path = out_dir / "original_recommendations.csv"
    context_path = out_dir / "original_t30_context.json"
    existing = (recommendations_path.exists(), context_path.exists())
    if existing == (True, True):
        return
    if existing != (False, False):
        raise ValidationError(
            "Incomplete T-30 original snapshot: original_recommendations.csv and "
            "original_t30_context.json must either both exist or both be absent."
        )

    injuries_by_league: dict[str, dict[str, str]] = {}
    lineups_by_league: dict[str, dict[str, Any]] = {}
    event_starts: dict[str, str] = {}
    probable_pitchers_by_league: dict[str, dict[str, dict[str, Any]]] = {}
    league_tokens = set((games_norm_by_league or {}).keys()) | set(
        (props_norm_by_league or {}).keys()
    )
    leagues = sorted(
        str(league).strip().upper() for league in league_tokens if str(league).strip()
    )
    from outlier_scrapers.probable_pitchers import load_probable_pitcher_lookup

    for league in leagues:
        games_payload = (games_norm_by_league or {}).get(league)
        props_payload = (props_norm_by_league or {}).get(league)
        injuries_by_league[league] = build_injuries(games_payload)
        event_context = ((games_payload or {}).get("context") or {}).get("events") or {}
        lineups_by_league[league] = {
            str(event_id): event.get("lineups")
            for event_id, event in event_context.items()
            if isinstance(event, dict) and isinstance(event.get("lineups"), dict)
        }
        event_starts.update(build_event_starts(props_payload, games_payload))
        probable_pitchers_by_league[league] = load_probable_pitcher_lookup(league)

    pack_date = target_date if target_date else out_dir.name
    try:
        datetime.strptime(pack_date, "%Y-%m-%d")
    except ValueError:
        pack_date = ""
    if pack_date:
        event_starts = {
            event_id: start
            for event_id, start in event_starts.items()
            if _local_date(str(start) if start is not None else None) == pack_date
        }

    def normalize_original(row: dict[str, Any]) -> dict[str, Any]:
        if row.get("total_kind") not in (None, ""):
            normalized: dict[str, Any] = {field: "" for field in CANDIDATES_HEADER}
            total_side = str(row.get("best_side") or "").upper()
            candidate_match = next(
                (
                    candidate
                    for candidate in rows
                    if str(candidate.get("sport") or "").upper()
                    == str(row.get("sport") or "").upper()
                    and str(candidate.get("event_id") or "") == str(row.get("event_id") or "")
                    and str(candidate.get("market_id") or "")
                    == str(row.get("market_id") or "")
                    and _to_float(candidate.get("line")) == _to_float(row.get("line"))
                    and total_side in str(candidate.get("selection") or "").upper()
                ),
                {},
            )
            normalized.update(candidate_match)
            normalized.update(row)
            # totals_id is a pack representation key, not the current provider
            # outcome identity.  Prefer the source candidate's outcome; if it is
            # absent, leave it blank so T-30 requires the exact normalized side.
            normalized["outcome_id"] = candidate_match.get("outcome_id") or ""
            normalized["market_type"] = (
                "TEAM_PROP" if str(row.get("total_kind")).lower() == "team" else "GAMELINE"
            )
            normalized["board"] = "A"
            normalized["model_prob"] = row.get("final_blended_prob") or row.get(
                "market_consensus_prob"
            )
            normalized["model_prob_source"] = row.get("devig_source") or "totals_model"
            normalized["data_quality_flags"] = row.get("quality_flags") or ""
            normalized["_event_starts_at"] = event_starts.get(str(row.get("event_id")), "")
            return normalized
        return dict(row)

    def recommendation_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
        numeric_line = _to_float(row.get("line"))
        return (
            str(row.get("sport") or "").upper(),
            str(row.get("event_id") or ""),
            str(row.get("market_id") or ""),
            str(row.get("outcome_id") or ""),
            "" if numeric_line is None else f"{numeric_line:.10g}",
        )

    # Specialized totals rows are authoritative for totals also represented in
    # the candidate ledger, matching feedback._load_pack_rows semantics.
    original_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for source_row in [*totals_rows, *rows]:
        normalized = normalize_original(source_row)
        if not _is_original_recommendation(normalized):
            continue
        key = recommendation_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        original_rows.append(normalized)
    _write_csv(recommendations_path, CANDIDATES_HEADER, original_rows)

    context = {
        "schema_version": 1,
        "captured_at": datetime.now().astimezone().isoformat(),
        "event_starts": event_starts,
        "injuries_by_league": injuries_by_league,
        "lineups_by_league": lineups_by_league,
        "probable_pitchers_by_league": probable_pitchers_by_league,
    }
    context_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")


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


def build_selection(name: Any, label: Any, side: Any, line: Any, proposition: Any = None) -> str:
    name_s = str(name or "").strip()
    label_s = str(label or "").strip()
    side_s = str(side or "").strip()
    if name_s and label_s:
        ln = label_s.lower()
        nn = name_s.lower()
        if (
            ln.startswith(nn)
            or ln.startswith(nn + " ")
            or ln.startswith(nn + "-")
            or ln.startswith(nn + " -")
        ):
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
    fl = _fmt_signed_line(line, proposition)
    if fl:
        parts.append(fl)
    return " ".join(parts) if parts else (side_s if side_s else "")


def index_projections(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index eligible shadow projections by normalized outcome id."""

    indexed: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for projection in (payload or {}).get("projections") or []:
        if not isinstance(projection, dict) or projection.get("status") != "eligible":
            continue
        outcome_id = projection.get("row_id") or projection.get("outcome_id")
        if outcome_id not in (None, ""):
            key = str(outcome_id)
            if key in indexed:
                ambiguous.add(key)
            else:
                indexed[key] = projection
    for key in ambiguous:
        indexed.pop(key, None)
    return indexed


def apply_shadow_projection(
    row: dict[str, Any], projection: dict[str, Any] | None, expected_side: Any
) -> list[str]:
    """Populate independent fields without touching consensus or sizing fields."""

    if not projection:
        return []
    distribution = projection.get("distribution")
    if not isinstance(distribution, dict):
        return ["projection_invalid_distribution"]
    if projection.get("event_id") not in (None, "", row.get("event_id")):
        return ["projection_event_mismatch"]
    if projection.get("market_id") not in (None, "", row.get("market_id")):
        return ["projection_market_mismatch"]
    if str(projection.get("sport") or "").upper() not in (
        "",
        str(row.get("sport") or "").upper(),
    ):
        return ["projection_sport_mismatch"]
    projection_line = _to_float(distribution.get("line", projection.get("line")))
    row_line = _to_float(row.get("line"))
    if projection_line is not None and row_line is not None and projection_line != row_line:
        return ["projection_line_mismatch"]
    projection_side = str(distribution.get("side") or projection.get("side") or "").upper()
    side_aliases = {"YES": "OVER", "NO": "UNDER"}
    normalized_side = str(expected_side or "").upper()
    if side_aliases.get(normalized_side, normalized_side) not in ("", projection_side):
        return ["projection_side_mismatch"]
    win_prob = _to_float(distribution.get("win_prob"))
    push_prob = _to_float(distribution.get("push_prob"))
    if win_prob is None or not 0.0 <= win_prob <= 1.0:
        return ["projection_invalid_probability"]
    row["independent_model_prob"] = win_prob
    row["independent_push_prob"] = push_prob if push_prob is not None else ""
    implied_prob = _to_float(row.get("implied_prob"))
    if implied_prob is None:
        decimal_price = _to_float(row.get("decimal_price"))
        implied_prob = 1.0 / decimal_price if decimal_price and decimal_price > 0 else None
    if implied_prob is not None:
        row["independent_edge_pct"] = (win_prob - implied_prob) * 100.0
    row["projection_distribution"] = "discrete_pmf"
    row["projection_mean"] = distribution.get("mean", "")
    row["projection_variance"] = distribution.get("variance", "")
    quantiles = distribution.get("quantiles")
    row["projection_quantiles"] = json.dumps(quantiles, sort_keys=True) if quantiles else ""
    row["projection_model_version"] = distribution.get("model_version", "")
    row["projection_feature_hash"] = projection.get("feature_snapshot_hash", "")
    return []


def apply_learned_probability_blend(row: dict[str, Any], artifact: dict[str, Any] | None) -> None:
    """Apply an offline-fitted blend and refresh sizing when it is safe to do so."""

    if row.get("projection_quality_flags"):
        return
    blended = probability_blend.blend_probabilities(
        row.get("market_consensus_prob"),
        row.get("independent_model_prob"),
        artifact,
        row,
    )
    if blended is None:
        return
    row["blend_market_weight"] = blended["market_weight"]
    row["blend_model_weight"] = blended["model_weight"]
    row["blend_weight_source"] = blended["source"]
    row["blend_model_version"] = blended["model_version"]
    row["blend_segment"] = json.dumps(blended.get("segment") or {}, sort_keys=True)
    row["final_blended_prob"] = blended["final_probability"]

    if blended["model_weight"] <= 0:
        return
    row["model_prob"] = blended["final_probability"]
    row["model_prob_source"] = f"learned_blend:{blended['model_version']}"
    decimal_price = _to_float(row.get("decimal_price"))
    push_prob = _to_float(row.get("push_prob"))
    if decimal_price is None or push_prob is None:
        return
    sizing = compute_sizing(
        decimal_price=decimal_price,
        model_prob=blended["final_probability"],
        push_prob=push_prob,
    )
    row["implied_prob"] = sizing.implied_prob
    row["edge_pct"] = sizing.edge_pct
    row["kelly_025_units"] = sizing.kelly_025_units
    row["max_units"] = sizing.max_units
    row["recommended_units_pre_news"] = sizing.recommended_units_pre_news


def _apply_enforced_portfolio_units(row: dict[str, Any], allocated_units: Any) -> None:
    """Apply enforce-mode units without reviving a non-actionable recommendation."""

    units = _to_float(allocated_units)
    is_actionable = str(row.get("actionable") or "").lower() == "true"
    is_board_a = str(row.get("board") or "").upper() == "A"
    if is_actionable and is_board_a and units is not None and units > 0:
        row["recommended_units_pre_news"] = allocated_units
        return

    row["recommended_units_pre_news"] = ""
    if is_actionable:
        row["actionable"] = "false"
        if is_board_a:
            row["board"] = "A_FLAGGED"
        if row.get("_board") == "board_a":
            row["_board"] = "flagged"


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
    projections_by_outcome: dict[str, dict[str, Any]] | None = None,
    blend_artifact: dict[str, Any] | None = None,
    stream: str = "props",
    health_payload: dict[str, Any] | None = None,
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
    outcome_id = outcome_id or ref.get("outcome_id")
    event_id = card.get("event_id") or ref.get("event_id")
    market_token = card.get("market") or ref.get("market")
    market_type = card.get("market_type") or ref.get("market_type") or market_token
    proposition = (
        card.get("proposition")
        or ref.get("proposition")
        or card.get("market_label")
        or ref.get("market_label")
        or market_token
    )
    has_player = bool(
        card.get("player") or ref.get("player") or card.get("player_id") or ref.get("player_id")
    )
    if not (card.get("market_type") or ref.get("market_type")) and not has_player:
        prop_token = str(proposition or "").upper()
        if (
            prop_token != "TOTAL"
            and is_team_total_proposition(prop_token, sport=sport)
            and (card.get("team") or ref.get("team"))
        ):
            market_type = "TEAM_PROP"
        elif prop_token in {"TOTAL", "SPREAD", "MONEYLINE"}:
            market_type = "GAMELINE"
    if is_excluded_market(market_token, market_type):
        return None
    scope = card.get("scope") or ref.get("scope")
    market_type_upper = str(market_type or "").upper()
    is_total_proposition = (
        market_type_upper == "TEAM_PROP"
        and is_team_total_proposition(proposition, sport=sport)
    ) or (market_type_upper == "GAMELINE" and str(proposition or "").upper() == "TOTAL")
    if is_total_proposition and not is_full_game_total({"scope": scope}):
        # Period/partial-game totals (quarters, first-5-innings, etc.) are the
        # exclusive domain of the dedicated totals pipeline (game_totals.py),
        # which already excludes them from its own totals projection outputs
        # (Game/Team totals). Letting them through here — candidates.csv is
        # built by this function, not game_totals.py — surfaces them unlabeled
        # in the briefing's Top EV/signal cards, indistinguishable from the
        # full-game market for the same team/game (see BETTING REPORTS/GENERIC
        # /2026-08-08 review).
        return None
    row: dict[str, Any] = {k: "" for k in CANDIDATES_HEADER}
    row["sport"] = sport
    row["event_id"] = event_id
    row["market_id"] = market_id
    row["outcome_id"] = outcome_id
    row["market_type"] = market_type
    row["player_id"] = card.get("player_id") or ref.get("player_id")
    is_team_total = str(market_type or "").upper() == "TEAM_PROP" and is_team_total_proposition(
        proposition, sport=sport
    )
    name = (
        card.get("player")
        or ref.get("player")
        or ((card.get("team") or ref.get("team")) if is_team_total else None)
        or card.get("matchup")
        or ref.get("matchup")
    )
    label = (
        "Team Total"
        if is_team_total
        else (ref.get("market_label") or card.get("market_label") or market_token)
    )
    row["selection"] = build_selection(name, label, headline_side, line, proposition)
    # Human-readable context the normalizer already resolved. Surfacing it stops
    # the reasoning desk from guessing teams/markets off the hash event_id or a
    # terse code (e.g. LAS vs LVA, PT = Pitches Thrown).
    matchup = card.get("matchup") or ref.get("matchup")
    team = card.get("team") or ref.get("team")
    opponent = card.get("opponent") or ref.get("opponent")
    # A row whose own team is unresolved must not carry a populated opponent:
    # the desk reads opponent-only context as the player's side (2026-07-18
    # Carleton row showed her matchup's other team as the only team column).
    is_player_row = has_player or str(market_type or "").upper() == "PLAYER_PROP"
    team_enrichment_failed = not team and bool(opponent or is_player_row)
    if team_enrichment_failed:
        opponent = None
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
    if str(proposition or "").strip().upper() in SIGNED_MARGIN_PROPOSITIONS:
        signed_line = _fmt_signed_line(line, proposition)
        if signed_line:
            row["line"] = signed_line
    row["research_leverage"] = get_research_leverage(market_token, scope, sport)
    row["injury_flags"] = injuries.get(str(event_id), "") if event_id else ""
    row["source_timestamps"] = format_source_timestamps(source_ts)
    movement = side_view.get("movement") or {}
    row["line_open"] = movement.get("open_line")
    row["line_now"] = movement.get("current_line")
    public_money = side_view.get("public_money") or {}
    row["public_money_pct"] = _coalesce(
        public_money.get("public_money_pct"), public_money.get("percentage")
    )
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
    eligible = (
        bool(ev_summary) and not (ev_summary or {}).get("is_alt_line_fallback") and bool(usable)
    )
    if eligible:
        best_record_id = ev_summary.get("best_record_id")
        if best_record_id:
            usable = [r for r in usable if r.get("record_id") == best_record_id]
        if not usable:
            return None
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
        row["market_consensus_prob"] = model_prob
        row["final_blended_prob"] = model_prob
        if model_prob is not None:
            row["model_prob_source"] = (
                "local_devig"
                if str((ev_summary or {}).get("ev_source") or "").upper() == "LOCAL"
                else "outlier_devig"
            )
        if push_prob is None:
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
        if ev_summary and (ev_summary or {}).get("is_alt_line_fallback"):
            row["sizing_flags"] = "ev_line_fallback"
        elif ev_summary:
            row["sizing_flags"] = "no_book_decimal"
        proxy = side_view.get("proxy_market_edge") or {}
        best_odds = proxy.get("odds") if proxy else side_view.get("best_odds")
        row["price"] = best_odds
        row["decimal_price"] = american_to_decimal(best_odds)
        row["as_of"] = odds_ts if ev_summary else norm_ts
        per_book = side_view.get("per_book_odds") or {}
        if proxy:
            row["book"] = proxy.get("book")
        elif isinstance(per_book, dict) and per_book:
            row["book"] = next(iter(per_book.keys()), None)
        elif isinstance(per_book, list) and per_book and isinstance(per_book[0], dict):
            row["book"] = per_book[0].get("book")
        proxy_prob_pct = _to_float(proxy.get("fair_prob_pct")) if proxy else None
        if (
            proxy_prob_pct is not None
            and row.get("decimal_price") is not None
            and push_prob is not None
        ):
            model_prob = proxy_prob_pct / 100.0
            sizing = compute_sizing(
                decimal_price=row["decimal_price"], model_prob=model_prob, push_prob=push_prob
            )
            row["model_prob"] = model_prob
            row["market_consensus_prob"] = model_prob
            row["final_blended_prob"] = model_prob
            row["model_prob_source"] = "proxy_market_devig"
            row["implied_prob"] = sizing.implied_prob
            row["edge_pct"] = sizing.edge_pct
            row["kelly_025_units"] = (
                max(0.0, sizing.kelly_025_units) if sizing.kelly_025_units is not None else None
            )
            row["max_units"] = sizing.max_units
            row["sizing_flags"] = ";".join(
                filter(None, (str(row.get("sizing_flags") or ""), "proxy_market_probability"))
            )
    if is_longshot_price(row.get("price")):
        return None

    if row.get("decimal_price") is not None and row["decimal_price"] <= 1.20:
        return None

    projection_flags = apply_shadow_projection(
        row,
        (projections_by_outcome or {}).get(str(outcome_id))
        if outcome_id not in (None, "")
        else None,
        headline_side,
    )
    row["projection_quality_flags"] = ";".join(projection_flags)

    # Surface card-level quality flags and, for an EV alt-line fallback, the line
    # the EV/price was actually derived from (e.g. shown 9.0 but priced at 8.5),
    # so the desk sees the mismatch instead of silently trusting the shown line.
    dq_flags = [str(f) for f in (card.get("flags") or [])]
    movement_now = _to_float((side_view.get("movement") or {}).get("current_line"))
    if movement_now is not None and _to_float(line) is not None and movement_now != _to_float(line):
        dq_flags.append("movement_line_mismatch")
    if (ev_summary or {}).get("is_alt_line_fallback"):
        priced = _priced_line_from_ev(ev_records, ev_summary.get("best_record_id"))
        if priced is not None and _to_float(priced) != _to_float(line):
            row["priced_line"] = priced
            priced_display = _fmt_line(priced)
            if str(proposition or "").strip().upper() in SIGNED_MARGIN_PROPOSITIONS:
                row["priced_line"] = _fmt_signed_line(priced, proposition)
                priced_display = row["priced_line"]
            dq_flags = [f for f in dq_flags if f != "ev_line_fallback"]
            dq_flags.append(f"ev_line_fallback:priced_at={priced_display}")
    dq_flags += market_validation_flags(
        sport, card, ref, market_token, market_type, row.get("player_id"), line
    )
    if team_enrichment_failed:
        dq_flags.append("team_enrichment_failed")
    if matchup and team and not row["home_away"]:
        dq_flags.append("HOME_AWAY_UNRESOLVED")
    health_failures = feed_health.matching_failures(health_payload or {}, stream, row)
    if health_failures:
        dq_flags.append("SOURCE_INTEGRITY_FLAG")
        for failure in health_failures:
            feed = re.sub(r"[^a-z0-9_]+", "_", str(failure.get("feed") or "unknown").lower())
            reason = re.sub(
                r"[^a-z0-9_]+", "_", str(failure.get("reason") or "failed").lower()
            ).strip("_")
            dq_flags.append(f"source_health:{feed}:{reason or 'failed'}")
    event_starts_at = event_starts.get(str(event_id)) if event_id else None
    row["_event_starts_at"] = event_starts_at
    hours_to_game = probability_blend.hours_before_game(row.get("as_of"), event_starts_at)
    row["hours_before_game"] = round(hours_to_game, 4) if hours_to_game is not None else ""
    row["time_before_game"] = probability_blend.time_before_game_bucket(hours_to_game)
    row["odds_range"] = probability_blend.odds_range(row.get("price"))
    disqualifying = not DISQUALIFYING_DQ_FLAGS.isdisjoint(dq_flags) or any(
        f.startswith(CROSS_SPORT_DQ_PREFIX) for f in dq_flags
    )
    row["data_quality_tier"] = probability_blend.data_quality_tier(
        ";".join(dict.fromkeys(dq_flags)),
        row.get("projection_quality_flags"),
        disqualifying=disqualifying,
    )
    apply_learned_probability_blend(row, blend_artifact)
    # Stale-line edge gate: reverse line movement (line moved against this side)
    # plus thin liquidity means the devigged edge is a phantom — the market moved
    # sharply on prices we can't trust. The RLM/thin flags alone were already
    # ignored downstream (2026-07-11 Bonner O10.5 shipped 3.0u with both set), so
    # withhold the stake recommendation itself. edge_pct stays visible.
    if (
        row.get("recommended_units_pre_news") not in ("", None)
        and "reverse_line_movement" in dq_flags
        and "thin_liquidity" in dq_flags
    ):
        dq_flags.append("edge_suspect_stale_line")
        row["recommended_units_pre_news"] = ""
    edge_pct_val = _to_float(row.get("edge_pct"))
    if edge_pct_val is not None and edge_pct_val <= 0.035 and "thin_liquidity" in dq_flags:
        dq_flags.append("edge_suspect_thin_liquidity")
        row["recommended_units_pre_news"] = ""
    model_p = _to_float(row.get("model_prob"))
    dec_price = _to_float(row.get("decimal_price"))
    if (
        model_p is not None
        and model_p <= 0.525
        and dec_price is not None
        and dec_price >= 2.0
        and str(row.get("model_prob_source") or "").lower() == "proxy_market_devig"
    ):
        existing_flags = str(row.get("sizing_flags") or "")
        if "plus_money_speculative_edge" not in existing_flags:
            row["sizing_flags"] = f"{existing_flags};plus_money_speculative_edge".strip(";")

    disqualifying = (
        not DISQUALIFYING_DQ_FLAGS.isdisjoint(dq_flags)
        or any(f.startswith(CROSS_SPORT_DQ_PREFIX) for f in dq_flags)
        or any(f == "ev_line_fallback" or f.startswith("ev_line_fallback:") for f in dq_flags)
    )
    if row.get("recommended_units_pre_news") not in ("", None) and disqualifying:
        row["recommended_units_pre_news"] = ""

    units = _to_float(row.get("recommended_units_pre_news"))
    is_actionable = (
        card.get("board") == "A"
        and units is not None
        and units > 0
        and edge_pct_val is not None
        and edge_pct_val > 0
        and not disqualifying
        and not dq_flags
    )
    row["actionable"] = "true" if is_actionable else "false"
    if not is_actionable:
        row["recommended_units_pre_news"] = ""
    row["data_quality_flags"] = ";".join(dict.fromkeys(dq_flags))

    signal = side_view.get("signal") or {}
    movement_corroboration = _to_float(signal.get("movement_corroboration"))
    row["hit_rate_component"] = signal.get("hit_component", "")
    row["insight_component"] = signal.get("insight_component", "")
    row["movement_component"] = (
        50.0 + 25.0 * movement_corroboration if movement_corroboration is not None else ""
    )
    row["orf_component"] = signal.get("orf_component", "")
    # Board B public-money component (descriptive only; never EV / actionable).
    # Empty string when signal_score used the legacy four-way path (no PM).
    pm_component = signal.get("public_money_component", "")
    row["public_money_component"] = pm_component if pm_component is not None else ""
    pm_div = signal.get("public_money_divergence_pct", "")
    row["public_money_divergence_pct"] = pm_div if pm_div is not None else ""
    # Descriptive-only: edge implied by the raw recency hit rate. Reads
    # signal["hit_pct"] (None when Outlier had no recency data), NOT
    # hit_rate_component, whose 50.0 no-data default would fabricate an edge.
    hit_rate_pct = _to_float(signal.get("hit_pct"))
    hist_edge = compute_historical_edge(
        hit_rate_prob=hit_rate_pct / 100.0 if hit_rate_pct is not None else None,
        decimal_price=_to_float(row.get("decimal_price")),
        push_prob=_to_float(row.get("push_prob")),
    )
    row["historical_edge_pct"] = round(hist_edge, 4) if hist_edge is not None else ""
    signal_flags: list[str] = []
    for value, flag in (
        (_to_float(row.get("hit_rate_component")), "hit_rate_support"),
        (_to_float(row.get("insight_component")), "insight_support"),
        (_to_float(row.get("orf_component")), "orf_support"),
    ):
        if value is not None and value > 50.0:
            signal_flags.append(flag)
    if movement_corroboration is not None:
        if movement_corroboration > 0:
            signal_flags.append("movement_support")
        elif movement_corroboration < 0:
            signal_flags.append("movement_against")
    if signal.get("insight_conflict"):
        signal_flags.append("insight_conflict")
    # Public-money flags live only on signal_flags — never card.flags /
    # data_quality_flags (those feed actionable=false via not dq_flags).
    from outlier_scrapers.cards import public_money_signal_flags

    for flag in public_money_signal_flags(_to_float(row.get("public_money_divergence_pct"))):
        signal_flags.append(flag)
    row["signal_flags"] = ";".join(dict.fromkeys(signal_flags))

    if card.get("board") == "A":
        row["_board"] = "board_a" if row["actionable"] == "true" else "flagged"
        row["board"] = "A" if row["actionable"] == "true" else "A_FLAGGED"
    else:
        row["_board"] = "board_b"
        row["board"] = "B"
    row["_rank_value"] = card.get("rank_value") or 0.0
    row["_slug"] = _slug(card.get("matchup") or ref.get("matchup"))

    # Preserve any _raw_* or other upstream passthrough fields from card/ref/ev (AGENTS.md).
    for src in (card, ref, (ev_summary or {})):
        if isinstance(src, dict):
            for k, v in src.items():
                if k.startswith("_") and k not in row:
                    row[k] = v

    return row


def process_stream(
    cards_payload: dict | None,
    lm_payload: dict | None,
    norm_payload: dict | None,
    enrichment_payload: dict | None,
    sport: str,
    stream: str,
    event_starts: dict[str, str],
    injuries: dict[str, str],
    projections_payload: dict[str, Any] | None = None,
    blend_artifact: dict[str, Any] | None = None,
    health_payload: dict[str, Any] | None = None,
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
    if projections_payload:
        source_ts["projections"] = projections_payload.get("generated_at")
    odds_ts = lm_payload.get("generated_at") if lm_payload else None
    norm_ts = norm_payload.get("generated_at") if norm_payload else None
    ev_records = lm_payload.get("ev_records", []) if lm_payload else []
    by_outcome = index_ev_by_outcome(ev_records)
    projections_by_outcome = index_projections(projections_payload)
    rows: list[dict[str, Any]] = []
    for card in (cards_payload.get("board_a") or []) + (cards_payload.get("board_b") or []):
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
            projections_by_outcome,
            blend_artifact,
            stream,
            health_payload,
        )
        if row is not None:
            row["_stream"] = stream
            rows.append(row)
    return rows


def select_date(
    rows: list[dict[str, Any]], requested: str | None
) -> tuple[list[dict[str, Any]], str]:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    dated: set[str] = {d for r in rows if (d := _local_date(r.get("_event_starts_at"))) is not None}
    if not dated:
        return rows, (requested or today)
    if requested and requested in dated:
        target = requested
    else:
        target = today if today in dated else max(dated)
    kept = [r for r in rows if _local_date(r.get("_event_starts_at")) in (target, None)]
    if requested and requested not in dated:
        logger.warning(
            "Requested date %s has no events; emitting empty pack for that date.", requested
        )
    return kept, target


def rank_rows(rows: list[dict[str, Any]], top_ev_n: int, top_signal_n: int) -> list[dict[str, Any]]:
    # Immutability: do not mutate caller's rows. Create new objects (AGENTS.md).
    rows = [({**r, "_stream": "props"} if "_stream" not in r else r) for r in rows]
    board_a = [r for r in rows if r.get("_board") == "board_a"]
    board_b = [r for r in rows if r.get("_board") == "board_b"]
    flagged = [r for r in rows if r.get("_board") == "flagged"]

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

    ev_audit = round_robin_then_fill(board_a + flagged, top_ev_n)
    ev = [row for row in ev_audit if row.get("_board") == "board_a"]
    audit = [row for row in ev_audit if row.get("_board") == "flagged"]
    sig = round_robin_then_fill(board_b, top_signal_n)
    return ev + sig + audit


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


_TOTALS_NAME_RE = re.compile(r"^(.*?)\s+(?:Total O/U|Team Total)\s+")


def _totals_event_display(row: dict[str, Any]) -> str:
    """Best-effort matchup/team label for a totals row (no matchup field on
    GAME_TOTALS_HEADER — the name is embedded in `selection`)."""
    match = _TOTALS_NAME_RE.match(str(row.get("selection") or ""))
    if match:
        return match.group(1)
    return str(row.get("team") or "unknown matchup")


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
    "- Render ONLY fields present in the candidate rows. NEVER introduce a player, injury status, or pitcher not in the pack.",
    "- If a row has priced_line set (or a data_quality_flags entry like"
    " ev_line_fallback:priced_at=…), the EV/price were derived at priced_line, not the shown"
    " line — reconcile to priced_line before quoting an edge and note the mismatch.",
    "- data_quality_flags may also carry cross_sport_market:<LEAGUE>, implausible_line,"
    " non_numeric_line, spread_sign_conflict, movement_line_mismatch,"
    " edge_suspect_stale_line, edge_suspect_thin_liquidity, SOURCE_INTEGRITY_FLAG,"
    " SIDE_RESOLUTION_CONFLICT, or UNINDEXED_SLATE_GAME — treat any such row as a"
    " data artifact with actionable=false and verdict PASS / STAND-DOWN.",
    "- model_prob_source distinguishes Outlier EV devig from proxy_market_devig. The proxy"
    " source fills probability/edge/Kelly for auditability but is market-implied context,"
    " NOT an independent predictive model confirmation. Reasoning models MUST NOT double-count"
    " proxy market devigs as independent corroboration of an EV play.",
    "- Spread / run line / puck line rows already carry an explicit sign (e.g. '+1.5' or"
    " '-1.5' in the line and selection) — never re-derive or flip it from model_prob or"
    " the favorite/underdog assumption. model_prob on these rows is the probability that"
    " the STATED signed side covers, not the probability of winning the game; a heavily"
    " favored team can correctly show a positive (cushion) line if that is the side priced.",
    "- Variance taxonomy to anchor evaluation:",
    "   * High variance: 3PM, hits allowed, turnovers.",
    "   * Moderate variance: strikeouts, assists, points.",
    "- CORRELATION: rows sharing the same event_id (same matchup) are same-game"
    " legs. Do NOT size stacked same-event bets as independent — their outcomes"
    " are correlated (e.g. two props in one game, or a team side plus that game's"
    " total). Discount total stake across correlated legs rather than summing"
    " each leg's recommended_units_pre_news at face value.",
    "",
    "HOUSE RULES (all passes):",
    "- MLB player props (strict whitelist): SO, H, TB, OUTS, 2B, HRR, ER, BB only."
    " Doubles (2B) are UNDER-only. Team props: H, SO, BB, R, TOTAL only."
    " Game lines (ML/spread/total) are preserved.",
    "- HR markets are excluded from this desk entirely."
    " If one appears in the pack, treat it as a data error and stand it down.",
    f"- Plus-money longshots priced +{LONGSHOT_AMERICAN_PRICE} or longer (e.g. a Hits Over at +181)"
    " are filtered from this pack. If one appears, treat it as a data error and stand it down.",
    "- Lines are PREGAME-only: candidates whose event already started (first lock in the past)"
    " are filtered from this pack. If a card's as_of/source timestamps fall at or after its"
    " event's first lock, its lines are LIVE/in-play — treat the whole event as a data error"
    " and stand it down.",
    "",
    "REASONING PASSES (pack-only):",
    "- Use this pack ONLY. Do not use memory or the web.",
    "- Never invent or recall odds/lines. Every verdict quotes the exact market_id + line/price from the pack.",
    "- If you need info not in the pack, list it under NEEDS — do not guess.",
    "",
    "RESEARCH PASSES (web-enabled):",
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

# Versioned desk publications live under packs/<date>/verdicts/. That subtree
# must never appear here: rebuild cleanup uses Path.unlink(), which raises on
# a directory, and wiping the snapshot on every rebuild would discard the
# coherence contract. Retention is desk_snapshot.prune_publications only.
VERDICTS_SUBTREE = "verdicts"

DERIVED_PACK_OUTPUTS = (
    "candidate_coverage.json",
    "feed_health.json",
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
    "projections.jsonl",
    "portfolio_risk.json",
    "mlb_alt_bankroll_props.csv",
    "wnba_alt_bankroll_props.csv",
    "mlb_alt_spreads.csv",
    "wnba_alt_spreads.csv",
    "ultimate_alt.csv",
    "ultimate_alt_parlays.csv",
)


def clear_derived_pack_outputs(out_dir: Path) -> None:
    """Unlink derived files named in DERIVED_PACK_OUTPUTS.

    Never touches packs/<date>/verdicts/ (directory or any path under it).
    Directory entries are skipped so a future accidental add of ``verdicts``
    cannot crash rebuild with IsADirectoryError.
    """
    verdicts_root = (out_dir / VERDICTS_SUBTREE).resolve()
    for name in DERIVED_PACK_OUTPUTS:
        if name == VERDICTS_SUBTREE or str(name).replace("\\", "/").startswith(f"{VERDICTS_SUBTREE}/"):
            continue
        target = out_dir / name
        try:
            resolved = target.resolve()
        except OSError:
            continue
        if resolved == verdicts_root or verdicts_root in resolved.parents:
            continue
        if target.is_dir():
            continue
        target.unlink(missing_ok=True)

FEED_STATUS_FIELDS = (
    "props_status",
    "games_status",
    "insights_status",
    "injuries_status",
    "line_movement_status",
    "game_line_movement_status",
    "cards_status",
)


def build_freshness_section(
    leagues: Sequence[str],
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    lines = ["### Freshness / Coverage"]
    for raw in leagues:
        lg = raw.strip().upper()
        if not lg:
            continue
        health = (feed_health_by_league or {}).get(lg)
        if health is None:
            health = feed_health.build_feed_health(lg, write=True)
        safe, reasons = feed_health.validate_feed_health(health)
        statuses = ", ".join(
            f"{field.removesuffix('_status')}={health.get(field, 'missing')}"
            for field in FEED_STATUS_FIELDS
        )
        latest_age = health.get("latest_source_age")
        oldest_age = health.get("oldest_source_age")
        age_text = (
            f"{latest_age:.2f}h..{oldest_age:.2f}h"
            if isinstance(latest_age, (int, float)) and isinstance(oldest_age, (int, float))
            else "unknown"
        )
        verdict = "OK" if safe else "UNSAFE"
        lines.append(
            f"- {lg}: {verdict}; coverage={float(health.get('coverage_pct') or 0.0):.2f}%; "
            f"source_age={age_text}; {statuses}"
        )
        failed = health.get("failed_ids") or []
        if failed:
            shown = [
                f"{item.get('feed')}:{item.get('stream')}:{item.get('id_type')}:{item.get('id')}"
                for item in failed[:8]
                if isinstance(item, dict)
            ]
            more = f" (+{len(failed) - len(shown)} more)" if len(failed) > len(shown) else ""
            lines.append(f"  - failed_ids: {', '.join(shown)}{more}")
        if reasons:
            lines.append(f"  - gate: {'; '.join(reasons)}")
    return lines


def build_candidate_coverage_section(coverage: dict[str, dict[str, int]]) -> list[str]:
    """Explain exactly why a requested league did or did not reach the pack."""
    lines = ["### Candidate Coverage"]
    for league, stats in coverage.items():
        emitted = stats.get("emitted", 0)
        state = "ZERO CANDIDATES" if emitted == 0 else f"{emitted} emitted"
        lines.append(
            f"- {league}: {state}; cards={stats.get('cards', 0)}, "
            f"rows_built={stats.get('rows_built', 0)}, "
            f"date_filtered={stats.get('date_filtered', 0)}, "
            f"started_dropped={stats.get('started_dropped', 0)}, "
            f"unverified_start_dropped={stats.get('unverified_start_dropped', 0)}"
        )
    return lines


def build_briefing(
    rows: list[dict[str, Any]],
    target_date: str,
    freshness_lines: list[str] | None = None,
    totals_rows: list[dict[str, Any]] | None = None,
    team_totals_rows: list[dict[str, Any]] | None = None,
    coverage_lines: list[str] | None = None,
    ultimate_alt_rows: list[dict[str, Any]] | None = None,
) -> str:
    derived_market_ids = {
        (str(r.get("sport") or ""), str(r.get("market_id")))
        for r in (totals_rows or []) + (team_totals_rows or [])
        if r.get("market_id")
    }
    lines = [f"SLATE: {target_date}", ""]
    if freshness_lines:
        lines += freshness_lines + [""]
    if coverage_lines:
        lines += coverage_lines + [""]
    lines += ROLE_BLOCK + ["", "### Top EV cards"]
    for r in rows:
        if (
            r.get("_board") == "board_a"
            and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
        ):
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"({r.get('price')}) edge={r.get('edge_pct')} units={r.get('recommended_units_pre_news')} "
                f"| {_matchup_display(r)}"
            )
    lines += ["", "### Top signal cards"]
    for r in rows:
        if (
            r.get("_board") == "board_b"
            and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
        ):
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} @ {r.get('line')} "
                f"| {_matchup_display(r)}"
            )
    flagged = [
        r
        for r in rows
        if r.get("_board") == "flagged"
        and (str(r.get("sport") or ""), str(r.get("market_id"))) not in derived_market_ids
    ]
    if flagged:
        lines += ["", "### Non-actionable flagged cards"]
        for r in flagged:
            lines.append(
                f"- [{r.get('sport')}] {r.get('market_id')}: {r.get('selection')} "
                f"@ {r.get('line')} ({r.get('price')}) actionable=false "
                f"flags={r.get('data_quality_flags')}"
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
    # Games that produced only Game/Team Totals markets (no player/team-prop
    # candidates) never appear in `rows`, so without this they were silently
    # missing from the Slate index while still being quoted later in the
    # Game/Team totals tables — an unverifiable-looking event reference.
    for r in (totals_rows or []) + (team_totals_rows or []):
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_totals_event_display(r)} | event {eid} "
                f"| first lock: n/a (totals-only event)"
            )
    # Events that only produced Ultimate Alt Shadow legs (spreads/totals/props
    # priced solely by that lane) never appear in `rows`, `totals_rows`, or
    # `team_totals_rows` either, so they were silently absent from the Slate
    # index while still being quoted in the Ultimate Alt section below — the
    # exact "event not in the supplied Slate index" gap flagged reviewing the
    # 2026-08-08 GROK Ultimate Alt Shadow report (NYM @ PIT).
    for r in ultimate_alt_rows or []:
        eid = r.get("event_id")
        if eid and eid not in seen:
            seen.add(eid)
            lines.append(
                f"- {r.get('sport')} {_matchup_display(r)} | event {eid} "
                f"| first lock: {r.get('event_starts_at') or 'n/a'} (alt-shadow-only event)"
            )
    if totals_rows is not None:
        lines.append("")
        lines.append(_format_game_totals_md(totals_rows, title="### Game totals"))
    if team_totals_rows is not None:
        lines.append("")
        lines.append(_format_game_totals_md(team_totals_rows, title="### Team totals"))
    return "\n".join(lines)


def _format_game_totals_md(
    totals_rows: list[dict[str, Any]], title: str = "# Game totals projection board"
) -> str:
    lines = [title, ""]
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
    props_norm_by_league: dict[str, Any] | None = None,
    target_date: str | None = None,
    coverage: dict[str, dict[str, int]] | None = None,
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
    opportunity_rows: list[dict[str, Any]] | None = None,
    projection_records: list[dict[str, Any]] | None = None,
) -> None:

    # Validate all candidate rows against schema constraints
    for idx, row in enumerate(rows):
        row_errors = validate_candidate_row(row, CANDIDATES_HEADER)
        if row_errors:
            # If a critical field is missing or empty, raise ValidationError
            critical_mismatch = any(
                "Critical field" in err or "dictionary" in err for err in row_errors
            )
            if critical_mismatch:
                raise ValidationError(
                    f"Critical schema compatibility violation at row {idx}: {'; '.join(row_errors)}"
                )
            # Log minor issues as warnings
            for err in row_errors:
                logger.warning("Candidate row schema warning at index %d: %s", idx, err)

    from outlier_scrapers.portfolio import load_portfolio_policy
    import json

    policy = load_portfolio_policy()
    # Immutable enforce pack check
    if policy.mode == "enforce":
        sidecar_path = out_dir / "portfolio_risk.json"
        if sidecar_path.exists():
            with open(sidecar_path, "r", encoding="utf-8") as sf:
                try:
                    existing = json.load(sf)
                    if existing.get("mode") == "enforce":
                        raise ValueError(
                            "Enforce pack already exists for this slate. Refusing to overwrite immutable pack."
                        )
                except json.JSONDecodeError:
                    pass

        # 14-day shadow window check
        import sqlite3
        from outlier_scrapers import paths

        db_path = paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3"
        if not db_path.exists():
            raise ValueError(
                "Enforce mode refused: feedback.sqlite3 not found (0 shadow days). 14 required."
            )
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(decisions)")
                columns = {row[1] for row in cursor.fetchall()}
                if "portfolio_mode" in columns:
                    query = """
                        SELECT COUNT(DISTINCT SUBSTR(m.captured_at, 1, 10))
                        FROM market_snapshots m
                        JOIN decisions d ON m.snapshot_id = d.snapshot_id
                        WHERE d.portfolio_mode = 'shadow'
                    """
                else:
                    query = (
                        "SELECT COUNT(DISTINCT SUBSTR(captured_at, 1, 10)) FROM market_snapshots"
                    )
                cursor.execute(query)
                shadow_days = cursor.fetchone()[0]
        except Exception as e:
            raise ValueError(f"Enforce mode refused: failed to query shadow window: {e}")
        if shadow_days < 14:
            raise ValueError(
                f"Enforce mode refused: only {shadow_days} days of shadow history found. 14 required."
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    pack_date = target_date or out_dir.name
    clear_derived_pack_outputs(out_dir)
    dossiers_dir = out_dir / "dossiers"
    if dossiers_dir.exists():
        for stale_dossier in dossiers_dir.glob("*.md"):
            stale_dossier.unlink()

    # Totals rows (game + team) rarely carry an Outlier EV devig, which left
    # every OVER total with a blank model_prob/edge_pct. Fill them from the
    # two-sided book ladder blended with the recent-games L10 signal.
    if games_norm_by_league:
        from outlier_scrapers.totals_model import backfill_totals_probabilities

        rows = backfill_totals_probabilities(rows, games_norm_by_league)
        if opportunity_rows is not None:
            opportunity_rows = backfill_totals_probabilities(opportunity_rows, games_norm_by_league)

    from outlier_scrapers.game_totals import (
        GAME_TOTALS_HEADER,
        build_game_totals,
        TEAM_TOTALS_HEADER,
        build_team_totals,
    )
    from outlier_scrapers.portfolio import (
        project_risk_identity,
        collapse_duplicate_outcomes,
        unify_and_dedup_streams,
        allocate_portfolio_risk,
        load_portfolio_policy,
        policy_fingerprint,
    )
    from outlier_scrapers.probable_pitchers import load_probable_pitcher_lookup
    import subprocess

    totals_rows: list[dict[str, Any]] = []
    team_totals_rows: list[dict[str, Any]] = []
    for lg, payload in (games_norm_by_league or {}).items():
        probable_pitchers = load_probable_pitcher_lookup(lg)
        totals_rows.extend(
            build_game_totals(rows, payload, sport=lg, probable_pitchers=probable_pitchers)
        )
        team_totals_rows.extend(
            build_team_totals(rows, payload, sport=lg, probable_pitchers=probable_pitchers)
        )

    # Filter totals rows to ONLY keep games/teams active on the current target date slate
    slate_eids = {str(r.get("event_id")) for r in rows if r.get("event_id")}
    slate_matchups = {str(r.get("matchup")).upper() for r in rows if r.get("matchup")}
    slate_teams = set()
    for r in rows:
        if r.get("team"):
            slate_teams.add(str(r.get("team")).upper())
        if r.get("opponent"):
            slate_teams.add(str(r.get("opponent")).upper())

    if slate_eids or slate_matchups or slate_teams:
        totals_rows = [
            r
            for r in totals_rows
            if str(r.get("event_id")) in slate_eids
            or str(r.get("matchup")).upper() in slate_matchups
        ]
        team_totals_rows = [
            r
            for r in team_totals_rows
            if str(r.get("event_id")) in slate_eids
            or str(r.get("matchup")).upper() in slate_matchups
            or (r.get("team") and str(r.get("team")).upper() in slate_teams)
        ]

    # Build all legacy alternate artifacts first, then compare them on one
    # conservative, price-aware shadow surface.  The legacy files remain for
    # compatibility; only ultimate_alt is used for new shadow evaluation.
    from outlier_scrapers.alt_team_totals import (
        ALT_TEAM_TOTAL_PARLAYS_HEADER,
        ALT_TEAM_TOTALS_HEADER,
        build_alt_team_total_board,
        build_alt_team_total_parlays,
        format_alt_team_totals_md,
    )
    from outlier_scrapers.alt_bankroll_props import (
        ALT_BANKROLL_PROPS_HEADER,
        build_alt_bankroll_board,
    )
    from outlier_scrapers.alt_spreads import ALT_SPREADS_HEADER, build_alt_spreads_board
    from outlier_scrapers.alt_player_props import (
        ALT_PLAYER_PROPS_PARLAYS_HEADER,
        ALT_PLAYER_PROPS_HEADER,
        build_alt_player_props_board,
        build_alt_player_props_parlays,
        format_alt_player_props_md,
    )
    from outlier_scrapers.ultimate_alt import (
        ULTIMATE_ALT_HEADER,
        ULTIMATE_ALT_PARLAYS_HEADER,
        build_ultimate_alt_board,
        build_ultimate_alt_parlays,
        format_ultimate_alt_md,
    )

    alt_tt_rows: list[dict[str, Any]] = []
    alt_tt_parlays: list[dict[str, Any]] = []
    alt_spread_rows: list[dict[str, Any]] = []
    alt_total_rows: list[dict[str, Any]] = []
    bankroll_rows_by_league: dict[str, list[dict[str, Any]]] = {}
    for lg, payload in (games_norm_by_league or {}).items():
        league_tt_rows = build_alt_team_total_board(payload, league=lg, target_date=pack_date)
        alt_tt_rows.extend(league_tt_rows)
        alt_tt_parlays.extend(build_alt_team_total_parlays(league_tt_rows))
        bankroll_rows = build_alt_bankroll_board(payload, league=lg, target_date=pack_date)
        bankroll_rows_by_league[lg] = bankroll_rows
        alt_spread_rows.extend(build_alt_spreads_board(payload, league=lg, target_date=pack_date))
        alt_total_rows.extend(
            row
            for row in bankroll_rows
            if str(row.get("proposition") or "").upper() == "TOTAL"
            or str(row.get("market_type") or "").upper() == "TEAM_PROP"
        )
    alt_total_rows.extend(alt_tt_rows)

    alt_player_rows: list[dict[str, Any]] = []
    alt_player_parlays: list[dict[str, Any]] = []
    for lg, payload in (props_norm_by_league or {}).items():
        league_rows = build_alt_player_props_board(payload, league=lg, target_date=pack_date)
        alt_player_rows.extend(league_rows)
        alt_player_parlays.extend(build_alt_player_props_parlays(league_rows))

    ultimate_alt_rows = build_ultimate_alt_board(
        spread_rows=alt_spread_rows,
        total_rows=alt_total_rows,
        player_rows=alt_player_rows,
    )
    ultimate_alt_parlays = build_ultimate_alt_parlays(ultimate_alt_rows)

    # --- PORTFOLIO RISK ALLOCATION ---
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=str(Path(__file__).parent)
        ).strip()
    except Exception:
        git_sha = "unknown"

    policy = load_portfolio_policy()

    # Immutable enforce pack check
    if policy.mode == "enforce":
        sidecar_path = out_dir / "portfolio_risk.json"
        if sidecar_path.exists():
            with open(sidecar_path, "r", encoding="utf-8") as sf:
                try:
                    existing = json.load(sf)
                    if existing.get("mode") == "enforce":
                        raise ValueError(
                            "Enforce pack already exists for this slate. Refusing to overwrite immutable pack."
                        )
                except json.JSONDecodeError:
                    pass

        # 14-day shadow window check
        import sqlite3
        from outlier_scrapers import paths

        db_path = paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3"
        if not db_path.exists():
            raise ValueError(
                "Enforce mode refused: feedback.sqlite3 not found (0 shadow days). 14 required."
            )
        with sqlite3.connect(db_path) as conn:
            try:
                res = conn.execute(
                    "SELECT COUNT(DISTINCT SUBSTR(captured_at, 1, 10)) FROM market_snapshots"
                ).fetchone()
                days = res[0] if res else 0
                if days < 14:
                    raise ValueError(
                        f"Enforce mode refused: only {days} days of shadow history found. 14 required."
                    )
            except sqlite3.OperationalError:
                pass  # table might not exist in an empty db

    all_projected = []
    for stream_name, stream_rows in [
        ("candidates", rows),
        ("game_totals", totals_rows),
        ("team_totals", team_totals_rows),
    ]:
        for r in stream_rows:
            units_val = r.get("recommended_units_pre_news")
            if units_val not in (None, ""):
                r["units"] = float(units_val)
            proj = project_risk_identity(r, stream_name)
            proj["_original_ref"] = r
            proj["stream"] = stream_name
            all_projected.append(proj)
    ultimate_projected = []
    for r in ultimate_alt_rows:
        if r.get("shadow_status") != "QUALIFIED":
            continue
        risk_input = dict(r)
        risk_input.update(
            {
                "actionable": "true",
                "board": "A",
                "units": r.get("recommended_units_pre_news") or 0.5,
                "pre_cap_units": r.get("recommended_units_pre_news") or 0.5,
            }
        )
        proj = project_risk_identity(risk_input, "ultimate_alt")
        proj["_original_ref"] = r
        proj["_ultimate_shadow"] = True
        proj["stream"] = "ultimate_alt"
        ultimate_projected.append(proj)

    collapsed = collapse_duplicate_outcomes(all_projected)
    unified = unify_and_dedup_streams(collapsed, policy)
    alloc_result = allocate_portfolio_risk(unified, policy)

    # Ultimate Alt competes with every live stream for shadow capacity, but it
    # never changes live/enforce allocations until a manual promotion follows
    # the settlement release gate.
    combined_shadow = unify_and_dedup_streams(
        collapse_duplicate_outcomes([*all_projected, *ultimate_projected]), policy
    )
    combined_shadow_result = allocate_portfolio_risk(combined_shadow, policy)

    for u_row in unified:
        orig = u_row.pop("_original_ref", None)
        if orig is not None:
            wager_id = u_row.get("stable_wager_id")
            if wager_id and wager_id in alloc_result.allocated_units:
                u_row["portfolio_units"] = alloc_result.allocated_units[wager_id]
                if not policy.shadow_mode:
                    _apply_enforced_portfolio_units(u_row, u_row["portfolio_units"])
            for k, v in u_row.items():
                if policy.shadow_mode and k in (
                    "recommended_units_pre_news",
                    "actionable",
                    "board",
                ):
                    continue
                orig[k] = v

    for u_row in combined_shadow:
        if not u_row.get("_ultimate_shadow"):
            continue
        orig = u_row.get("_original_ref")
        if orig is None:
            continue
        wager_id = u_row.get("stable_wager_id")
        allocated = combined_shadow_result.allocated_units.get(wager_id, 0.0) if wager_id else 0.0
        orig["portfolio_shadow_units"] = allocated
        for key in (
            "stable_wager_id",
            "risk_market_family",
            "risk_subject_id",
            "correlation_cluster_ids",
            "missing_risk_identity",
        ):
            if key in u_row:
                orig[key] = u_row[key]
        if wager_id:
            orig["cap_reasons"] = ";".join(combined_shadow_result.cap_reasons.get(wager_id, []))

    legacy_units = sum(
        float(r.get("recommended_units_pre_news") or 0.0)
        for r in all_projected
        if str(r.get("actionable", "")).lower() == "true"
    )
    raw_kelly_units = sum(
        float(r.get("kelly_025_units") or 0.0)
        for r in all_projected
        if str(r.get("actionable", "")).lower() == "true"
    )
    pre_cap_units = sum(
        float(r.get("pre_cap_units", r.get("units", 0.0)))
        for r in unified
        if r.get("risk_role") == "PRIMARY" and str(r.get("actionable", "")).lower() == "true"
    )
    shadow_units = sum(alloc_result.allocated_units.values())
    ultimate_shadow_units = 0.0
    for shadow_row in combined_shadow:
        if not shadow_row.get("_ultimate_shadow"):
            continue
        shadow_wager_id = str(shadow_row.get("stable_wager_id") or "")
        if shadow_wager_id:
            ultimate_shadow_units += combined_shadow_result.allocated_units.get(
                shadow_wager_id, 0.0
            )
    final_units = shadow_units if not policy.shadow_mode else legacy_units

    bs_breakdown: dict[str, int] = {}
    for r in unified:
        bs = r.get("book_source", "unknown")
        bs_breakdown[bs] = bs_breakdown.get(bs, 0) + 1

    # --- Flat metrics consumed by portfolio_report.py / portfolio replay (A8) ---
    # A7 (this sidecar writer) and A8 (portfolio_report.py, plus the pinned
    # fixtures in test_portfolio_report.py / test_portfolio_replay.py) were
    # implemented in separate commits and never reconciled: the reader expects
    # flat keys (legacy_total_units, caps_binding, book_source_exposure, ...)
    # that this sidecar never wrote, so every real pack silently produced a
    # report of zeros. These are computed here, alongside the existing nested
    # diagnostic fields, using data already available above.
    caps_binding: dict[str, int] = {}
    for group_key in alloc_result.binding_constraints:
        cap_type = group_key.split(":", 1)[0]
        caps_binding[cap_type] = caps_binding.get(cap_type, 0) + 1

    book_source_exposure: dict[str, float] = {}
    zeroed_rows = 0
    for r in unified:
        if r.get("risk_role") != "PRIMARY":
            continue
        wager_id = r.get("stable_wager_id")
        allocated = alloc_result.allocated_units.get(wager_id, 0.0) if wager_id else 0.0
        bs = r.get("book_source", "unknown")
        book_source_exposure[bs] = round(book_source_exposure.get(bs, 0.0) + allocated, 4)
        pre_cap_row = float(r.get("pre_cap_units", r.get("units", 0.0)))
        if pre_cap_row > 0.0 and allocated == 0.0:
            zeroed_rows += 1

    missing_identity_counts = sum(1 for r in all_projected if r.get("missing_risk_identity"))
    duplicate_collapse_counts = (len(all_projected) - len(collapsed)) + (
        len(collapsed) - len(unified)
    )

    sidecar = {
        "schema_version": policy.schema_version,
        "policy_version": policy.policy_version,
        "policy_fingerprint": policy_fingerprint(policy),
        "mode": policy.mode,
        "code_git_sha": git_sha,
        "slate_date": out_dir.name,
        "row_counts": {
            "candidates": len(rows),
            "game_totals": len(totals_rows),
            "team_totals": len(team_totals_rows),
            "ultimate_alt": sum(
                1 for row in ultimate_alt_rows if row.get("shadow_status") == "QUALIFIED"
            ),
            "total_in_scope": len(all_projected),
            "unified": len(unified),
        },
        "unit_totals": {
            "legacy": legacy_units,
            "raw_kelly": raw_kelly_units,
            "pre_cap": pre_cap_units,
            "shadow": shadow_units,
            "ultimate_alt_shadow": ultimate_shadow_units,
            "final": final_units,
        },
        "cap_limits": {
            "max_wager_units": policy.max_wager_units,
            "max_daily_units": policy.max_daily_units,
            "max_event_units": policy.max_event_units,
            "max_player_units": policy.max_player_units,
            "max_team_units": policy.max_team_units,
            "max_market_type_units": policy.max_market_type_units,
            "max_correlated_cluster_units": policy.max_correlated_cluster_units,
            "max_book_units": policy.max_book_units,
        },
        "reserved_capacity": {},
        "cap_utilization": alloc_result.utilization,
        "binding_constraints": alloc_result.binding_constraints,
        "dedup_log": {
            "collapsed": len(all_projected) - len(collapsed),
            "unified": len(collapsed) - len(unified),
        },
        "missing_identity_warnings": missing_identity_counts,
        "book_source_breakdown": bs_breakdown,
        "quantization_only_difference_totals": 0,
        "order_invariance_hash": alloc_result.order_invariance_hash,
        # Flat aliases required by portfolio_report.py / portfolio replay.
        "legacy_total_units": legacy_units,
        "shadow_total_units": shadow_units,
        "caps_binding": caps_binding,
        "zeroed_rows": zeroed_rows,
        "missing_identity_counts": missing_identity_counts,
        "duplicate_collapse_counts": duplicate_collapse_counts,
        "book_source_exposure": book_source_exposure,
        # Ticket A1 (continuous, unrounded pre_cap_units) is not yet merged onto
        # this branch, so pre_cap_units currently falls back to the already
        # half-unit-rounded legacy `units` value (see project_risk_identity /
        # allocate_portfolio_risk). With no continuous baseline to diff
        # against, a genuine quantization-only difference can't be computed
        # yet -- report 0 with an explicit flag rather than a value that would
        # look measured but isn't.
        "quantization_differences": 0,
        "quantization_diff_measurable": False,
    }

    with open(out_dir / "portfolio_risk.json", "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
    # --- END PORTFOLIO RISK ALLOCATION ---

    with open(out_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATES_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    _freeze_t30_originals(
        out_dir,
        rows,
        games_norm_by_league=games_norm_by_league,
        props_norm_by_league=props_norm_by_league,
        totals_rows=[*totals_rows, *team_totals_rows],
        target_date=pack_date,
    )

    selected_keys = {_opportunity_key(row) for row in rows}
    opportunity_output: list[dict[str, Any]] = []
    for source_row in opportunity_rows if opportunity_rows is not None else rows:
        row = dict(source_row)
        opportunity_key = _opportunity_key(row)
        row["selected"] = "true" if opportunity_key in selected_keys else "false"
        opportunity_output.append(row)
    with open(out_dir / "opportunities.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=[*CANDIDATES_HEADER, "selected"], extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(opportunity_output)

    with open(out_dir / "projections.jsonl", "w", encoding="utf-8") as projection_file:
        for projection in projection_records or []:
            projection_file.write(json.dumps(projection, sort_keys=True) + "\n")

    (out_dir / "briefing.md").write_text(
        build_briefing(
            rows,
            out_dir.name,
            freshness_lines,
            totals_rows if games_norm_by_league is not None else None,
            team_totals_rows if games_norm_by_league is not None else None,
            build_candidate_coverage_section(coverage) if coverage is not None else None,
            ultimate_alt_rows,
        ),
        encoding="utf-8",
    )
    if coverage is not None:
        (out_dir / "candidate_coverage.json").write_text(
            json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8"
        )
    if feed_health_by_league is not None:
        (out_dir / "feed_health.json").write_text(
            json.dumps(feed_health_by_league, indent=2, sort_keys=True), encoding="utf-8"
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
    from outlier_scrapers.feedback import DECISION_FIELDS

    with open(out_dir / "decisions.csv", "w", newline="", encoding="utf-8") as df:
        csv.DictWriter(df, fieldnames=DECISION_FIELDS).writeheader()

    sections_dir = out_dir / "sections"
    sections_dir.mkdir(exist_ok=True)

    with open(out_dir / "game_totals.csv", "w", newline="", encoding="utf-8") as tf:
        writer = csv.DictWriter(tf, fieldnames=GAME_TOTALS_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(totals_rows)
    (sections_dir / "game_totals.md").write_text(
        _format_game_totals_md(totals_rows), encoding="utf-8"
    )

    with open(out_dir / "team_totals.csv", "w", newline="", encoding="utf-8") as tf:
        writer = csv.DictWriter(tf, fieldnames=TEAM_TOTALS_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(team_totals_rows)
    (sections_dir / "team_totals.md").write_text(
        _format_game_totals_md(team_totals_rows, title="# Team totals"), encoding="utf-8"
    )

    _write_csv(out_dir / "alt_team_totals.csv", ALT_TEAM_TOTALS_HEADER, alt_tt_rows)
    _write_csv(
        out_dir / "alt_team_total_parlays.csv",
        ALT_TEAM_TOTAL_PARLAYS_HEADER,
        alt_tt_parlays,
    )
    (sections_dir / "alt_team_totals.md").write_text(
        format_alt_team_totals_md(alt_tt_rows, alt_tt_parlays), encoding="utf-8"
    )

    for lg, bankroll_rows in bankroll_rows_by_league.items():
        _write_csv(
            out_dir / f"{lg.lower()}_alt_bankroll_props.csv",
            ALT_BANKROLL_PROPS_HEADER,
            bankroll_rows,
        )

    for lg in games_norm_by_league or {}:
        spread_rows = [row for row in alt_spread_rows if row.get("league") == lg]
        _write_csv(
            out_dir / f"{lg.lower()}_alt_spreads.csv",
            ALT_SPREADS_HEADER,
            spread_rows,
        )

    _write_csv(out_dir / "alt_player_props.csv", ALT_PLAYER_PROPS_HEADER, alt_player_rows)
    _write_csv(
        out_dir / "alt_player_props_parlays.csv",
        ALT_PLAYER_PROPS_PARLAYS_HEADER,
        alt_player_parlays,
    )
    (sections_dir / "alt_player_props.md").write_text(
        format_alt_player_props_md(alt_player_rows, alt_player_parlays), encoding="utf-8"
    )

    _write_csv(out_dir / "ultimate_alt.csv", ULTIMATE_ALT_HEADER, ultimate_alt_rows)
    _write_csv(
        out_dir / "ultimate_alt_parlays.csv",
        ULTIMATE_ALT_PARLAYS_HEADER,
        ultimate_alt_parlays,
    )
    (sections_dir / "ultimate_alt.md").write_text(
        format_ultimate_alt_md(ultimate_alt_rows, ultimate_alt_parlays),
        encoding="utf-8",
    )


def build_feed_health_by_league(leagues: Sequence[str]) -> dict[str, dict[str, Any]]:
    health_by_league: dict[str, dict[str, Any]] = {}
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        health = feed_health.build_feed_health(league, write=True)
        safe, reasons = feed_health.validate_feed_health(health)
        if not safe:
            raise RuntimeError(f"{league} feed health unsafe: {'; '.join(reasons)}")
        health_by_league[league] = health
    return health_by_league


def build_pack_with_coverage(
    leagues: Sequence[str],
    requested_date: str | None,
    top_ev_n: int,
    top_signal_n: int,
    *,
    opportunity_rows_out: list[dict[str, Any]] | None = None,
    blend_artifact: dict[str, Any] | None = None,
    feed_health_by_league: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], dict[str, dict[str, int]]]:
    all_rows: list[dict[str, Any]] = []
    games_norm_by_league: dict[str, Any] = {}
    coverage: dict[str, dict[str, int]] = {}
    for raw_league in leagues:
        lg = raw_league.strip().upper()
        if not lg:
            continue
        health_payload = (feed_health_by_league or {}).get(lg)
        if health_payload is None:
            health_payload = feed_health.build_feed_health(lg, write=True)
        safe, reasons = feed_health.validate_feed_health(health_payload)
        if not safe:
            raise RuntimeError(f"{lg} feed health unsafe: {'; '.join(reasons)}")
        lp = paths.league_paths(lg)
        cards_dir = lp.root / "cards"
        norm = lp.normalized
        low = lg.lower()
        games_norm = load_json(norm / f"{low}_games_latest.json")
        games_norm_by_league[lg] = games_norm
        props_norm = load_json(norm / f"{low}_props_latest.json")
        projections_payload = load_json(norm / f"{low}_projections_latest.json")
        event_starts = build_event_starts(props_norm, games_norm)
        injuries = build_injuries(games_norm)
        props_cards = load_json(cards_dir / f"{low}_cards_latest.json")
        games_cards = load_json(cards_dir / f"{low}_games_cards_latest.json")
        props_rows = process_stream(
            props_cards,
            load_json(norm / f"{low}_line_movement_latest.json"),
            props_norm,
            None,
            lg,
            "props",
            event_starts,
            injuries,
            projections_payload,
            blend_artifact,
            health_payload,
        )
        games_rows = process_stream(
            games_cards,
            load_json(norm / f"{low}_games_line_movement_latest.json"),
            games_norm,
            load_json(norm / f"{low}_games_enrichment_latest.json"),
            lg,
            "games",
            event_starts,
            injuries,
            projections_payload,
            blend_artifact,
            health_payload,
        )
        if not games_cards:
            logger.warning("%s: no game-cards stream found", lg)
        coverage[lg] = {
            "cards": sum(
                len((payload or {}).get(key) or [])
                for payload in (props_cards, games_cards)
                for key in ("board_a", "board_b")
            ),
            "rows_built": len(props_rows) + len(games_rows),
            "date_filtered": 0,
            "started_dropped": 0,
            "unverified_start_dropped": 0,
            "emitted": 0,
        }
        all_rows.extend(props_rows)
        all_rows.extend(games_rows)
    kept, target_date = select_date(all_rows, requested_date)
    for lg, stats in coverage.items():
        before = sum(1 for row in all_rows if row.get("sport") == lg)
        after = sum(1 for row in kept if row.get("sport") == lg)
        stats["date_filtered"] = before - after
    kept, locked = drop_locked_events(kept)
    for row in locked:
        league_stats = coverage.get(str(row.get("sport") or ""))
        if league_stats is None:
            continue
        if _parse_start(row.get("_event_starts_at")) is None:
            league_stats["unverified_start_dropped"] += 1
        else:
            league_stats["started_dropped"] += 1
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
    if opportunity_rows_out is not None:
        opportunity_rows_out.extend(dict(row) for row in kept)
    final_rows = rank_rows(kept, top_ev_n, top_signal_n)
    for lg, stats in coverage.items():
        stats["emitted"] = sum(1 for row in final_rows if row.get("sport") == lg)
    return final_rows, target_date, games_norm_by_league, coverage


def build_pack(
    leagues: Sequence[str], requested_date: str | None, top_ev_n: int, top_signal_n: int
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Backward-compatible pack builder; coverage-aware callers use the companion helper."""
    rows, target_date, games_norm, _coverage = build_pack_with_coverage(
        leagues, requested_date, top_ev_n, top_signal_n
    )
    return rows, target_date, games_norm


def load_props_norm_by_league(leagues: Sequence[str]) -> dict[str, Any]:
    """Load normalized player-prop payloads without changing pack-builder API."""
    payloads: dict[str, Any] = {}
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        league_root = paths.league_paths(league).normalized
        payloads[league] = load_json(league_root / f"{league.lower()}_props_latest.json")
    return payloads


def _retry_replace(src: Path, dst: Path, retries: int = 10, delay: float = 0.1) -> None:
    last_err = None
    for _ in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    if last_err:
        raise last_err


def _retry_rmtree(path: Path, retries: int = 10, delay: float = 0.1) -> None:
    last_err = None
    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    if last_err:
        raise last_err


def load_projection_records(leagues: Sequence[str]) -> list[dict[str, Any]]:
    """Load the league projection artifacts for pack-local audit freezing."""

    records: list[dict[str, Any]] = []
    for raw_league in leagues:
        league = raw_league.strip().upper()
        if not league:
            continue
        league_paths = paths.league_paths(league)
        payload = load_json(league_paths.normalized / f"{league.lower()}_projections_latest.json")
        records.extend((payload or {}).get("projections") or [])
    return records


def _swap_staged_pack(staging_dir: Path, out_dir: Path) -> Path | None:
    """Publish staging while retaining the prior pack for transaction rollback."""

    backup_dir = out_dir.parent / f".{out_dir.name}.feedback-backup-{uuid.uuid4().hex}"
    had_existing = out_dir.exists()
    if had_existing:
        _retry_replace(out_dir, backup_dir)
    try:
        _retry_replace(staging_dir, out_dir)
    except Exception:
        if had_existing and backup_dir.exists() and not out_dir.exists():
            _retry_replace(backup_dir, out_dir)
        raise
    return backup_dir if had_existing else None


def _restore_published_pack(out_dir: Path, backup_dir: Path | None) -> None:
    if out_dir.exists():
        _retry_rmtree(out_dir)
    if backup_dir is not None and backup_dir.exists():
        _retry_replace(backup_dir, out_dir)


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="Build the daily AI research-desk pack.")
    parser.add_argument("--leagues", default="MLB,WNBA")
    parser.add_argument("--date")
    parser.add_argument("--top-ev-n", type=int, default=15)
    parser.add_argument("--top-signal-n", type=int, default=10)
    parser.add_argument("--feedback-db", type=Path)
    parser.add_argument(
        "--blend-weights",
        type=Path,
        default=probability_blend.DEFAULT_WEIGHTS_PATH,
        help="Versioned learned-weight artifact; missing/insufficient history stays market-only.",
    )
    parser.add_argument(
        "--no-feedback-ledger",
        action="store_true",
        help="Build pack artifacts without writing the permanent feedback database.",
    )
    args = parser.parse_args(argv)
    leagues = args.leagues.split(",")
    feed_health_by_league = build_feed_health_by_league(leagues)
    blend_artifact = probability_blend.load_weight_artifact(args.blend_weights)
    opportunity_rows: list[dict[str, Any]] = []
    build_kwargs: dict[str, Any] = {
        "opportunity_rows_out": opportunity_rows,
        "feed_health_by_league": feed_health_by_league,
    }
    if blend_artifact is not None:
        build_kwargs["blend_artifact"] = blend_artifact
    final_rows, target_date, games_norm, coverage = build_pack_with_coverage(
        leagues,
        args.date,
        args.top_ev_n,
        args.top_signal_n,
        **build_kwargs,
    )
    props_norm_by_league = load_props_norm_by_league(leagues)
    projection_records = load_projection_records(leagues)
    freshness = build_freshness_section(leagues, feed_health_by_league)
    out_dir = paths.PROJECT_ROOT / "packs" / target_date
    if args.no_feedback_ledger:
        write_pack(
            final_rows,
            out_dir,
            freshness,
            games_norm_by_league=games_norm,
            props_norm_by_league=props_norm_by_league,
            target_date=target_date,
            coverage=coverage,
            feed_health_by_league=feed_health_by_league,
            opportunity_rows=opportunity_rows,
            projection_records=projection_records,
        )
    else:
        from outlier_scrapers import feedback

        feedback_db = args.feedback_db or (paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = out_dir.parent / f".{out_dir.name}.feedback-staging-{uuid.uuid4().hex}"
        if out_dir.exists():
            recommendation_path = out_dir / "original_recommendations.csv"
            context_path = out_dir / "original_t30_context.json"
            if recommendation_path.exists() != context_path.exists():
                raise ValidationError(
                    "Incomplete published T-30 original snapshot: refusing to rebuild the pack."
                )
            shutil.copytree(out_dir, staging_dir)
        conn = None
        backup_dir: Path | None = None
        published = False
        try:
            write_pack(
                final_rows,
                staging_dir,
                freshness,
                games_norm_by_league=games_norm,
                props_norm_by_league=props_norm_by_league,
                target_date=target_date,
                coverage=coverage,
                feed_health_by_league=feed_health_by_league,
                opportunity_rows=opportunity_rows,
                projection_records=projection_records,
            )
            conn = feedback.open_database(feedback_db)
            conn.execute("BEGIN IMMEDIATE")
            stats = feedback.capture_pack(
                staging_dir,
                feedback_db,
                recorded_pack_path=out_dir,
                connection=conn,
            )
            backup_dir = _swap_staged_pack(staging_dir, out_dir)
            published = True
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
            if published:
                _restore_published_pack(out_dir, backup_dir)
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise
        finally:
            if conn is not None:
                conn.close()
        if backup_dir is not None and backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                logger.warning("Could not remove prior pack backup %s: %s", backup_dir, exc)
        logger.info(
            "Captured %d feedback snapshots and %d decisions in %s",
            stats.snapshots,
            stats.decisions,
            feedback_db,
        )
    logger.info("Wrote %d rows to %s", len(final_rows), out_dir)
    return out_dir


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
