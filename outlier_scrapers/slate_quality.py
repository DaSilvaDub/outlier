"""Post-8/16 slate quality gates.

These helpers keep pack ranking, injury usage, and CLV rules out of the
2000-line pack writer. They are pure functions over already-built rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OUT_STATUS = re.compile(
    r"\b(?:out(?:\s+for\s+season)?|ofs|inactive|\d+-day\s+il|il)\b",
    re.IGNORECASE,
)
_INJURY_CHUNK = re.compile(
    r"\s*(?:(?P<team>[A-Z]{2,3}):\s*)?(?P<body>[^|]+)"
)
_STATUS_PAREN = re.compile(r"\((?P<status>[^)]*)\)")
_PLAYER_PROP_SIDES = re.compile(r"\b(OVER|UNDER)\b", re.IGNORECASE)
_SIGNED_LINE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

LOCAL_DEVIG_UNIT_CAP = 1.0
GAMELINE_TYPES = {"GAMELINE", "SPREAD", "MONEYLINE", "RUN_LINE", "RUNLINE"}
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
    return " - " in str(row.get("selection") or "")


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
    """Kelly oversizes local-devig Board A plays (PLAY ROI < flat). Cap at 1u."""
    source = str(row.get("model_prob_source") or "").lower()
    if source != "local_devig":
        return
    units = _to_float(row.get("recommended_units_pre_news"))
    if units is None or units <= LOCAL_DEVIG_UNIT_CAP:
        return
    row["recommended_units_pre_news"] = LOCAL_DEVIG_UNIT_CAP
    existing = str(row.get("sizing_flags") or "")
    if "local_devig_unit_cap" not in existing:
        row["sizing_flags"] = f"{existing};local_devig_unit_cap".strip(";")


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
