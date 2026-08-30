"""Candidate row selection policy for the pack writer.

``build_row`` turns one triage card into a candidate row (or rejects it). It
is split into focused phases below — identity resolution, base row assembly,
pricing/sizing, projections, quality/signal flags, and final actionable
resolution — each delegating to ``pack_market`` / ``pack_projections`` /
``pack_sizing`` rather than duplicating their policy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from outlier_scrapers import feed_health, probability_blend, slate_quality
from outlier_scrapers.game_totals import is_full_game_total
from outlier_scrapers.pack_market import (
    _align_gameline_team,
    _coalesce,
    _fmt_line,
    _fmt_signed_line,
    _home_away,
    _opponent_from_matchup,
    _priced_line_from_ev,
    _slug,
    _to_float,
    american_to_decimal,
    build_selection,
    get_research_leverage,
    is_excluded_market,
    is_longshot_price,
    is_no_push_market,
    market_validation_flags,
    match_ev_records,
    format_source_timestamps,
    SIGNED_MARGIN_PROPOSITIONS,
    _selected_ev_devig_decimal,
    _canonical_pack_date,
    _selection_side,
)
from outlier_scrapers.pack_projections import (
    _projection_side_conflicts,
    apply_learned_probability_blend,
    apply_shadow_projection,
)
from outlier_scrapers.registry import get_sport_config, team_display_name
from outlier_scrapers.schema import ValidationError
from outlier_scrapers.sizing import compute_historical_edge, compute_sizing
from outlier_scrapers.team_totals import is_team_total_proposition
from outlier_scrapers.utils import _local_date, _write_csv

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
    "blend_promotion_mode",
    "blend_sizing_source",
    "push_prob",
    "implied_prob",
    "edge_pct",
    "historical_edge_pct",
    "recency_hit_prob",
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
    "learned_source_probability",
    "learned_calibrated_probability",
    "learned_conservative_probability",
    "learned_raw_kelly_units",
    "calibration_multiplier",
    "uncertainty_multiplier",
    "correlation_multiplier",
    "drawdown_multiplier",
    "learned_pre_cap_units",
    "calibration_source",
    "uncertainty_source",
    "drawdown_source",
    "calibration_artifact_version",
    "uncertainty_artifact_version",
    "drawdown_tier",
    "learned_multiplier_status",
    "learned_multiplier_reason",
    "source_timestamps",
]

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
    "ev_probability_mismatch",
    "SOURCE_INTEGRITY_FLAG",
    "LOCKED_OR_UNVERIFIED_EVENT",
    "SIDE_RESOLUTION_CONFLICT",
    "UNINDEXED_SLATE_GAME",
}
CROSS_SPORT_DQ_PREFIX = "cross_sport_market:"


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


def _is_original_recommendation(row: dict[str, Any]) -> bool:
    """Return whether a published candidate is an actionable morning recommendation."""

    units = _to_float(row.get("recommended_units_pre_news"))
    return (
        str(row.get("board") or "A").upper() == "A"
        and str(row.get("actionable") or "").lower() == "true"
        and units is not None
        and units > 0
    )


def _total_reconciliation_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    line = _to_float(row.get("line"))
    return (
        str(row.get("sport") or "").strip().upper(),
        str(row.get("event_id") or "").strip(),
        str(row.get("market_id") or "").strip(),
        "" if line is None else f"{line:.10g}",
        _selection_side(row),
        str(row.get("team") or "").strip().upper(),
    )


def _reconcile_candidates_with_totals_board(
    rows: list[dict[str, Any]],
    totals_rows: list[dict[str, Any]],
    team_totals_rows: list[dict[str, Any]],
) -> None:
    """Demote candidates rejected by the exact specialized totals representation."""

    rejected: set[tuple[str, str, str, str, str, str]] = set()
    for total in [*totals_rows, *team_totals_rows]:
        key = _total_reconciliation_key(total)
        if (
            str(total.get("actionable") or "").strip().lower() != "true"
            and all(key[:5])
        ):
            rejected.add(key)
    for row in rows:
        if _total_reconciliation_key(row) not in rejected:
            continue
        if str(row.get("actionable") or "").lower() != "true" and str(
            row.get("board") or ""
        ).upper() != "A":
            continue
        flags = [
            flag.strip()
            for flag in str(row.get("data_quality_flags") or "").split(";")
            if flag.strip()
        ]
        if "totals_board_rejected" not in flags:
            flags.append("totals_board_rejected")
        row["data_quality_flags"] = ";".join(flags)
        row["actionable"] = "false"
        row["recommended_units_pre_news"] = ""
        if str(row.get("board") or "").upper() == "A" or row.get("_board") == "board_a":
            row["board"] = "A_FLAGGED"
            row["_board"] = "flagged"


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

    from outlier_scrapers.pack_context import build_event_starts, build_injuries

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

    pack_date = _canonical_pack_date(target_date, out_dir)
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", pack_date):
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


def _resolve_candidate_identity(
    card: dict[str, Any],
    ev_records: list[dict[str, Any]],
    by_outcome: dict[str, list[dict[str, Any]]],
    sport: str,
) -> dict[str, Any] | None:
    """Resolve a card's market identity, or ``None`` if it should be dropped."""

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
    token = str(market_token or "").upper()
    if token in slate_quality.PLAYER_PROP_HINTS and token != "PLAYER_PROP":
        market_type = token
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
    tok_check = (
        str(market_token or market_type or proposition or "")
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
        .upper()
    )
    if tok_check in {"DD", "TD", "DOUBLEDOUBLE", "TRIPLEDOUBLE"} and headline_side == "UNDER":
        return None
    scope = card.get("scope") or ref.get("scope")
    market_type_upper = str(market_type or "").upper()
    is_total_proposition = (
        market_type_upper == "TEAM_PROP" and is_team_total_proposition(proposition, sport=sport)
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
    return {
        "headline_side": headline_side,
        "side_view": side_view,
        "market_id": market_id,
        "outcome_id": outcome_id,
        "line": line,
        "ev_summary": ev_summary,
        "matched": matched,
        "ref": ref,
        "event_id": event_id,
        "market_token": market_token,
        "market_type": market_type,
        "proposition": proposition,
        "has_player": has_player,
        "scope": scope,
    }


def _build_base_row(
    card: dict[str, Any],
    identity: dict[str, Any],
    sport: str,
    source_ts: dict[str, str | None],
    event_starts: dict[str, str],
    injuries: dict[str, str],
) -> tuple[dict[str, Any], bool]:
    """Assemble identity/context fields. Returns ``(row, team_enrichment_failed)``."""

    headline_side = identity["headline_side"]
    side_view = identity["side_view"]
    market_id = identity["market_id"]
    outcome_id = identity["outcome_id"]
    line = identity["line"]
    ev_summary = identity["ev_summary"]
    ref = identity["ref"]
    event_id = identity["event_id"]
    market_token = identity["market_token"]
    market_type = identity["market_type"]
    proposition = identity["proposition"]

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
    team, opponent = _align_gameline_team(
        matchup=matchup,
        team=team,
        opponent=opponent,
        headline_side=headline_side,
        market_type=market_type,
        proposition=proposition,
    )
    # A row whose own team is unresolved must not carry a populated opponent:
    # the desk reads opponent-only context as the player's side (2026-07-18
    # Carleton row showed her matchup's other team as the only team column).
    is_player_row = identity["has_player"] or str(market_type or "").upper() == "PLAYER_PROP"
    team_enrichment_failed = not team and bool(opponent or is_player_row)
    if team_enrichment_failed:
        opponent = None
    elif team and not opponent:
        opponent = _opponent_from_matchup(matchup, team)
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
    row["research_leverage"] = get_research_leverage(market_token, identity["scope"], sport)
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
    row["_event_starts_at"] = event_starts.get(str(event_id)) if event_id else None
    return row, team_enrichment_failed


def _apply_price_and_sizing(
    row: dict[str, Any],
    identity: dict[str, Any],
    ev_records: list[dict[str, Any]],
    odds_ts: str | None,
    norm_ts: str | None,
) -> tuple[bool, bool]:
    """Price the row and compute sizing. Returns ``(accepted, ev_probability_mismatch)``."""

    side_view = identity["side_view"]
    ev_summary = identity["ev_summary"]
    matched = identity["matched"]
    market_token = identity["market_token"]
    line = identity["line"]

    no_push = is_no_push_market(market_token, line)
    push_prob = 0.0 if no_push else None
    row["push_prob"] = push_prob
    ev_probability_mismatch = False
    usable = [r for r in matched if r.get("book_decimal_odds") is not None]
    eligible = (
        bool(ev_summary) and not (ev_summary or {}).get("is_alt_line_fallback") and bool(usable)
    )
    if eligible:
        best_record_id = ev_summary.get("best_record_id")
        if best_record_id:
            usable = [r for r in usable if r.get("record_id") == best_record_id]
        if not usable:
            return False, ev_probability_mismatch
        best = sorted(
            usable,
            key=lambda r: (r.get("book_decimal_odds") or 0.0, r.get("calculated_ev_pct") or 0.0),
            reverse=True,
        )[0]
        row["book"] = best.get("book")
        row["price"] = best.get("book_odds")
        row["decimal_price"] = best.get("book_decimal_odds")
        row["as_of"] = odds_ts
        devig, has_selected_method_contract = _selected_ev_devig_decimal(best, ev_summary)
        model_prob = (1.0 / devig) if devig is not None else None
        if has_selected_method_contract and model_prob is None:
            ev_probability_mismatch = True
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
        proxy_decimal_price = row.get("decimal_price")
        if (
            proxy_prob_pct is not None
            and proxy_decimal_price is not None
            and push_prob is not None
        ):
            model_prob = proxy_prob_pct / 100.0
            sizing = compute_sizing(
                decimal_price=proxy_decimal_price, model_prob=model_prob, push_prob=push_prob
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
        return False, ev_probability_mismatch
    final_decimal_price = row.get("decimal_price")
    if final_decimal_price is not None and final_decimal_price <= 1.20:
        return False, ev_probability_mismatch
    return True, ev_probability_mismatch


def _apply_projection_fields(
    row: dict[str, Any],
    card: dict[str, Any],
    identity: dict[str, Any],
    projections_by_outcome: dict[str, dict[str, Any]] | None,
    probable_pitchers: dict[str, dict[str, Any]] | None,
) -> None:
    """Populate independent-projection fields, generating a fallback if needed."""

    ref = identity["ref"]
    outcome_id = identity["outcome_id"]
    headline_side = identity["headline_side"]

    incoming_projection = (
        (projections_by_outcome or {}).get(str(outcome_id))
        if outcome_id not in (None, "")
        else None
    )
    projection_flags = apply_shadow_projection(row, incoming_projection, headline_side)
    explicit_failed = bool(incoming_projection) and bool(projection_flags)
    if not row.get("independent_model_prob") and not explicit_failed:
        from outlier_scrapers.projections import (
            GAMELOG_SO_HASH,
            LEAGUE_AVG_SO_HASH,
            WNBA_GAMELOG_HASH,
            WNBA_MINUTES_HASH,
            get_wnba_player_features,
            independent_projection_eligible,
            mlb_so_projection_record,
            wnba_projection_record,
        )

        generated = mlb_so_projection_record(row, probable_pitchers)
        if generated is None and str(row.get("sport") or "").upper() == "WNBA":
            player_name = str(
                card.get("player") or ref.get("player") or row.get("player") or ""
            ).strip()
            if player_name:
                season = datetime.now().astimezone().year
                as_of = str(row.get("as_of") or "")
                if len(as_of) >= 4 and as_of[:4].isdigit():
                    season = int(as_of[:4])
                features = get_wnba_player_features(player_name, season=season)
                generated = wnba_projection_record(row, features=features)
        if generated:
            extra_flags = apply_shadow_projection(row, generated, headline_side)
            projection_flags = [*projection_flags, *extra_flags]
            if extra_flags:
                projection_flags.append("projection_fallback_rejected")
            else:
                digest = str(generated.get("feature_snapshot_hash") or "")
                if digest == LEAGUE_AVG_SO_HASH:
                    projection_flags.append("projection_audit_league_avg")
                elif digest == GAMELOG_SO_HASH and independent_projection_eligible(generated):
                    projection_flags.append("projection_independent_gamelog_so")
                elif digest == WNBA_GAMELOG_HASH:
                    projection_flags.append("projection_audit_wnba_gamelog")
                elif digest == WNBA_MINUTES_HASH:
                    projection_flags.append("projection_audit_wnba_minutes")
                elif not independent_projection_eligible(generated):
                    projection_flags.append("projection_audit_only")
    row["projection_quality_flags"] = ";".join(projection_flags)


def _apply_quality_and_signal_flags(
    row: dict[str, Any],
    card: dict[str, Any],
    identity: dict[str, Any],
    sport: str,
    ev_records: list[dict[str, Any]],
    ev_probability_mismatch: bool,
    team_enrichment_failed: bool,
    health_payload: dict[str, Any] | None,
    stream: str,
    blend_artifact: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """Build data-quality/signal flags and gates. Returns ``(disqualifying, dq_flags)``."""

    ref = identity["ref"]
    ev_summary = identity["ev_summary"]
    market_token = identity["market_token"]
    market_type = identity["market_type"]
    proposition = identity["proposition"]
    line = identity["line"]
    side_view = identity["side_view"]
    headline_side = identity["headline_side"]
    market_type_upper = str(market_type or "").upper()

    # Surface card-level quality flags and, for an EV alt-line fallback, the line
    # the EV/price was actually derived from (e.g. shown 9.0 but priced at 8.5),
    # so the desk sees the mismatch instead of silently trusting the shown line.
    dq_flags = [str(f) for f in (card.get("flags") or [])]
    if (identity["has_player"] or market_type_upper == "PLAYER_PROP") and _projection_side_conflicts(
        row, headline_side
    ):
        dq_flags.append("projection_side_conflict")
    if ev_probability_mismatch:
        dq_flags.append("ev_probability_mismatch")
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
    if row.get("matchup") and row.get("team") and not row["home_away"]:
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
    event_starts_at = row.get("_event_starts_at")
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
    injury_view = slate_quality.classify_injuries(
        str(row.get("injury_flags") or ""), row.get("team")
    )
    if slate_quality.usage_up_under(row, injury_view):
        dq_flags.append("usage_up_under")
    line_with_side = slate_quality.signed_line_moved_with_side(row)
    if line_with_side:
        dq_flags = [flag for flag in dq_flags if flag != "reverse_line_movement"]
    if (
        row.get("recommended_units_pre_news") not in ("", None)
        and "reverse_line_movement" in dq_flags
        and "thin_liquidity" in dq_flags
        and not line_with_side
    ):
        dq_flags.append("edge_suspect_stale_line")
        row["recommended_units_pre_news"] = ""
    slate_quality.apply_local_devig_unit_cap(row)
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
    row["data_quality_flags"] = ";".join(dict.fromkeys(dq_flags))

    signal = side_view.get("signal") or {}
    movement_corroboration = _to_float(signal.get("movement_corroboration"))
    row["hit_rate_component"] = _blank_neutral_component(signal.get("hit_component"))
    row["insight_component"] = _blank_neutral_component(signal.get("insight_component"))
    if movement_corroboration is None:
        row["movement_component"] = ""
    else:
        row["movement_component"] = _blank_neutral_component(50.0 + 25.0 * movement_corroboration)
    row["orf_component"] = _blank_neutral_component(signal.get("orf_component"))
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
        if movement_corroboration > 0 or line_with_side:
            signal_flags.append("movement_support")
        elif movement_corroboration < 0 and not line_with_side:
            signal_flags.append("movement_against")
    if line_with_side:
        signal_flags.append("line_moved_with_side")
    if injury_view.own_star_out:
        signal_flags.append("own_star_out")
    if injury_view.opponent_star_out:
        signal_flags.append("opponent_star_out")
        market_upper = str(row.get("market_type") or "").upper()
        if market_upper in slate_quality.GAMELINE_TYPES:
            row["research_leverage"] = "HIGH"
    if signal.get("insight_conflict"):
        signal_flags.append("insight_conflict")
    # Public-money flags live only on signal_flags — never card.flags /
    # data_quality_flags (those feed actionable=false via not dq_flags).
    from outlier_scrapers.cards import public_money_signal_flags

    for flag in public_money_signal_flags(_to_float(row.get("public_money_divergence_pct"))):
        signal_flags.append(flag)
    row["signal_flags"] = ";".join(dict.fromkeys(signal_flags))
    slate_quality.apply_predictor_gates(row)
    return disqualifying, dq_flags


def _blank_neutral_component(value: Any) -> Any:
    """Board B uses 50 as a missing-data filler. Do not ship it as a score."""
    number = _to_float(value)
    if number == 50.0:
        return ""
    if value in (None,):
        return ""
    return value


def _finalize_actionable_row(
    row: dict[str, Any],
    card: dict[str, Any],
    identity: dict[str, Any],
    disqualifying: bool,
    dq_flags: list[str],
) -> None:
    """Resolve actionable/board status and preserve upstream passthrough fields."""

    ref = identity["ref"]
    ev_summary = identity["ev_summary"]

    units = _to_float(row.get("recommended_units_pre_news"))
    edge_pct_val = _to_float(row.get("edge_pct"))
    is_actionable = (
        card.get("board") == "A"
        and units is not None
        and units > 0
        and edge_pct_val is not None
        and edge_pct_val > 0
        and not disqualifying
        and not dq_flags
        and str(row.get("actionable")) == "true"
    )
    row["actionable"] = "true" if is_actionable else "false"
    if not is_actionable:
        row["recommended_units_pre_news"] = ""

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
    probable_pitchers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    identity = _resolve_candidate_identity(card, ev_records, by_outcome, sport)
    if identity is None:
        return None

    row, team_enrichment_failed = _build_base_row(
        card, identity, sport, source_ts, event_starts, injuries
    )

    accepted, ev_probability_mismatch = _apply_price_and_sizing(
        row, identity, ev_records, odds_ts, norm_ts
    )
    if not accepted:
        return None

    _apply_projection_fields(row, card, identity, projections_by_outcome, probable_pitchers)

    disqualifying, dq_flags = _apply_quality_and_signal_flags(
        row,
        card,
        identity,
        sport,
        ev_records,
        ev_probability_mismatch,
        team_enrichment_failed,
        health_payload,
        stream,
        blend_artifact,
    )

    _finalize_actionable_row(row, card, identity, disqualifying, dq_flags)

    return row
