"""Active roster extraction and player-team identity verification for outlier_nfl.

Prevents stale historical pre-training memory hallucinations (e.g. attributing Aaron Rodgers
to the Jets instead of the Steelers) by deterministically indexing starting quarterbacks,
primary ball-carriers, and top pass-catchers directly from normalized Outlier market feeds.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.roster")


def build_team_roster_index(props: list[NflPlayerProp] | list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a verified active roster index mapping each team code to their starting QB and key skill players.

    Returns:
        Dictionary keyed by canonical team code (e.g. 'PIT', 'NYJ', 'GB') containing:
        - 'starting_qb': Name of the primary passer
        - 'key_rbs': List of top rushing personnel
        - 'key_pass_catchers': List of top receiving targets
    """
    teams: set[str] = set()
    for p in props:
        t = p.team if isinstance(p, NflPlayerProp) else p.get("team")
        if t:
            teams.add(str(t).strip().upper())

    rosters: dict[str, dict[str, Any]] = {}
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
            "starting_qb": top_qb[0][0] if top_qb else None,
            "key_rbs": top_rbs,
            "key_pass_catchers": top_wrs,
        }

    return rosters


def verify_player_team_attribution(
    player_name: str,
    expected_team: str,
    rosters: Mapping[str, dict[str, Any]],
) -> bool:
    """Verify whether a player is correctly attributed to the expected team in the active roster."""
    t_clean = expected_team.strip().upper()
    team_info = rosters.get(t_clean)
    if not team_info:
        return True  # Team not in current slate index

    p_clean = player_name.strip().lower()
    qb = str(team_info.get("starting_qb") or "").lower()
    if p_clean in qb or qb in p_clean:
        return True

    for rb in team_info.get("key_rbs", []):
        if p_clean in str(rb).lower() or str(rb).lower() in p_clean:
            return True

    for wr in team_info.get("key_pass_catchers", []):
        if p_clean in str(wr).lower() or str(wr).lower() in p_clean:
            return True

    return False
