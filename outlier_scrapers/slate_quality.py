"""Post-8/16 slate quality gates.

These helpers keep pack ranking, injury usage, and CLV rules out of the
2000-line pack writer. They are pure functions over already-built rows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_OUT_STATUS = re.compile(
    r"\b(?:out(?:\s+for\s+season)?|ofs|inactive)\b",
    re.IGNORECASE,
)
_INJURY_CHUNK = re.compile(r"\s*(?:(?P<team>[A-Z]{2,3}):\s*)?(?P<body>[^|]+)")
_STATUS_PAREN = re.compile(r"\((?P<status>[^)]*)\)")
_PLAYER_PROP_SIDES = re.compile(r"\b(OVER|UNDER)\b", re.IGNORECASE)
_SIGNED_LINE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_SELECTION_NAME_RE = re.compile(r"^(.*?)\s+(?:OVER|UNDER)\b", re.IGNORECASE)
_TRAILING_SO_MARKET_RE = re.compile(r"\s+(?:SO|STRIKEOUTS?|K)$", re.IGNORECASE)
_RETURNING_IL_STATUS_RE = re.compile(
    r"\b(?:60|15|10|7)[- ]day\s+il\b|\binjured\s+list\b",
    re.IGNORECASE,
)
_RETURNING_IL_NOTE_RE = re.compile(
    r"\b(?:cleared(?:\s+to)?|first\s+(?:start|time|game|outing|appearance)|rehab(?:\s+(?:assignment|start|outing))?|activat(?:ed|ion)|pitch\s+(?:count|limit|restriction)|simulated\s+game)\b",
    re.IGNORECASE,
)

LOCAL_DEVIG_UNIT_CAP = 1.0
MARKET_DEVIG_UNIT_CAP = 1.0
INDEPENDENT_SO_UNIT_CAP = 2.0
MARKET_DEVIG_SOURCES = frozenset({"local_devig", "outlier_devig"})
INDEPENDENT_SO_SOURCE = "independent_gamelog_so"
GAMELOG_FEATURE_HASH = "so-starter-gamelog-v2"
PREDICTIVE_SIGNAL_FLAGS = frozenset({"insight_support", "movement_support", "orf_support"})
PHANTOM_EDGE_THRESHOLD = 0.10
# Live Kelly from gamelog independents stays off until so_eval prefers independent.
# Force: OUTLIER_PROMOTE_INDEPENDENT_SO=1
# Auto (ledger gate): OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO=1 + config/so_promotion.json
ENABLE_INDEPENDENT_SO_SIZING = os.environ.get(
    "OUTLIER_PROMOTE_INDEPENDENT_SO", ""
).strip().lower() in {"1", "true", "yes", "on"}
GAMELINE_TYPES = {"GAMELINE", "SPREAD", "MONEYLINE", "RUN_LINE", "RUNLINE"}
PITCHER_IDENTITY_MISMATCH = "pitcher_identity_mismatch"
PITCHER_IDENTITY_UNCONFIRMED = "pitcher_identity_unconfirmed"
PITCHER_RETURNING_FROM_IL = "pitcher_returning_from_il"
_MLB_SO_MARKETS = {"SO", "STRIKEOUTS", "PITCHER_STRIKEOUTS", "K"}
PLAYER_PROP_HINTS = {
    "REB",
    "AST",
    "PTS",
    "3PTS",
    "3PT",
    "RA",
    "PR",
    "PA",
    "PRA",
    "SO",
    "H",
    "TB",
    "OUTS",
    "2B",
    "ER",
    "BB",
    "HRR",
    "PLAYER_PROP",
    "K",
    "STRIKEOUTS",
    "PITCHER_STRIKEOUTS",
}


@dataclass(frozen=True)
class InjuryView:
    own_star_out: bool
    opponent_star_out: bool
    own_outs: tuple[str, ...]
    opponent_outs: tuple[str, ...]
    unscoped_outs: tuple[str, ...]


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _is_out_status(status: str) -> bool:
    text = status.strip()
    if not text:
        return False
    if re.search(r"game[- ]?time|probable|questionable|day[- ]?to[- ]?day|dtd", text, re.I):
        return False
    return bool(_OUT_STATUS.search(text))


def classify_injuries(injury_flags: str, team: str | None) -> InjuryView:
    """Split pack injury_flags into own-team vs opponent star-outs.

    Prefixed chunks (`CHI: Diggins (Out; ...)`) are authoritative. Unprefixed
    chunks stay in ``unscoped_outs`` so they cannot invent a side.
    """
    own: list[str] = []
    opp: list[str] = []
    unscoped: list[str] = []
    team_token = str(team or "").strip().upper()
    for match in _INJURY_CHUNK.finditer(str(injury_flags or "")):
        body = (match.group("body") or "").strip()
        if not body:
            continue
        status_match = _STATUS_PAREN.search(body)
        status = status_match.group("status") if status_match else body
        if not _is_out_status(status):
            continue
        prefix = (match.group("team") or "").strip().upper()
        if prefix and team_token and prefix == team_token:
            own.append(body)
        elif prefix and team_token and prefix != team_token:
            opp.append(body)
        else:
            unscoped.append(body)
    return InjuryView(
        own_star_out=bool(own),
        opponent_star_out=bool(opp),
        own_outs=tuple(own),
        opponent_outs=tuple(opp),
        unscoped_outs=tuple(unscoped),
    )


def _selection_side(row: dict[str, Any]) -> str | None:
    selection = str(row.get("selection") or "")
    match = _PLAYER_PROP_SIDES.search(selection)
    if match:
        return match.group(1).upper()
    headline = str(row.get("headline_side") or "").upper()
    return headline or None


def is_player_prop(row: dict[str, Any]) -> bool:
    market = str(row.get("market_type") or "").upper()
    if market in PLAYER_PROP_HINTS or market == "PLAYER_PROP":
        return True
    if market in GAMELINE_TYPES or market == "TEAM_PROP":
        return False
    if row.get("player_id"):
        return True
    return " - " in str(row.get("selection") or "")


def _normalize_person_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _so_player_name(row: Mapping[str, Any], player_name: str | None = None) -> str:
    if player_name:
        return _normalize_person_name(player_name)
    player = str(row.get("player") or "").strip()
    if player:
        return _normalize_person_name(player)
    selection = str(row.get("selection") or "")
    if " - " in selection:
        return _normalize_person_name(selection.split(" - ", 1)[0])
    match = _SELECTION_NAME_RE.match(selection)
    if not match:
        return ""
    return _normalize_person_name(_TRAILING_SO_MARKET_RE.sub("", match.group(1).strip()))


def _is_mlb_so_row(row: Mapping[str, Any]) -> bool:
    sport = str(row.get("sport") or row.get("league") or "").upper()
    market = str(row.get("market_type") or row.get("market") or "").upper()
    if sport and sport != "MLB":
        return False
    if market in _MLB_SO_MARKETS:
        return True
    selection = str(row.get("selection") or "").upper()
    return "STRIKEOUT" in selection or "STRIKEOUT" in market


def pitcher_identity_flags(
    row: Mapping[str, Any],
    probable_pitchers: Mapping[str, Any] | None,
    *,
    player_name: str | None = None,
) -> list[str]:
    """Fail-closed MLB SO identity vs the probable-pitcher lookup.

    A confirmed starter on team T must not ship as a clean card for team U
    (2026-09-06 MacKenzie Gore on WSH while starting for TEX). Relievers on a
    team with a confirmed starter are the same class of mismatch. A missing
    or empty lookup is unconfirmed — it does not skip the gate.
    """
    if not _is_mlb_so_row(row):
        return []
    if not probable_pitchers:
        return [PITCHER_IDENTITY_UNCONFIRMED]
    player = _so_player_name(row, player_name)
    team = str(row.get("team") or "").strip().upper()
    if not player or not team:
        return [PITCHER_IDENTITY_UNCONFIRMED]

    listed_teams = [
        str(code).strip().upper()
        for code, info in probable_pitchers.items()
        if isinstance(info, Mapping)
        and bool(info.get("confirmed"))
        and _normalize_person_name(info.get("pitcher")) == player
    ]
    if listed_teams:
        if team in listed_teams:
            return []
        return [PITCHER_IDENTITY_MISMATCH]

    listed = probable_pitchers.get(team)
    if isinstance(listed, Mapping) and listed.get("confirmed"):
        starter = _normalize_person_name(listed.get("pitcher"))
        if starter and starter != player:
            return [PITCHER_IDENTITY_MISMATCH]
    return [PITCHER_IDENTITY_UNCONFIRMED]


def pitcher_returning_from_il(
    row: Mapping[str, Any],
    injury_flags: str | None = None,
    *,
    player_name: str | None = None,
) -> bool:
    """True when an MLB starting pitcher is returning from an IL stint or rehab limit."""
    if not _is_mlb_so_row(row):
        return False
    player = _so_player_name(row, player_name)
    if not player:
        return False
    text = str(injury_flags if injury_flags is not None else row.get("injury_flags") or "").strip()
    if not text:
        return False

    for match in _INJURY_CHUNK.finditer(text):
        body = (match.group("body") or "").strip()
        if not body:
            continue
        name_part = body.split("(", 1)[0].split(":", 1)[0].strip()
        if not name_part:
            continue
        norm_name = _normalize_person_name(name_part)
        if norm_name != player and player not in norm_name and norm_name not in player:
            continue

        status_match = _STATUS_PAREN.search(body)
        status = status_match.group("status") if status_match else body
        if _RETURNING_IL_STATUS_RE.search(status) and _RETURNING_IL_NOTE_RE.search(body):
            return True
    return False


def _identity_audit_entry(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "player": str(row.get("player") or row.get("selection") or ""),
        "team": str(row.get("team") or ""),
        "matchup": str(row.get("matchup") or ""),
        "market_id": str(row.get("market_id") or ""),
        "board": str(row.get("board") or ""),
        "data_quality_flags": str(row.get("data_quality_flags") or ""),
    }


def summarize_pitcher_identity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    so_rows = 0
    mismatch: list[dict[str, str]] = []
    unconfirmed: list[dict[str, str]] = []
    for row in rows:
        if not _is_mlb_so_row(row):
            continue
        so_rows += 1
        flags = {
            flag.strip()
            for flag in str(row.get("data_quality_flags") or "").split(";")
            if flag.strip()
        }
        if PITCHER_IDENTITY_MISMATCH in flags:
            mismatch.append(_identity_audit_entry(row))
        if PITCHER_IDENTITY_UNCONFIRMED in flags:
            unconfirmed.append(_identity_audit_entry(row))
    fail_closed_count = len(mismatch) + len(unconfirmed)
    return {
        "schema_version": "1.0",
        "so_rows": so_rows,
        "mismatch_count": len(mismatch),
        "unconfirmed_count": len(unconfirmed),
        "fail_closed_count": fail_closed_count,
        "status": "fail_closed" if fail_closed_count else "ok",
        "mismatch": mismatch,
        "unconfirmed": unconfirmed,
    }


def usage_up_under(row: dict[str, Any], injuries: InjuryView) -> bool:
    """True when an UNDER player prop sits on a usage-up (own star out) card."""
    if not is_player_prop(row):
        return False
    if _selection_side(row) != "UNDER":
        return False
    return injuries.own_star_out


def _is_signed_margin_market(row: dict[str, Any]) -> bool:
    market = str(row.get("market_type") or "").upper()
    proposition = str(row.get("proposition") or "").upper()
    selection = str(row.get("selection") or "").upper()
    if market in {"SPREAD", "RUN_LINE", "RUNLINE"} or proposition in {
        "SPREAD",
        "RUN_LINE",
        "RUNLINE",
    }:
        return True
    if market == "GAMELINE" and (
        "SPREAD" in selection or "RUN LINE" in selection or proposition == "SPREAD"
    ):
        return True
    return False


def signed_line_moved_with_side(row: dict[str, Any]) -> bool:
    """CHI -1.5 → -2.5 is +CLV for the favorite, not reverse line movement.

    Only signed margin markets (spread / run line). OVER/UNDER props and totals
    keep the existing reverse-line-movement stale gate.
    """
    if not _is_signed_margin_market(row):
        return False
    opened = _to_float(row.get("line_open"))
    now = _to_float(row.get("line_now"))
    selected = _to_float(row.get("line"))
    if opened is None or now is None or selected is None:
        return False
    if now == opened:
        return False
    if selected < 0:
        return now < opened
    if selected > 0:
        return now > opened
    return False


def apply_local_devig_unit_cap(row: dict[str, Any]) -> None:
    """Kelly oversizes market-devig Board A plays (PLAY ROI < flat). Cap at 1u."""
    apply_market_devig_unit_cap(row)


def _append_sizing_flag(row: dict[str, Any], flag: str) -> None:
    existing = str(row.get("sizing_flags") or "")
    parts = [part for part in existing.split(";") if part]
    if flag not in parts:
        parts.append(flag)
    row["sizing_flags"] = ";".join(parts)


def apply_market_devig_unit_cap(row: dict[str, Any]) -> None:
    """Cap Outlier/local AVERAGE-devig Kelly at 1u.

    Naked market EV is not an independent forecast. Historical PLAY ROI is
    worse than flat 1u on the same cards.
    """
    source = str(row.get("model_prob_source") or "").lower()
    if source not in MARKET_DEVIG_SOURCES:
        return
    units = _to_float(row.get("recommended_units_pre_news"))
    if units is None or units <= MARKET_DEVIG_UNIT_CAP:
        return
    row["recommended_units_pre_news"] = MARKET_DEVIG_UNIT_CAP
    _append_sizing_flag(row, "market_devig_unit_cap")
    if source == "local_devig":
        _append_sizing_flag(row, "local_devig_unit_cap")


def _signal_flag_set(row: dict[str, Any]) -> set[str]:
    return {part.strip() for part in str(row.get("signal_flags") or "").split(";") if part.strip()}


def has_predictive_signal(row: dict[str, Any]) -> bool:
    """True when insight, movement, or ORF corroborates the side.

    Recency hit rate and IL laundry-list star-out flags do not count. Any
    ``movement_against`` vetoes the rest.
    """
    flags = _signal_flag_set(row)
    if "movement_against" in flags:
        return False
    return bool(flags & PREDICTIVE_SIGNAL_FLAGS)


def promote_independent_so_sizing(row: dict[str, Any]) -> bool:
    """Size from gamelog-eligible independent SO probs instead of market de-vig.

    Disabled by default. Enable via ``OUTLIER_PROMOTE_INDEPENDENT_SO=1`` (force)
    or auto mode that clears ``so_promotion`` ledger criteria. Kelly uses a
    market-tempered blend of the independent win prob (not raw overconfident
    tails). Requires a current-generation gamelog feature hash (v2, or a
    promoted calibrated refit) and (for player props) a predictive
    signal. Returns True when sizing was rewritten.
    """
    from outlier_scrapers.projections import is_current_gamelog_so_hash
    from outlier_scrapers.sizing import compute_sizing
    from outlier_scrapers.so_promotion import (
        independent_so_sizing_enabled,
        load_so_promotion_config,
    )
    from outlier_scrapers.so_sizing_blend import temper_independent_prob

    if not (ENABLE_INDEPENDENT_SO_SIZING or independent_so_sizing_enabled()):
        return False
    independent = _to_float(row.get("independent_model_prob"))
    digest = str(row.get("projection_feature_hash") or "")
    decimal_price = _to_float(row.get("decimal_price"))
    market = _to_float(row.get("market_consensus_prob"))
    if (
        independent is None
        or market is None
        or not is_current_gamelog_so_hash(digest)
        or decimal_price is None
        or decimal_price <= 1.0
    ):
        return False
    if is_player_prop(row) and not has_predictive_signal(row):
        return False

    cfg = load_so_promotion_config()
    unit_cap = float(cfg.get("max_units") or INDEPENDENT_SO_UNIT_CAP)
    temper_weight = float(cfg.get("temper_independent_weight") or 0.55)
    sizing_prob = temper_independent_prob(independent, market, independent_weight=temper_weight)

    push_prob = _to_float(row.get("independent_push_prob"))
    if push_prob is None:
        push_prob = _to_float(row.get("push_prob")) or 0.0
    sizing = compute_sizing(
        decimal_price=decimal_price,
        model_prob=sizing_prob,
        push_prob=push_prob,
        max_units=unit_cap,
    )
    if sizing.recommended_units_pre_news is None:
        return False

    row["model_prob"] = sizing_prob
    row["model_prob_source"] = INDEPENDENT_SO_SOURCE
    row["implied_prob"] = sizing.implied_prob
    row["edge_pct"] = sizing.edge_pct
    row["kelly_025_units"] = sizing.kelly_025_units
    row["max_units"] = sizing.max_units
    row["recommended_units_pre_news"] = sizing.recommended_units_pre_news
    _append_sizing_flag(row, "independent_gamelog_so_sizing")
    _append_sizing_flag(row, "independent_so_market_tempered")
    if (
        sizing.recommended_units_pre_news is not None
        and sizing.recommended_units_pre_news > unit_cap
    ):
        row["recommended_units_pre_news"] = unit_cap
        _append_sizing_flag(row, "independent_so_unit_cap")
    return True


def apply_predictor_gates(row: dict[str, Any]) -> None:
    """Turn Board A from a market-EV sizer into a signal-gated, capped play.

    Optionally prefers gamelog-backed independent SO sizing when explicitly
    enabled. Otherwise refuses to treat de-vig leftover as a 3-unit forecast.
    """
    promoted = promote_independent_so_sizing(row)
    if not promoted:
        apply_market_devig_unit_cap(row)
    if is_player_prop(row) and not has_predictive_signal(row):
        row["recommended_units_pre_news"] = ""
        _append_sizing_flag(row, "missing_predictive_signal")
    independent = _to_float(row.get("independent_model_prob"))
    edge = _to_float(row.get("edge_pct"))
    if not promoted and independent is None and edge is not None and edge >= PHANTOM_EDGE_THRESHOLD:
        _append_sizing_flag(row, "edge_suspect_no_independent_model")
        units = _to_float(row.get("recommended_units_pre_news"))
        if units is not None and units > MARKET_DEVIG_UNIT_CAP:
            row["recommended_units_pre_news"] = MARKET_DEVIG_UNIT_CAP
            _append_sizing_flag(row, "market_devig_unit_cap")

    units = _to_float(row.get("recommended_units_pre_news"))
    edge = _to_float(row.get("edge_pct"))
    missing_signal = "missing_predictive_signal" in str(row.get("sizing_flags") or "")
    is_actionable = (
        units is not None and units > 0 and edge is not None and edge > 0 and not missing_signal
    )
    row["actionable"] = "true" if is_actionable else "false"
    if not is_actionable:
        row["recommended_units_pre_news"] = ""


def playable_prop_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    """Greatest edge first; rank_value is the tie-break, not the primary key."""
    edge = _to_float(row.get("edge_pct"))
    rank = _to_float(row.get("_rank_value")) or 0.0
    return (
        -(edge if edge is not None else float("-inf")),
        -rank,
        str(row.get("market_id") or ""),
    )


def dossier_injury_section(rows: list[dict[str, Any]]) -> list[str]:
    """Own vs opponent outs plus usage implication for the first row's team."""
    if not rows:
        return []
    row = rows[0]
    view = classify_injuries(str(row.get("injury_flags") or ""), row.get("team"))
    lines = ["", "### Injury / usage"]
    if view.own_outs:
        lines.append("- Own-team outs: " + " ; ".join(view.own_outs))
    if view.opponent_outs:
        lines.append("- Opponent outs: " + " ; ".join(view.opponent_outs))
    if view.unscoped_outs:
        lines.append("- Unscoped outs (team unknown): " + " ; ".join(view.unscoped_outs))
    if not (view.own_outs or view.opponent_outs or view.unscoped_outs):
        lines.append("- No confirmed Out / OFS tags in pack injury_flags.")
    if view.own_star_out:
        lines.append(
            "- Usage: own-team star Out supports teammate OVER props and "
            "weakens that team's UNDER props (do not Board-A an UNDER)."
        )
    if view.opponent_star_out:
        lines.append(
            "- Sides: opponent star Out is the primary cover signal; "
            "own-star Out is secondary and must not veto a still-plus EV side."
        )
    return lines
