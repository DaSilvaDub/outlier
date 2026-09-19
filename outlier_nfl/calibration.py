"""Game script calibration and empirical hit-rate tiering for outlier_nfl.

Implements calibration heuristics learned from empirical postgame reconciliation:
1. Deficit-Risk Haircut on Road Underdog RB Rushing Lines (-15% volume adjustment).
2. Two-High Shell Target Divergence in Comeback Mode (+20% Slot/TE, -25% Deep Threat).
3. Empirical Hit Rate Priority (Tier-1 Anchor classification for 100% L5 / 80%+ L10).
"""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from outlier_nfl.models import NflGameLine, NflPlayerProp

logger = logging.getLogger("outlier_nfl.calibration")

# Known archetypes for pass-catchers
KNOWN_SLOT_INTERMEDIATE_TARGETS: tuple[str, ...] = (
    "Amon-Ra St. Brown",
    "A. St. Brown",
    "Khalil Shakir",
    "K. Shakir",
    "Cooper Kupp",
    "C. Kupp",
    "Puka Nacua",
    "P. Nacua",
    "Keenan Allen",
    "K. Allen",
    "Christian Kirk",
    "C. Kirk",
    "Wan'Dale Robinson",
    "W. Robinson",
    "Jayden Reed",
    "J. Reed",
    "Zay Flowers",
    "Z. Flowers",
    "Tank Dell",
    "T. Dell",
    "Josh Downs",
    "J. Downs",
)

KNOWN_VERTICAL_DEEP_THREATS: tuple[str, ...] = (
    "Jameson Williams",
    "J. Williams",
    "Rashid Shaheed",
    "R. Shaheed",
    "Alec Pierce",
    "A. Pierce",
    "Christian Watson",
    "C. Watson",
    "Darius Slayton",
    "D. Slayton",
    "Marquez Valdes-Scantling",
    "M. Valdes-Scantling",
    "Gabe Davis",
    "G. Davis",
    "Jalin Hyatt",
    "J. Hyatt",
)


def extract_game_script_context(game_lines: list[NflGameLine]) -> dict[str, dict[str, Any]]:
    """Extract deficit risk and pace context per event and team.

    Returns a dict keyed by event_id containing team-specific game script contexts.
    """
    contexts: dict[str, dict[str, Any]] = {}

    # Group game lines by event_id
    by_event: dict[str, list[NflGameLine]] = {}
    for g in game_lines:
        if g.scope == "full_game":
            by_event.setdefault(g.event_id, []).append(g)

    for event_id, lines in by_event.items():
        home_team = lines[0].home_team if lines else ""
        away_team = lines[0].away_team if lines else ""

        # Extract Spreads
        spreads = [ln for ln in lines if ln.market == "SPREAD"]
        away_spread_val = 0.0
        for s in spreads:
            if s.team == away_team and float(s.line or 0) > 0:
                away_spread_val = max(away_spread_val, float(s.line or 0))

        # Extract Team Totals
        team_totals = [
            ln for ln in lines
            if ln.market_type == "TEAM_PROP" and ln.proposition in ("POINTS", "TOTAL_POINTS", "TEAM_TOTAL", "TOTAL")
        ]
        # Seeding the accumulator with the default made the default a floor, so a
        # team total genuinely below it was reported as the default instead.
        home_tt_quoted: float | None = None
        away_tt_quoted: float | None = None
        for tt in team_totals:
            if tt.line is None:
                continue
            tt_line = float(tt.line)
            if tt.team == home_team:
                home_tt_quoted = tt_line if home_tt_quoted is None else max(home_tt_quoted, tt_line)
            elif tt.team == away_team:
                away_tt_quoted = tt_line if away_tt_quoted is None else max(away_tt_quoted, tt_line)

        home_tt = home_tt_quoted if home_tt_quoted is not None else 27.0
        away_tt = away_tt_quoted if away_tt_quoted is not None else 21.0

        # Road underdog deficit risk trigger: away underdog >= +4.5 and home total >= 28.0
        away_deficit_risk = (away_spread_val >= 4.5) and (home_tt >= 28.0)

        contexts[event_id] = {
            "home_team": home_team,
            "away_team": away_team,
            "away_spread": away_spread_val,
            "home_team_total": home_tt,
            "away_team_total": away_tt,
            "away_deficit_risk": away_deficit_risk,
            "home_deficit_risk": False,
        }

    return contexts


