"""Active roster extraction and player-team identity verification for outlier_nfl.

Prevents stale historical pre-training memory hallucinations (e.g. attributing Aaron Rodgers
to the Jets instead of the Steelers) by deterministically indexing starting quarterbacks,
primary ball-carriers, and top pass-catchers directly from normalized Outlier market feeds.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.roster")

# Aggregate "Most Passing Yards"-style markets carry no playerName, so props.py falls
# back to the market label and they arrive here as a player. Match "most" as a whole
# word: a bare substring test also swallows real surnames that contain it (Raheem
# Mostert), dropping an active ball-carrier from the index and then reporting him as
# a mis-attribution.
_AGGREGATE_NAME_RE = re.compile(r"\bmost\b", re.IGNORECASE)


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
            if not name or _AGGREGATE_NAME_RE.search(name):
                continue

            mkt = p.market if isinstance(p, NflPlayerProp) else p.get("market")
            books_count = len(p.books) if isinstance(p, NflPlayerProp) else len(p.get("books", []))

            # PASS_COMP / PASS_TD are the codes normalize_market actually emits
            # (config.PROP_PASS_COMP / PROP_PASS_TDS); the longer spellings never match.
            if mkt in (
                "PASS_YDS",
                "PASS_ATTEMPTS",
                "PASS_ATT",
                "PASS_COMP",
                "PASS_COMPLETIONS",
                "PASS_TD",
                "PASS_TDS",
            ):
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


def _names_overlap(candidate: str, indexed: str) -> bool:
    """Substring match between two already-lowercased names, both required non-empty.

    A team with no indexed starting QB stores ``None``; without the emptiness guard
    ``"" in candidate`` is always True and the verifier passes every player on that
    team, which is the exact hallucination this module exists to catch.
    """
    if not candidate or not indexed:
        return False
    return candidate in indexed or indexed in candidate


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
    if not p_clean:
        return False

    qb = str(team_info.get("starting_qb") or "").strip().lower()
    if _names_overlap(p_clean, qb):
        return True

    for rb in team_info.get("key_rbs", []):
        if _names_overlap(p_clean, str(rb).strip().lower()):
            return True

    for wr in team_info.get("key_pass_catchers", []):
        if _names_overlap(p_clean, str(wr).strip().lower()):
            return True

    return False
