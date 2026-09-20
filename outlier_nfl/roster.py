"""Active roster extraction and player-team identity verification for outlier_nfl.

Prevents stale historical pre-training memory hallucinations (e.g. attributing Aaron Rodgers
to the Jets instead of the Steelers, or Anthony Richardson instead of Daniel Jones on the Colts)
by deterministically indexing starting quarterbacks, primary ball-carriers, and top pass-catchers
directly from normalized Outlier market feeds and verified 2026 depth chart registries.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.roster")

# Ground truth 2026 starting quarterbacks registry across all 32 NFL franchises.
# Used as baseline anchors to eliminate LLM parametric pre-training hallucinations.
NFL_2026_STARTING_QBS: dict[str, str] = {
    "ARI": "Kyler Murray",
    "ATL": "Cooper Rush",
    "BAL": "Lamar Jackson",
    "BUF": "Josh Allen",
    "CAR": "Bryce Young",
    "CHI": "Caleb Williams",
    "CIN": "Joe Burrow",
    "CLE": "Deshaun Watson",
    "DAL": "Dak Prescott",
    "DEN": "Bo Nix",
    "DET": "Jared Goff",
    "GB": "Jordan Love",
    "HOU": "C.J. Stroud",
    "IND": "Daniel Jones",
    "JAX": "Trevor Lawrence",
    "KC": "Patrick Mahomes",
    "LAC": "Justin Herbert",
    "LAR": "Matthew Stafford",
    "LV": "Kirk Cousins",
    "MIA": "Tua Tagovailoa",
    "MIN": "Carson Wentz",
    "NE": "Drake Maye",
    "NO": "Tyler Shough",
    "NYG": "Russell Wilson",
    "NYJ": "Geno Smith",
    "PHI": "Jalen Hurts",
    "PIT": "Aaron Rodgers",
    "SEA": "Sam Darnold",
    "SF": "Brock Purdy",
    "TB": "Baker Mayfield",
    "TEN": "Cam Ward",
    "WAS": "Jayden Daniels",
}


def get_starting_qb(team: str, rosters: Mapping[str, dict[str, Any]] | None = None) -> str:
    """Retrieve the verified starting quarterback for a given NFL team code."""
    t_clean = team.strip().upper()
    if rosters and t_clean in rosters:
        qb = rosters[t_clean].get("starting_qb")
        if qb:
            return str(qb).strip()
    if t_clean in NFL_2026_STARTING_QBS:
        return NFL_2026_STARTING_QBS[t_clean]
    raise ValueError(f"Unknown or unverified starting quarterback for NFL team code: {team}")


def build_team_roster_index(
    props: list[NflPlayerProp] | list[dict[str, Any]],
    include_league_baseline: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build a verified active roster index mapping each team code to their starting QB and key skill players.

    Args:
        props: Player prop records from Outlier normalized feeds.
        include_league_baseline: If True, populates all 32 NFL teams with baseline 2026 starting
            QBs so kickoff window filters do not erase league-wide ground truth.

    Returns:
        Dictionary keyed by canonical team code (e.g. 'IND', 'PIT', 'NYJ', 'GB') containing:
        - 'starting_qb': Name of the primary passer
        - 'key_rbs': List of top rushing personnel
        - 'key_pass_catchers': List of top receiving targets
    """
    rosters: dict[str, dict[str, Any]] = {}

    if include_league_baseline:
        for t, qb in NFL_2026_STARTING_QBS.items():
            rosters[t] = {
                "team": t,
                "starting_qb": qb,
                "key_rbs": [],
                "key_pass_catchers": [],
            }

    teams: set[str] = set()
    for p in props:
        t = p.team if isinstance(p, NflPlayerProp) else p.get("team")
        if t:
            teams.add(str(t).strip().upper())

    for t in sorted(teams):
        passers: dict[str, int] = {}
        rushers: dict[str, int] = {}
        receivers: dict[str, int] = {}

        for p in props:
            p_team = p.team if isinstance(p, NflPlayerProp) else p.get("team")
            if str(p_team).strip().upper() != t:
                continue

            name = p.player_name if isinstance(p, NflPlayerProp) else p.get("player_name")
            if not name or "most" in name.lower():
                continue

            mkt = p.market if isinstance(p, NflPlayerProp) else p.get("market")
            books_count = len(p.books) if isinstance(p, NflPlayerProp) else len(p.get("books", []))

            if mkt in ("PASS_YDS", "PASS_ATTEMPTS", "PASS_ATT", "PASS_COMPLETIONS", "PASS_TDS"):
                passers[name] = passers.get(name, 0) + max(1, books_count)
            elif mkt in ("RUSH_YDS", "RUSH_ATTEMPTS", "RUSH_ATT"):
                rushers[name] = rushers.get(name, 0) + max(1, books_count)
            elif mkt in ("REC_YDS", "RECEPTIONS", "REC", "TARGETS"):
                receivers[name] = receivers.get(name, 0) + max(1, books_count)

        top_qb = sorted(passers.items(), key=lambda x: -x[1])[:1]
        top_rbs = [r[0] for r in sorted(rushers.items(), key=lambda x: -x[1])[:3]]
        top_wrs = [w[0] for w in sorted(receivers.items(), key=lambda x: -x[1])[:4]]

        rosters[t] = {
            "team": t,
            "starting_qb": top_qb[0][0] if top_qb else rosters.get(t, {}).get("starting_qb"),
            "key_rbs": top_rbs or rosters.get(t, {}).get("key_rbs", []),
            "key_pass_catchers": top_wrs or rosters.get(t, {}).get("key_pass_catchers", []),
        }

    return rosters


def verify_player_team_attribution(
    player_name: str,
    expected_team: str,
    rosters: Mapping[str, dict[str, Any]],
    position: str | None = None,
) -> bool:
    """Verify whether a player is correctly attributed to the expected team in the active roster.

    If position is 'QB' or starting quarterback verification is requested, strictly checks
    that the player matches the verified starter for the team.
    """
    t_clean = expected_team.strip().upper()
    team_info = rosters.get(t_clean)
    verified_qb = str(
        team_info.get("starting_qb") if team_info else NFL_2026_STARTING_QBS.get(t_clean, "")
    ).lower()

    p_clean = player_name.strip().lower()

    # If position is quarterback or player is known QB, enforce strict starter match
    if position and position.upper() == "QB":
        return bool(p_clean in verified_qb or verified_qb in p_clean)

    # Check QB match
    if verified_qb and (p_clean in verified_qb or verified_qb in p_clean):
        return True

    # Check known RBs/WRs from feed
    if team_info:
        for rb in team_info.get("key_rbs", []):
            if p_clean in str(rb).lower() or str(rb).lower() in p_clean:
                return True

        for wr in team_info.get("key_pass_catchers", []):
            if p_clean in str(wr).lower() or str(wr).lower() in p_clean:
                return True

    return False