def apply_game_script_calibration(
    game_lines: list[NflGameLine],
    props: list[NflPlayerProp],
) -> list[NflPlayerProp]:
    """Apply empirical game script calibration, volume haircuts, and tier ranking.

    Returns a new list of calibrated NflPlayerProp instances.
    """
    event_contexts = extract_game_script_context(game_lines)
    calibrated_props: list[NflPlayerProp] = []

    for prop in props:
        tags: list[str] = list(prop.calibration_tags)
        vol_adj: float | None = prop.calibrated_volume_adjustment
        tier: str = "STANDARD"

        ctx = event_contexts.get(prop.event_id, {})
        team = prop.team or ""
        is_underdog_in_deficit_risk = (
            (team == ctx.get("away_team") and ctx.get("away_deficit_risk"))
            or (team == ctx.get("home_team") and ctx.get("home_deficit_risk"))
        )

        # 1. Deficit-Risk Adjustment on Underdog Running Backs
        if is_underdog_in_deficit_risk:
            if prop.market in ("RUSH_YDS", "RUSH_ATT") and prop.position == "OVER":
                tags.append("DEFICIT_VOLUME_RISK")
                vol_adj = -0.15  # 15% downward volume haircut
            elif prop.market in ("ANYTIME_TD", "REC_YDS", "REC", "RUSH_REC_YDS"):
                tags.append("RESILIENT_GAME_SCRIPT_TARGET")

        # 2. Defensive Shell Target Divergence for Pass Catchers
        if is_underdog_in_deficit_risk:
            # Check slot/intermediate targets
            if any(name in prop.player_name for name in KNOWN_SLOT_INTERMEDIATE_TARGETS):
                tags.append("SHELL_COVERAGE_TARGET_UPGRADE")
                if prop.market in ("REC_YDS", "REC"):
                    vol_adj = (vol_adj or 0.0) + 0.20
            # Check tight end safety valves
            elif prop.market in ("REC_YDS", "REC") and any(te in prop.player_name for te in ("LaPorta", "Kincaid", "Kelce", "McBride", "Kittle", "Ferguson", "Hockenson", "Njoku", "Goedert")):
                tags.append("SHELL_COVERAGE_TARGET_UPGRADE")
                vol_adj = (vol_adj or 0.0) + 0.15
            # Check vertical deep threats
            elif any(name in prop.player_name for name in KNOWN_VERTICAL_DEEP_THREATS):
                tags.append("SHELL_COVERAGE_DEEP_HAIRCUT")
                if prop.market in ("REC_YDS", "REC"):
                    vol_adj = (vol_adj or 0.0) - 0.25

        # 3. Empirical Hit Rate Priority (Tier 1 / Tier 2)
        l5 = prop.l5_hit_rate
        l10 = prop.l10_hit_rate
        book_count = len(prop.books)

        if l5 is not None and l5 == 1.0 and l10 is not None and l10 >= 0.80 and book_count >= 3:
            tier = "TIER_1_ANCHOR"
            tags.append("HIGH_HIT_RATE_ANCHOR")
        elif l5 is not None and l5 >= 0.80 and l10 is not None and l10 >= 0.70:
            tier = "TIER_2_STRONG"
            tags.append("CONSISTENT_HIT_RATE")

        calibrated_props.append(
            replace(
                prop,
                confidence_tier=tier,
                calibration_tags=tuple(tags),
                calibrated_volume_adjustment=round(vol_adj, 2) if vol_adj is not None else None,
            )
        )

    return calibrated_props
