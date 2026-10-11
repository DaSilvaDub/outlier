"""NFL Alternate Floor Props Module.

Extracts, evaluates, ranks, and exports dynamic alternate floor props
(Pass Yards, Rush Yards, Receiving Yards) tailored to sportsbook-specific
ladders (e.g. Hard Rock Bet) with consensus fallback.

Eliminates rigid, static thresholds (e.g. fixed 50 rush / 200 pass) and evaluates
each player's true available floor line, hit rate convergence (L5/L10/Season),
safety cushion below consensus, game environment, and weather calibration.
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
from outlier_nfl.roster import get_team_depth_chart

logger = logging.getLogger("outlier_nfl.alt_floors")

DEFAULT_TARGET_BOOK = "HARDROCK"
TARGET_BOOK_ALIASES = {"HARDROCK", "HARDROCK_R"}

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
    implied_probability: float | None  # None when there is no usable quote (F23)
    l5_hit_rate: float
    l10_hit_rate: float
    season_hit_rate: float
    confidence_score: float
    category_rank: int
    master_rank: int
    rationale: str
    play_type: str = PLAY_TYPE_STRAIGHT
    event_id: str = ""
    event_starts_at: str = ""
    scope: str = "full_game"
    player_id: str = ""
    # F23: only actionable rows are ranked or recommended. Inventory rows keep
    # the reason they are not executable and are listed separately.
    actionable: bool = True
    inventory_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def american_to_implied(odds: int | float | None) -> float:
    """Convert American moneyline odds to implied probability [0.0, 1.0]."""
    if odds is None:
        return 0.50
    try:
        val = float(odds)
        if val < 0:
            return abs(val) / (abs(val) + 100.0)
        elif val > 0:
            return 100.0 / (val + 100.0)
        else:
            return 0.50
    except (TypeError, ValueError):
        return 0.50


def _extract_book_quote(
    books: list[dict[str, Any]], target_book: str = DEFAULT_TARGET_BOOK
) -> tuple[str, int | float | None]:
    """Find quote for target book, falling back to best retail book if absent."""
    if not isinstance(books, list):
        return ("UNKNOWN", None)

    # 1. Check target book aliases
    aliases = TARGET_BOOK_ALIASES if target_book in TARGET_BOOK_ALIASES else {target_book.upper()}
    for b in books:
        if isinstance(b, dict) and str(b.get("book") or "").upper() in aliases:
            return (target_book, b.get("odds"))

    # 2. Fallback: select best regulated retail book
    retail_preferred = ["DRAFTKINGS", "FANDUEL", "BETMGM", "CAESARS", "FANATICS", "ESPNBET"]
    for pref in retail_preferred:
        for b in books:
            if isinstance(b, dict) and str(b.get("book") or "").upper() == pref:
                return (pref, b.get("odds"))

    # 3. Fallback: first available (reported, but not a sportsbook we can execute at)
    for b in books:
        if isinstance(b, dict) and b.get("odds") is not None:
            return (str(b.get("book") or "RETAIL"), b.get("odds"))

    return ("NONE", None)


RETAIL_BOOKS = frozenset(
    {"DRAFTKINGS", "FANDUEL", "BETMGM", "CAESARS", "FANATICS", "ESPNBET"}
)


def _finite_odds(odds: Any) -> float | None:
    try:
        val = float(odds)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) and val != 0 else None


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
    aliases = TARGET_BOOK_ALIASES if target_book in TARGET_BOOK_ALIASES else {target_book.upper()}
    if book.upper() not in aliases and book.upper() not in RETAIL_BOOKS:
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

        if line < thresholds[mkt]:
            continue

        pkey = _player_key(row)
        cons_line = (
            legacy_map.get((player, mkt)) if legacy_map else cons_map.get((*pkey, mkt))
        )
        if cons_line is None or line >= cons_line:
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

        # Must have favored odds or heavy juice indicating a legitimate floor
        if b_odds is not None and b_odds > 0:
            continue

        cushion = cons_line - line
        cushion_pct = (cushion / cons_line * 100.0) if cons_line > 0 else 0.0
        cushion_ratio = cushion / cons_line if cons_line > 0 else 0.0

        l5 = float(row.get("l5_hit_rate") or 0.0)
        l10 = float(row.get("l10_hit_rate") or 0.0)
        season = float(row.get("season_hit_rate") or 0.0)
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

        # Composite confidence formula:
        # 35% L10 stability + 30% L5 form + 15% Season + 10% Cushion ratio + 10% Implied + context
        base_score = (
            (l10 * 0.35)
            + (l5 * 0.30)
            + (season * 0.15)
            + (cushion_ratio * 0.10)
            + ((implied_p or 0.0) * 0.10)
            + situational_adj
        )
        conf_score = max(0.0, min(1.0, round(base_score, 4)))

        # Target book match bonus in tie-breakers
        is_target_book = b_name.upper() in (
            TARGET_BOOK_ALIASES if target_book in TARGET_BOOK_ALIASES else {target_book.upper()}
        )

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
                "l5_hit_rate": round(l5, 2),
                "l10_hit_rate": round(l10, 2),
                "season_hit_rate": round(season, 2),
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
            actionable=best["inventory_reason"] is None,
            inventory_reason=best["inventory_reason"],
        )
        optimal_by_category[mkt].append(prop_obj)

    return optimal_by_category


def rank_alt_floors(
    candidates_by_category: dict[str, list[AltFloorProp]],
) -> tuple[dict[str, list[AltFloorProp]], list[AltFloorProp], list[AltFloorProp]]:
    """Rank actionable candidates into Top 3 per category and Master Top 9.

    Third element: non-actionable inventory (F23), never ranked.
    """
    top3_by_category: dict[str, list[AltFloorProp]] = {}
    master_pool: list[AltFloorProp] = []
    inventory: list[AltFloorProp] = []

    for mkt in ["PASS_YDS", "RUSH_YDS", "REC_YDS"]:
        everything = candidates_by_category.get(mkt, [])
        inventory.extend(p for p in everything if not p.actionable)
        pool = [p for p in everything if p.actionable]
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
                f"Historical convergence: L5 {int(prop.l5_hit_rate * 100)}% ({int(round(prop.l5_hit_rate * 5))}/5), "
                f"L10 {int(prop.l10_hit_rate * 100)}% ({int(round(prop.l10_hit_rate * 10))}/10)."
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
        "Evaluates true hit rate convergence (L5/L10/Season), safety cushion below consensus line, and environmental factors.  ",
        "**Juice Cap Policy**: Standalone straight wagers require odds >= -250. Odds worse than -250 (e.g. -325 to -700) are flagged `PARLAY ONLY` due to extreme downside injury asymmetry.",
        "",
        "---",
        "",
        "## Master Confidence Ranking (Top 1–9 Overall)",
        "",
        "| Rank | Player | Team | Market | Alt Line | Consensus | Cushion | Odds | Play Type | L5 Hit | L10 Hit | Confidence |",
        "|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for p in master_pool:
        odds_str = f"{p.target_odds:+d}" if isinstance(p.target_odds, int) else f"{p.target_odds}"
        lines.append(
            f"| **#{p.master_rank}** | **{p.player_name}** | {p.team} | {p.market_display} | "
            f"**OVER {p.line}** | {p.consensus_line} | +{p.cushion} ({p.cushion_pct}%) | "
            f"`{p.target_book} {odds_str}` | `{p.play_type}` | {int(p.l5_hit_rate * 100)}% | {int(p.l10_hit_rate * 100)}% | **{p.confidence_score:.3f}** |"
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
                f"  - **Sportsbook Quote**: `{p.target_book} {odds_str}` (Implied: {(p.implied_probability or 0.0) * 100:.1f}%) | **Play Type**: `{p.play_type}`"
            )
            lines.append(
                f"  - **Safety Cushion**: +{p.cushion:.1f} yards below consensus line of {p.consensus_line} ({p.cushion_pct:.1f}% discount)"
            )
            lines.append(
                f"  - **Hit Rates**: L5: {int(p.l5_hit_rate * 100)}% ({int(round(p.l5_hit_rate * 5))}/5) | "
                f"L10: {int(p.l10_hit_rate * 100)}% ({int(round(p.l10_hit_rate * 10))}/10) | "
                f"Season: {int(p.season_hit_rate * 100)}%"
            )
            lines.append(f"  - **Confidence Score**: `{p.confidence_score:.3f}` (Master Rank: #{p.master_rank})")
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
        "---",
        "",
        "## Parlay Construction Guidelines",
        "",
        "1. **Low-Floor Same-Game Parlay (SGP)**: Anchor correlating positive script legs in dome/neutral matchups (e.g. Starting QB Passing Floor + Workhorse RB Rushing Floor).",
        "2. **Cross-Game High-Confidence Parlay**: Select the top 1 play from each category (Pass #1 + Rush #1 + Rec #1) to capture cross-game diversification with massive statistical floors.",
        "3. **Alt Floor Side Restriction**: Per pipeline invariants, alternate player props must be parlayed across different games when combining alt lines.",
        "4. **Juice Cap Enforcement**: Props with odds worse than -250 must NEVER be wagered as straight bets; allocate them exclusively as SGP or cross-game parlay anchors.",
        "",
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
) -> dict[str, str]:
    """Export Alt Floors to JSON, CSV, and Markdown files.

    CSV and ranked records carry actionable floors only; inventory is listed
    separately in JSON and markdown.
    """
    exports_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    records = [p.to_dict() for p in master_pool]
    payload = {
        "date": date_str,
        "target_book": target_book,
        "count": len(records),
        "categories": {
            mkt: [p.to_dict() for p in props]
            for mkt, props in top3_by_category.items()
        },
        "records": records,
        "inventory_count": len(inventory or []),
        "inventory": [p.to_dict() for p in (inventory or [])],
    }

    # 1. JSON Exports
    json_dated = exports_dir / f"nfl_alt_floors_{date_str}.json"
    with open(json_dated, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if write_latest:
        json_latest = exports_dir / "nfl_alt_floors_latest.json"
        with open(json_latest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

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

    outputs = export_alt_floors(
        top3,
        master_pool,
        exports_dir=exports_dir,
        reports_dir=reports_dir,
        date_str=date_str,
        target_book=target_book,
        write_latest=write_latest,
        inventory=inventory,
    )

    return {
        "count": len(master_pool),
        "inventory_count": len(inventory),
        "top3_by_category": {mkt: [p.to_dict() for p in props] for mkt, props in top3.items()},
        "master_pool": [p.to_dict() for p in master_pool],
        "outputs": outputs,
    }
