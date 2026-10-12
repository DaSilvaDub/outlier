"""NFL Alternate Floor Props Module.

Extracts, evaluates, ranks, and exports dynamic alternate floor props
(Pass Yards, Rush Yards, Receiving Yards) tailored to sportsbook-specific
ladders (e.g. Hard Rock Bet) with consensus fallback.

Eliminates rigid, static thresholds (e.g. fixed 50 rush / 200 pass) and evaluates
each player's true available floor line, hit rate convergence (L5/L10/Season),
cushion below consensus, game environment, and weather adjustments. Scores
are research ranks, not calibrated win probabilities.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from outlier_nfl.config import normalize_team
from outlier_nfl.floor_probability import (
    RANKING_SCORE_VERSION, describe_floor, is_half_point_line, odds_probability,
)
from outlier_nfl.roster import get_team_depth_chart

logger = logging.getLogger("outlier_nfl.alt_floors")

DEFAULT_TARGET_BOOK = "HARDROCK"
# Book names are compared compacted to A-Z0-9 ("Hard Rock" == "HARDROCK"), since
# live feeds may carry display names. "Hardrock R" is a separate book (see
# outlier_scrapers/alt_player_props.py): never the target, but a named retail book.
TARGET_BOOK_ALIASES = {"HARDROCK"}


def compact_book(name: Any) -> str:
    """``"Hard Rock"``/``"hardrock"``/``"HARD_ROCK"`` -> ``"HARDROCK"``."""
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def _target_keys(target_book: str) -> set[str]:
    key = compact_book(target_book)
    return set(TARGET_BOOK_ALIASES) if key in TARGET_BOOK_ALIASES else {key}

# Juice cap for standalone straight bets. Lines worse than -250 (e.g. -325, -600, -700)
# carry extreme negative asymmetry against in-game injuries and must be restricted to
# parlay legs / SGPs only.
MAX_STRAIGHT_ODDS: int = -250
PLAY_TYPE_STRAIGHT = "STRAIGHT"
PLAY_TYPE_PARLAY = "PARLAY_ONLY"

DEFAULT_MIN_LINES: dict[str, float] = {
    "PASS_YDS": 149.5,
    "RUSH_YDS": 34.5,
    "REC_YDS": 34.5,
}

MARKET_LABELS: dict[str, str] = {
    "PASS_YDS": "Passing Yards",
    "RUSH_YDS": "Rushing Yards",
    "REC_YDS": "Receiving Yards",
}


@dataclass
class AltFloorProp:
    player_name: str
    team: str
    opponent: str
    matchup: str
    market: str
    market_display: str
    position: str
    line: float
    consensus_line: float
    cushion: float
    cushion_pct: float
    target_book: str
    target_odds: int | float | None
    implied_probability: float | None  # Unconditional quote threshold; no push model -> None.
    l5_hit_rate: float | None
    l10_hit_rate: float | None
    season_hit_rate: float | None
    confidence_score: float
    category_rank: int
    master_rank: int
    rationale: str
    play_type: str = PLAY_TYPE_STRAIGHT
    event_id: str = ""
    event_starts_at: str = ""
    scope: str = "full_game"
    player_id: str = ""
    # Quote eligibility permits research ranking, never a betting recommendation.
    actionable: bool = False
    inventory_reason: str | None = None
    quote_eligible: bool = False
    bet_actionable: bool = False
    stake_fraction: float = 0.0
    ranking_score: float | None = None
    ranking_score_version: str = RANKING_SCORE_VERSION
    break_even_probability: float | None = None
    break_even_probability_status: str = "invalid_quote"
    market_probability: float | None = None
    market_probability_status: str = "no_exact_same_book_opposite_quote"
    model_probability: float | None = None
    calibrated_probability: float | None = None
    probability_status: str = "insufficient_calibration_history"
    fair_decimal_odds: float | None = None
    ev_per_unit: float | None = None

    def __post_init__(self) -> None:
        # Backward-compatible score name is internal only; never a win probability.
        if self.ranking_score is None:
            self.ranking_score = self.confidence_score
        self.actionable = False
        self.bet_actionable = False
        self.stake_fraction = 0.0
        self.model_probability = None
        self.calibrated_probability = None
        self.probability_status = "insufficient_calibration_history"
        self.fair_decimal_odds = None
        self.ev_per_unit = None
        quote_evidence = describe_floor(asdict(self), [])
        self.break_even_probability = quote_evidence["break_even_probability"]
        self.break_even_probability_status = quote_evidence["break_even_probability_status"]
        self.implied_probability = self.break_even_probability

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        # There is no production activation path until calibration is validated.
        record.update(
            actionable=False, bet_actionable=False, stake_fraction=0.0,
            model_probability=None, calibrated_probability=None,
            probability_status="insufficient_calibration_history",
            fair_decimal_odds=None, ev_per_unit=None,
        )
        if not is_half_point_line(self.line):
            record.update(
                implied_probability=None, break_even_probability=None,
                break_even_probability_status="push_probability_unmodeled",
                market_probability=None, market_probability_status="push_probability_unmodeled",
            )
        return record


def american_to_implied(odds: Any) -> float | None:
    """Convert a valid American price; a missing quote stays unavailable."""
    return odds_probability(odds)


def _hit_rate(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _rate_display(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 100:.2f}%"


def _extract_book_quote(
    books: list[dict[str, Any]], target_book: str = DEFAULT_TARGET_BOOK
) -> tuple[str, int | float | None]:
    """Find quote for target book, falling back to best retail book if absent."""
    if not isinstance(books, list):
        return ("UNKNOWN", None)

    # 1. Check target book aliases
    aliases = _target_keys(target_book)
    for b in books:
        if isinstance(b, dict) and compact_book(b.get("book")) in aliases:
            return (target_book, b.get("odds"))

    # 2. Fallback: select best regulated retail book (reported under its canonical name)
    for pref in RETAIL_PREFERRED:
        for b in books:
            if isinstance(b, dict) and compact_book(b.get("book")) == compact_book(pref):
                return (pref, b.get("odds"))

    # 3. Fallback: first available (reported, but not a sportsbook we can execute at)
    for b in books:
        if isinstance(b, dict) and b.get("odds") is not None:
            return (str(b.get("book") or "RETAIL"), b.get("odds"))

    return ("NONE", None)


RETAIL_PREFERRED = (
    "DRAFTKINGS", "FANDUEL", "BETMGM", "CAESARS", "FANATICS", "ESPNBET", "HARDROCK_R",
)
RETAIL_BOOKS = frozenset(compact_book(b) for b in RETAIL_PREFERRED)


def _finite_odds(odds: Any) -> float | None:
    try:
        val = float(odds)
    except (TypeError, ValueError):
        return None
    return val if odds_probability(odds) is not None else None


def _name_key(name: Any) -> str:
    return re.sub(r"[^a-z]", "", str(name or "").lower())


def _parse_utc(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else None
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def floor_inventory_reason(
    row: dict[str, Any],
    *,
    book: str,
    odds: Any,
    target_book: str,
    as_of_utc: Any = None,
    inactive_by_team: dict[str, list[str]] | None = None,
    require_injury_evidence: bool = False,
) -> str | None:
    """Why this floor is not executable, or None when it is (F23).

    Executable needs a finite quote from the target book or a named retail
    sportsbook, an event id plus a resolved team, a kickoff still ahead of
    ``as_of_utc``, and (when required) an admitted injury report that does not
    list the player.
    """
    if odds is None:
        return "no_quote"
    if _finite_odds(odds) is None:
        return "nonfinite_quote"
    if compact_book(book) not in _target_keys(target_book) | RETAIL_BOOKS:
        return "no_sportsbook_quote"
    team = normalize_team(row.get("team"))
    if not str(row.get("event_id") or "").strip() or not team:
        return "missing_identity"
    if as_of_utc is not None:
        as_of = _parse_utc(as_of_utc)
        kickoff = _parse_utc(row.get("event_starts_at"))
        if as_of is None or kickoff is None:
            return "unknown_kickoff"
        if kickoff <= as_of:
            return "stale_quote"
    if require_injury_evidence and inactive_by_team is None:
        return "injury_unverified"
    out = {_name_key(n) for n in (inactive_by_team or {}).get(team, [])}
    if _name_key(row.get("player_name")) in out:
        return "inactive_player"
    return None


def _player_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """Event + player identity: one name in two games is two players."""
    pid = str(row.get("player_id") or "").strip()
    return (
        str(row.get("event_id") or "").strip(),
        pid or str(row.get("player_name") or "").strip(),
        str(row.get("team") or "").strip().upper(),
    )


def _floor_depth_chart(
    team: str, tapes: dict[str, dict[str, Any]] | None, static_depth: bool
) -> dict[str, Any]:
    """WR order and TE for the depth adjustments (F18).

    From the run's admitted tape roles (``wr_deep`` then ``wr_slot``, ``te``);
    the fixed 2026 chart only with ``static_depth`` (fixture runs). No roles
    means no depth adjustment.
    """
    if static_depth:
        try:
            return get_team_depth_chart(team)
        except ValueError:
            return {}
    roles = (tapes or {}).get(team) or {}
    wrs = [str(roles[k]) for k in ("wr_deep", "wr_slot") if roles.get(k)]
    return {"wrs": wrs, "te": roles.get("te")}


def discover_alt_floor_candidates(
    props: list[dict[str, Any]] | list[Any],
    *,
    consensus_map: dict[tuple[str, str], float] | None = None,
    target_book: str = DEFAULT_TARGET_BOOK,
    min_lines: dict[str, float] | None = None,
    starting_qbs: set[str] | None = None,
    weather_by_event: dict[str, dict[str, Any]] | None = None,
    tapes: dict[str, dict[str, Any]] | None = None,
    static_depth: bool = False,
    as_of_utc: Any = None,
    inactive_by_team: dict[str, list[str]] | None = None,
    require_injury_evidence: bool = False,
) -> dict[str, list[AltFloorProp]]:
    """Discover and evaluate all candidate alternate floor props.

    Groups props by event + player + market (full game only) and selects each
    player's optimal floor line based on hit rate stability, safety cushion,
    book pricing, and situational context. A row that fails
    ``floor_inventory_reason`` stays as non-actionable inventory (F23).
    """
    thresholds = min_lines or DEFAULT_MIN_LINES
    input_rows = [p if isinstance(p, dict) else p.to_dict() for p in props]
    input_rows = [
        {**row, "scope": "full_game" if row.get("scope") in (None, "") else row["scope"]}
        for row in input_rows
    ]
    # A supplied (even empty) starter set restricts PASS_YDS: no evidenced
    # starter means no QB floor (F18). None keeps the unrestricted default.
    active_qbs = set(starting_qbs) if starting_qbs is not None else None

    # 1. Build consensus map if not passed
    # A supplied map is keyed (player_name, market); a built one by event+player.
    legacy_map: dict[tuple[str, str], float] = dict(consensus_map or {})
    cons_map: dict[tuple[Any, ...], float] = {}
    if not legacy_map:
        for p in props:
            row = p if isinstance(p, dict) else p.to_dict()
            if row.get("is_consensus_line") and row.get("scope") in (None, "", "full_game"):
                mkt = str(row.get("market") or "").strip()
                try:
                    cons_map[(*_player_key(row), mkt)] = float(row.get("line") or 0.0)
                except (ValueError, TypeError):
                    pass

    # 2. Collect candidate lines per (market, event, player)
    candidates_by_player: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for p in props:
        row = p if isinstance(p, dict) else p.to_dict()
        mkt = str(row.get("market") or "").strip()
        if mkt not in thresholds:
            continue
        if str(row.get("position") or "").strip().upper() != "OVER":
            continue
        if row.get("scope") not in (None, "", "full_game"):
            continue

        player = str(row.get("player_name") or "").strip()
        if not player:
            continue

        # In PASS_YDS, restrict to starting QBs if index is provided
        if mkt == "PASS_YDS" and active_qbs is not None and player not in active_qbs:
            continue

        try:
            line = float(row.get("line") or 0.0)
        except (ValueError, TypeError):
            continue

        if not math.isfinite(line) or line < thresholds[mkt]:
            continue

        pkey = _player_key(row)
        cons_line = (
            legacy_map.get((player, mkt)) if legacy_map else cons_map.get((*pkey, mkt))
        )
        if cons_line is None or not math.isfinite(cons_line) or cons_line <= 0 or line >= cons_line:
            continue

        books = row.get("books") or []
        b_name, b_odds = _extract_book_quote(books, target_book=target_book)

        finite = _finite_odds(b_odds)
        reason = floor_inventory_reason(
            row,
            book=b_name,
            odds=b_odds,
            target_book=target_book,
            as_of_utc=as_of_utc,
            inactive_by_team=inactive_by_team,
            require_injury_evidence=require_injury_evidence,
        )
        if finite is None:
            b_odds = None  # never carry NaN/inf into scores or JSON
        else:
            b_odds = int(finite) if finite.is_integer() else finite

        # Must have favored odds or heavy juice indicating a legitimate floor
        if b_odds is not None and b_odds > 0:
            continue

        cushion = cons_line - line
        cushion_pct = (cushion / cons_line * 100.0) if cons_line > 0 else 0.0
        cushion_ratio = cushion / cons_line if cons_line > 0 else 0.0

        l5 = _hit_rate(row.get("l5_hit_rate"))
        l10 = _hit_rate(row.get("l10_hit_rate"))
        season = _hit_rate(row.get("season_hit_rate"))
        # No quote is no price evidence: never an invented 0.50 (F23).
        implied_p = american_to_implied(b_odds) if b_odds is not None else None
        play_type = (
            PLAY_TYPE_PARLAY
            if (b_odds is not None and b_odds < MAX_STRAIGHT_ODDS)
            else PLAY_TYPE_STRAIGHT
        )

        # Context adjustments
        situational_adj = 0.0
        event_id = str(row.get("event_id") or "")

        # Weather adjustment
        if weather_by_event and event_id in weather_by_event:
            w = weather_by_event[event_id]
            p_adj = float(w.get("pass_adjustment") or 0.0)
            w_mph = float(w.get("wind_mph") or 0.0)
            if mkt == "PASS_YDS":
                if p_adj < 0:
                    situational_adj += p_adj
                elif w_mph >= 15.0:
                    situational_adj -= 0.05
            elif mkt == "RUSH_YDS" and (w_mph >= 15.0 or p_adj < 0):
                situational_adj += 0.03  # Ground game upgrade in foul weather

        # Depth chart & Game script situational adjustments
        team_str = str(row.get("team") or "").strip().upper()
        depth_chart = _floor_depth_chart(team_str, tapes, static_depth)
        player_clean = player.strip().lower()

        if mkt == "RUSH_YDS":
            # Deficit-risk / negative script discount on road or trailing rushing lines
            matchup_str = str(row.get("matchup") or "")
            tags = [str(t).upper() for t in (row.get("calibration_tags") or [])]
            is_road_team = (
                "@" in matchup_str
                and matchup_str.split("@", 1)[0].strip().upper() == team_str
            )
            if is_road_team or any("DEFICIT" in t or "TRAIL" in t or "COMEBACK" in t for t in tags):
                situational_adj -= 0.04

        elif mkt == "REC_YDS":
            wrs = [w.strip().lower() for w in depth_chart.get("wrs", [])]
            te_name = str(depth_chart.get("te") or "").strip().lower()
            # WR depth chart hierarchy penalty (WR1 gets 0, WR2 gets -0.04, WR3+ gets -0.08)
            if any(player_clean in w for w in wrs):
                wr_idx = next(i for i, w in enumerate(wrs) if player_clean in w)
                if wr_idx >= 1:
                    situational_adj -= min(0.08, 0.04 * wr_idx)
            # TE shallow ADOT risk (yardage suppression on low cushion)
            elif te_name and player_clean in te_name:
                if cushion_pct < 35.0:
                    situational_adj -= 0.04

        # Composite research ranking formula (unavailable rates contribute 0 only to rank):
        # 35% L10 stability + 30% L5 form + 15% Season + 10% Cushion ratio + 10% Implied + context
        base_score = (
            ((l10 or 0.0) * 0.35)
            + ((l5 or 0.0) * 0.30)
            + ((season or 0.0) * 0.15)
            + (cushion_ratio * 0.10)
            + ((implied_p or 0.0) * 0.10)
            + situational_adj
        )
        conf_score = max(0.0, min(1.0, round(base_score, 4)))

        # Target book match bonus in tie-breakers
        is_target_book = compact_book(b_name) in _target_keys(target_book)

        candidates_by_player[(mkt, *pkey)].append(
            {
                "prop_dict": row,
                "player": player,
                "team": str(row.get("team") or ""),
                "opponent": str(row.get("opponent") or ""),
                "matchup": str(row.get("matchup") or ""),
                "market": mkt,
                "market_display": MARKET_LABELS.get(mkt, mkt),
                "position": "OVER",
                "line": line,
                "consensus_line": cons_line,
                "cushion": round(cushion, 1),
                "cushion_pct": round(cushion_pct, 1),
                "target_book": b_name,
                "target_odds": b_odds,
                "implied_probability": None if implied_p is None else round(implied_p, 4),
                "play_type": play_type,
                "l5_hit_rate": l5,
                "l10_hit_rate": l10,
                "season_hit_rate": season,
                "confidence_score": conf_score,
                "is_target_book": is_target_book,
                "event_id": event_id,
                "event_starts_at": str(row.get("event_starts_at") or ""),
                "player_id": str(row.get("player_id") or ""),
                "inventory_reason": reason,
            }
        )

    # 3. For each player and market, select their optimal floor line
    optimal_by_category: dict[str, list[AltFloorProp]] = defaultdict(list)

    for (mkt, *_ident), lines in candidates_by_player.items():
        if not lines:
            continue
        for candidate in lines:
            evidence = describe_floor({**candidate, "scope": "full_game"}, input_rows)
            if evidence["market_probability_status"] == "selected_quote_mismatch_or_conflict":
                candidate["inventory_reason"] = "conflicting_selected_quotes"
        # Sort lines: executable first, then target book, highest confidence
        # score, then deepest cushion
        lines.sort(
            key=lambda x: (
                x["inventory_reason"] is None,
                x["is_target_book"],
                x["confidence_score"],
                x["cushion_pct"],
                -x["line"],
            ),
            reverse=True,
        )
        best = lines[0]

        prop_obj = AltFloorProp(
            player_name=best["player"],
            team=best["team"],
            opponent=best["opponent"],
            matchup=best["matchup"],
            market=best["market"],
            market_display=best["market_display"],
            position=best["position"],
            line=best["line"],
            consensus_line=best["consensus_line"],
            cushion=best["cushion"],
            cushion_pct=best["cushion_pct"],
            target_book=best["target_book"],
            target_odds=best["target_odds"],
            implied_probability=best["implied_probability"],
            l5_hit_rate=best["l5_hit_rate"],
            l10_hit_rate=best["l10_hit_rate"],
            season_hit_rate=best["season_hit_rate"],
            confidence_score=best["confidence_score"],
            category_rank=0,
            master_rank=0,
            rationale="",
            play_type=best.get("play_type", PLAY_TYPE_STRAIGHT),
            event_id=best["event_id"],
            event_starts_at=best["event_starts_at"],
            scope="full_game",
            player_id=best["player_id"],
            quote_eligible=best["inventory_reason"] is None,
            inventory_reason=best["inventory_reason"],
        )
        evidence = describe_floor(prop_obj.to_dict(), input_rows)
        for field, value in evidence.items():
            setattr(prop_obj, field, value)
        if prop_obj.market_probability_status == "selected_quote_mismatch_or_conflict":
            # A known conflicting selected price cannot support a quoted research row.
            prop_obj.quote_eligible = False
            prop_obj.inventory_reason = "conflicting_selected_quotes"
        optimal_by_category[mkt].append(prop_obj)

    return optimal_by_category


def rank_alt_floors(
    candidates_by_category: dict[str, list[AltFloorProp]],
) -> tuple[dict[str, list[AltFloorProp]], list[AltFloorProp], list[AltFloorProp]]:
    """Rank quote-eligible research into Top 3 per category and Master Top 9.

    Third element: unquoted/ineligible inventory, never ranked. Ranking never
    admits a bet or supplies calibrated probability.
    """
    top3_by_category: dict[str, list[AltFloorProp]] = {}
    master_pool: list[AltFloorProp] = []
    inventory: list[AltFloorProp] = []

    for mkt in ["PASS_YDS", "RUSH_YDS", "REC_YDS"]:
        everything = candidates_by_category.get(mkt, [])
        inventory.extend(p for p in everything if not p.quote_eligible)
        pool = [p for p in everything if p.quote_eligible]
        # Sort by confidence score desc, then cushion desc
        pool.sort(key=lambda x: (x.confidence_score, x.cushion_pct), reverse=True)
        top3 = pool[:3]

        for rank_idx, prop in enumerate(top3, 1):
            prop.category_rank = rank_idx
            # Generate analytical rationale
            odds_str = f"{prop.target_odds:+d}" if isinstance(prop.target_odds, int) else f"{prop.target_odds}"
            play_tag = f" [{prop.play_type}]" if prop.play_type == PLAY_TYPE_PARLAY else ""
            prop.rationale = (
                f"{prop.player_name} ({prop.team}): OVER {prop.line} {prop.market_display} ({prop.target_book} {odds_str}){play_tag} "
                f"provides a +{prop.cushion:.1f} yd ({prop.cushion_pct:.1f}%) cushion below consensus ({prop.consensus_line}). "
                f"Reported historical rates: L5 {_rate_display(prop.l5_hit_rate)}, "
                f"L10 {_rate_display(prop.l10_hit_rate)}. Research ranking only."
            )
            master_pool.append(prop)

        top3_by_category[mkt] = top3

    # Sort master pool by confidence score desc
    master_pool.sort(key=lambda x: (x.confidence_score, x.cushion_pct), reverse=True)
    for m_idx, prop in enumerate(master_pool, 1):
        prop.master_rank = m_idx

    inventory.sort(key=lambda x: (x.market, x.event_id, x.player_name))
    return top3_by_category, master_pool, inventory


def render_alt_floors_markdown(
    top3_by_category: dict[str, list[AltFloorProp]],
    master_pool: list[AltFloorProp],
    date_str: str,
    target_book: str = DEFAULT_TARGET_BOOK,
    inventory: list[AltFloorProp] | None = None,
) -> str:
    """Render executive-ready markdown report for Alt Floor Props."""
    lines: list[str] = [
        f"# NFL Sportsbook Alternate Floor Props — {date_str}",
        "",
        f"**Target Book**: `{target_book}` (Dynamic floor ladders with retail consensus fallback)  ",
        "**Methodology**: Replaces static, arbitrary thresholds (fixed 50 rush / 200 pass) with player-specific floor lines. "
        "Ranks reported hit rates (L5/L10/Season), cushion below consensus line, prices and environmental factors.  ",
        "**Status**: QUOTED RESEARCH — no approved bets; ranking scores are not calibrated win probabilities.  ",
        "**Juice Cap Policy**: Standalone straight wagers require odds >= -250. Odds worse than -250 (e.g. -325 to -700) are flagged `PARLAY ONLY` due to extreme downside injury asymmetry.",
        "",
        "---",
        "",
        "## Quoted Research Ranking (Top 1–9 Overall)",
        "",
        "| Rank | Player | Team | Market | Alt Line | Consensus | Cushion | Odds | Play Type | L5 Hit | L10 Hit | Ranking Score |",
        "|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for p in master_pool:
        odds_str = f"{p.target_odds:+d}" if isinstance(p.target_odds, int) else f"{p.target_odds}"
        lines.append(
            f"| **#{p.master_rank}** | **{p.player_name}** | {p.team} | {p.market_display} | "
            f"**OVER {p.line}** | {p.consensus_line} | +{p.cushion} ({p.cushion_pct}%) | "
            f"`{p.target_book} {odds_str}` | `{p.play_type}` | {_rate_display(p.l5_hit_rate)} | {_rate_display(p.l10_hit_rate)} | **{p.confidence_score:.3f}** |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## Category Breakdowns",
        "",
    ])

    for mkt, title in [
        ("PASS_YDS", "Passing Yards Alternate Floors (Top 3)"),
        ("RUSH_YDS", "Rushing Yards Alternate Floors (Top 3)"),
        ("REC_YDS", "Receiving Yards Alternate Floors (Top 3)"),
    ]:
        props = top3_by_category.get(mkt, [])
        lines.append(f"### {title}")
        lines.append("")
        for p in props:
            odds_str = f"{p.target_odds:+d}" if isinstance(p.target_odds, int) else f"{p.target_odds}"
            lines.append(
                f"- **#{p.category_rank} {p.player_name} ({p.team} vs {p.opponent}) — OVER {p.line} {p.market_display}**"
            )
            lines.append(
                f"  - **Sportsbook Quote**: `{p.target_book} {odds_str}` | **Price Restriction**: `{p.play_type}`"
            )
            lines.append(
                f"  - **Safety Cushion**: +{p.cushion:.1f} yards below consensus line of {p.consensus_line} ({p.cushion_pct:.1f}% discount)"
            )
            lines.append(
                f"  - **Reported Hit Rates**: L5: {_rate_display(p.l5_hit_rate)} | "
                f"L10: {_rate_display(p.l10_hit_rate)} | Season: {_rate_display(p.season_hit_rate)}"
            )
            lines.append(f"  - **Ranking Score**: `{p.confidence_score:.3f}` (Master Rank: #{p.master_rank}; not a win probability)")
            lines.append(
                f"  - **Break-even Probability**: {_rate_display(p.break_even_probability)} "
                f"(`{p.break_even_probability_status}`) | **Market Benchmark**: {_rate_display(p.market_probability)} "
                f"(`{p.market_probability_status}`)"
            )
            lines.append(f"  - **Calibrated Probability / EV / Stake**: unavailable / unavailable / 0 (`{p.probability_status}`)")
            lines.append("")

    lines.extend([
        "---",
        "",
        "## Non-actionable inventory",
        "",
        "Floors listed for reference only: no executable quote, identity or injury "
        "evidence. Never ranked or recommended.",
        "",
    ])
    if inventory:
        for p in inventory:
            lines.append(
                f"- {p.player_name} ({p.team or '?'}) — OVER {p.line} {p.market_display} "
                f"[{p.event_id or 'no event'}]: `{p.inventory_reason}`"
            )
    else:
        lines.append("- None")
    lines.append("")

    lines.extend([
        "---", "", "## Price and probability limits", "",
        "A ranking score is a formula used to order research candidates, not a win probability. "
        "A same-book proportional-devig benchmark describes the market; it is not a calibrated forecast.",
        "Calibrated probability, fair odds, expected value and stakes remain unavailable until "
        "verified outcomes and independent validation support publication. No floor is an approved bet.",
        "Integer lines with possible pushes withhold unconditional break-even probabilities.",
        "`PARLAY_ONLY` is a price restriction; it does not establish value or justify combining legs.", "",
    ])

    return "\n".join(lines)


def export_alt_floors(
    top3_by_category: dict[str, list[AltFloorProp]],
    master_pool: list[AltFloorProp],
    exports_dir: Path,
    reports_dir: Path,
    date_str: str,
    target_book: str = DEFAULT_TARGET_BOOK,
    write_latest: bool = True,
    inventory: list[AltFloorProp] | None = None,
    *,
    calibration_candidates: list[AltFloorProp] | None = None,
    export_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Export Alt Floors to JSON, CSV, and Markdown files.

    CSV and ranked records carry quote-eligible research only; inventory is listed
    separately in JSON and markdown.
    """
    exports_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    records = [p.to_dict() for p in master_pool]
    calibration_records = [p.to_dict() for p in (
        calibration_candidates if calibration_candidates is not None
        else [*master_pool, *(inventory or [])]
    )]
    context = export_context or {}
    provenance = {key: context.get(key) for key in ("run_id", "captured_at", "run_context")}
    for record in calibration_records:
        record.update(provenance)
    payload = {
        "date": date_str,
        "target_book": target_book,
        "count": len(records),
        "categories": {
            mkt: [p.to_dict() for p in props]
            for mkt, props in top3_by_category.items()
        },
        "records": records,
        "status": "QUOTED_RESEARCH",
        "actionable_count": 0,
        "ranking_score_version": RANKING_SCORE_VERSION,
        "calibration_records": calibration_records,
        "calibration_record_count": len(calibration_records),
        **provenance,
        "inventory_count": len(inventory or []),
        "inventory": [p.to_dict() for p in (inventory or [])],
    }

    # 1. JSON Exports
    json_dated = exports_dir / f"nfl_alt_floors_{date_str}.json"
    with open(json_dated, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, allow_nan=False)

    if write_latest:
        json_latest = exports_dir / "nfl_alt_floors_latest.json"
        with open(json_latest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, allow_nan=False)

    # 2. CSV Exports
    fieldnames = [
        "master_rank",
        "category_rank",
        "player_name",
        "team",
        "opponent",
        "matchup",
        "market",
        "market_display",
        "position",
        "line",
        "consensus_line",
        "cushion",
        "cushion_pct",
        "target_book",
        "target_odds",
        "implied_probability",
        "play_type",
        "l5_hit_rate",
        "l10_hit_rate",
        "season_hit_rate",
        "confidence_score",
        "ranking_score",
        "ranking_score_version",
        "quote_eligible",
        "bet_actionable",
        "stake_fraction",
        "break_even_probability",
        "break_even_probability_status",
        "market_probability",
        "market_probability_status",
        "calibrated_probability",
        "probability_status",
        "fair_decimal_odds",
        "ev_per_unit",
        "rationale",
        "event_id",
        "player_id",
        "actionable",
        "inventory_reason",
    ]
    csv_dated = exports_dir / f"nfl_alt_floors_{date_str}.csv"
    with open(csv_dated, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)

    # The undated CSV is the "current slate" artifact, same role as
    # nfl_alt_floors_latest.json -- a windowed run (write_latest=False) carries
    # only part of the slate and must not overwrite it (F11, from #209).
    csv_main = exports_dir / "nfl_alt_floors.csv"
    if write_latest:
        with open(csv_main, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                writer.writerow(r)

    # 3. Markdown Report
    md_content = render_alt_floors_markdown(
        top3_by_category, master_pool, date_str=date_str, target_book=target_book,
        inventory=inventory,
    )
    md_dated = reports_dir / f"{date_str}_Alt_Floors.md"
    md_dated.write_text(md_content, encoding="utf-8")

    if write_latest:
        md_latest = reports_dir / "Alt_Floors_latest.md"
        md_latest.write_text(md_content, encoding="utf-8")

    logger.info("Exported %d alt floor props to %s and %s", len(records), json_dated, md_dated)
    return {
        "json": str(json_dated),
        "csv": str(csv_main if write_latest else csv_dated),
        "markdown": str(md_dated),
    }


def generate_alt_floors_pipeline(
    props: list[dict[str, Any]] | list[Any],
    *,
    exports_dir: Path,
    reports_dir: Path,
    date_str: str,
    target_book: str = DEFAULT_TARGET_BOOK,
    consensus_map: dict[tuple[str, str], float] | None = None,
    starting_qbs: set[str] | None = None,
    weather_records: list[dict[str, Any]] | None = None,
    tapes: dict[str, dict[str, Any]] | None = None,
    write_latest: bool = True,
    static_depth: bool = False,
    as_of_utc: Any = None,
    inactive_by_team: dict[str, list[str]] | None = None,
    require_injury_evidence: bool = False,
    export_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Complete pipeline orchestration for Alt Floor Props extraction and reporting."""
    weather_by_event: dict[str, dict[str, Any]] = {}
    if weather_records:
        for w in weather_records:
            eid = str(w.get("event_id") or "")
            if eid:
                weather_by_event[eid] = w

    candidates = discover_alt_floor_candidates(
        props,
        consensus_map=consensus_map,
        target_book=target_book,
        starting_qbs=starting_qbs,
        weather_by_event=weather_by_event,
        tapes=tapes,
        static_depth=static_depth,
        as_of_utc=as_of_utc,
        inactive_by_team=inactive_by_team,
        require_injury_evidence=require_injury_evidence,
    )

    top3, master_pool, inventory = rank_alt_floors(candidates)
    if require_injury_evidence and inactive_by_team is None and inventory and not master_pool:
        logger.warning(
            "Alt floors: all %d floors are non-actionable inventory because the injury "
            "report was not admitted (refused or missing tape); rerun with --refresh-tape.",
            len(inventory),
        )

    outputs = export_alt_floors(
        top3,
        master_pool,
        exports_dir=exports_dir,
        reports_dir=reports_dir,
        date_str=date_str,
        target_book=target_book,
        write_latest=write_latest,
        inventory=inventory,
        calibration_candidates=[p for pool in candidates.values() for p in pool],
        export_context=export_context,
    )

    return {
        "count": len(master_pool),
        "actionable_count": 0,
        "bet_actionable_count": 0,
        "calibration_count": sum(len(pool) for pool in candidates.values()),
        "status": "QUOTED_RESEARCH",
        "inventory_count": len(inventory),
        "top3_by_category": {mkt: [p.to_dict() for p in props] for mkt, props in top3.items()},
        "master_pool": [p.to_dict() for p in master_pool],
        "outputs": outputs,
    }
