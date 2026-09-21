"""Active roster extraction, full 32-team depth chart registry, and offseason movement verification for outlier_nfl.

Enforces 100% deterministic roster integrity before any pipeline execution or qualitative analysis.
Prevents stale historical pre-training hallucinations by:
1. Maintaining a full 32-team 2026 depth chart registry (starting QBs, primary RBs, pass-catchers).
2. Tracking all major 2026 offseason free agent signings and trades (e.g. Kenneth Walker III signed by KC,
   Daniel Jones starting on IND, Aaron Rodgers on PIT, Geno Smith on NYJ, DK Metcalf on PIT).
3. Providing pre-flight verification gates that scan text and data feeds to block references to former teams.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.roster")


class RosterIntegrityError(ValueError):
    """Raised when analysis text or data feeds violate verified 2026 NFL roster truth."""


# Explicit 2026 Offseason Player Movement Registry.
# Used by pre-flight validation gates to intercept and reject any reference to former teams.
OFFSEASON_MOVES_2026: dict[str, dict[str, Any]] = {
    "Kenneth Walker III": {
        "current_team": "KC",
        "former_teams": ["SEA", "Seahawks", "Seattle"],
        "pos": "RB",
        "role": "Lead Running Back (RB1)",
        "notes": "Signed by Kansas City Chiefs in 2026 free agency to lead the backfield.",
    },
    "Daniel Jones": {
        "current_team": "IND",
        "former_teams": ["NYG", "Giants", "New York Giants"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the Indianapolis Colts. Anthony Richardson is NOT starting.",
    },
    "Aaron Rodgers": {
        "current_team": "PIT",
        "former_teams": ["NYJ", "Jets", "New York Jets", "GB", "Packers"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the Pittsburgh Steelers.",
    },
    "Geno Smith": {
        "current_team": "NYJ",
        "former_teams": ["SEA", "Seahawks", "Seattle"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the New York Jets.",
    },
    "Carson Wentz": {
        "current_team": "MIN",
        "former_teams": ["LAR", "Rams", "KC", "Chiefs", "WAS", "Commanders"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the Minnesota Vikings.",
    },
    "Cam Ward": {
        "current_team": "TEN",
        "former_teams": ["Miami Hurricanes"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting rookie QB for the Tennessee Titans.",
    },
    "Cooper Rush": {
        "current_team": "ATL",
        "former_teams": ["DAL", "Cowboys", "Dallas"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the Atlanta Falcons.",
    },
    "Tyler Shough": {
        "current_team": "NO",
        "former_teams": ["Louisville"],
        "pos": "QB",
        "role": "Starting Quarterback",
        "notes": "Starting QB for the New Orleans Saints.",
    },
    "DK Metcalf": {
        "current_team": "PIT",
        "former_teams": ["SEA", "Seahawks", "Seattle"],
        "pos": "WR",
        "role": "Featured Perimeter Receiver (WR1)",
        "notes": "Acquired by Pittsburgh Steelers to partner with Aaron Rodgers.",
    },
    "David Montgomery": {
        "current_team": "HOU",
        "former_teams": ["DET", "Lions", "Detroit", "CHI", "Bears"],
        "pos": "RB",
        "role": "Primary Running Back",
        "notes": "Acquired by Houston Texans.",
    },
    "Travis Etienne Jr.": {
        "current_team": "NO",
        "former_teams": ["JAX", "Jaguars", "Jacksonville"],
        "pos": "RB",
        "role": "Primary Running Back",
        "notes": "Acquired by New Orleans Saints.",
    },
    "DJ Moore": {
        "current_team": "BUF",
        "former_teams": ["CHI", "Bears", "Chicago", "CAR", "Panthers"],
        "pos": "WR",
        "role": "Primary Outside Receiver",
        "notes": "Acquired by Buffalo Bills.",
    },
    "George Pickens": {
        "current_team": "DAL",
        "former_teams": ["PIT", "Steelers", "Pittsburgh"],
        "pos": "WR",
        "role": "Starting Wide Receiver",
        "notes": "Acquired by Dallas Cowboys.",
    },
    "Javonte Williams": {
        "current_team": "DAL",
        "former_teams": ["DEN", "Broncos", "Denver"],
        "pos": "RB",
        "role": "Lead Running Back",
        "notes": "Signed by Dallas Cowboys.",
    },
    "Cooper Kupp": {
        "current_team": "SEA",
        "former_teams": ["LAR", "Rams", "Los Angeles Rams"],
        "pos": "WR",
        "role": "Slot / Possession Receiver",
        "notes": "Signed by Seattle Seahawks.",
    },
    "Stefon Diggs": {
        "current_team": "WAS",
        "former_teams": ["HOU", "Texans", "BUF", "Bills"],
        "pos": "WR",
        "role": "Featured Wide Receiver",
        "notes": "Signed by Washington Commanders.",
    },
    "A.J. Brown": {
        "current_team": "NE",
        "former_teams": ["PHI", "Eagles", "Philadelphia", "TEN", "Titans"],
        "pos": "WR",
        "role": "WR1 / Alpha Receiver",
        "notes": "Acquired by New England Patriots. Is NOT on the Philadelphia Eagles.",
    },
    "AJ Brown": {
        "current_team": "NE",
        "former_teams": ["PHI", "Eagles", "Philadelphia", "TEN", "Titans"],
        "pos": "WR",
        "role": "WR1 / Alpha Receiver",
        "notes": "Acquired by New England Patriots. Is NOT on the Philadelphia Eagles.",
    },
    "Hollywood Brown": {
        "current_team": "PHI",
        "former_teams": ["KC", "Chiefs", "Kansas City", "ARI", "Cardinals", "BAL", "Ravens"],
        "pos": "WR",
        "role": "Starting Wide Receiver",
        "notes": "Acquired by Philadelphia Eagles. Is NOT on the Kansas City Chiefs.",
    },
    "Marquise Brown": {
        "current_team": "PHI",
        "former_teams": ["KC", "Chiefs", "Kansas City", "ARI", "Cardinals", "BAL", "Ravens"],
        "pos": "WR",
        "role": "Starting Wide Receiver",
        "notes": "Acquired by Philadelphia Eagles. Is NOT on the Kansas City Chiefs.",
    },
    "Dontayvion Wicks": {
        "current_team": "PHI",
        "former_teams": ["GB", "Packers", "Green Bay"],
        "pos": "WR",
        "role": "Wide Receiver",
        "notes": "Signed by Philadelphia Eagles.",
    },
    "Darren Waller": {
        "current_team": "CAR",
        "former_teams": ["NYG", "Giants", "LV", "Raiders"],
        "pos": "TE",
        "role": "Starting Tight End",
        "notes": "Signed by Carolina Panthers.",
    },
}

# Authoritative 32-team 2026 NFL depth chart registry.
NFL_2026_FULL_DEPTH_CHARTS: dict[str, dict[str, Any]] = {
    "ARI": {
        "name": "Arizona Cardinals",
        "starting_qb": "Kyler Murray",
        "rbs": ["James Conner", "Trey Benson"],
        "wrs": ["Marvin Harrison Jr.", "Michael Wilson", "Greg Dortch"],
        "te": "Trey McBride",
    },
    "ATL": {
        "name": "Atlanta Falcons",
        "starting_qb": "Cooper Rush",
        "rbs": ["Bijan Robinson", "Tyler Allgeier"],
        "wrs": ["Drake London", "Darnell Mooney", "Ray-Ray McCloud"],
        "te": "Kyle Pitts Sr.",
    },
    "BAL": {
        "name": "Baltimore Ravens",
        "starting_qb": "Lamar Jackson",
        "rbs": ["Derrick Henry", "Justice Hill"],
        "wrs": ["Zay Flowers", "Rashod Bateman", "LaJohntay Wester"],
        "te": "Mark Andrews",
    },
    "BUF": {
        "name": "Buffalo Bills",
        "starting_qb": "Josh Allen",
        "rbs": ["James Cook III", "Ray Davis", "Ty Johnson"],
        "wrs": ["DJ Moore", "Khalil Shakir", "Keon Coleman"],
        "te": "Dalton Kincaid",
    },
    "CAR": {
        "name": "Carolina Panthers",
        "starting_qb": "Bryce Young",
        "rbs": ["Chuba Hubbard", "Jonathon Brooks"],
        "wrs": ["Tetairoa McMillan", "Jalen Coker", "Xavier Legette"],
        "te": "Darren Waller",
    },
    "CHI": {
        "name": "Chicago Bears",
        "starting_qb": "Caleb Williams",
        "rbs": ["D'Andre Swift", "Kyle Monangai", "Roschon Johnson"],
        "wrs": ["Rome Odunze", "Luther Burden III", "Keenan Allen"],
        "te": "Cole Kmet",
    },
    "CIN": {
        "name": "Cincinnati Bengals",
        "starting_qb": "Joe Burrow",
        "rbs": ["Chase Brown", "Samaje Perine"],
        "wrs": ["Ja'Marr Chase", "Tee Higgins", "Jermaine Burton"],
        "te": "Mike Gesicki",
    },
    "CLE": {
        "name": "Cleveland Browns",
        "starting_qb": "Deshaun Watson",
        "rbs": ["Quinshon Judkins", "Jerome Ford"],
        "wrs": ["Jerry Jeudy", "Denzel Boston", "KC Concepcion Jr."],
        "te": "Harold Fannin Jr.",
    },
    "DAL": {
        "name": "Dallas Cowboys",
        "starting_qb": "Dak Prescott",
        "rbs": ["Javonte Williams", "Rico Dowdle"],
        "wrs": ["CeeDee Lamb", "George Pickens", "Jalen Tolbert"],
        "te": "Jake Ferguson",
    },
    "DEN": {
        "name": "Denver Broncos",
        "starting_qb": "Bo Nix",
        "rbs": ["Audric Estime", "Jaleel McLaughlin"],
        "wrs": ["Courtland Sutton", "Troy Franklin", "Marvin Mims Jr."],
        "te": "Greg Dulcich",
    },
    "DET": {
        "name": "Detroit Lions",
        "starting_qb": "Jared Goff",
        "rbs": ["Jahmyr Gibbs", "Sione Vaki"],
        "wrs": ["Amon-Ra St. Brown", "Jameson Williams", "Kalif Raymond"],
        "te": "Sam LaPorta",
    },
    "GB": {
        "name": "Green Bay Packers",
        "starting_qb": "Jordan Love",
        "rbs": ["Josh Jacobs", "MarShawn Lloyd", "Chris Brooks"],
        "wrs": ["Christian Watson", "Jayden Reed", "Romeo Doubs"],
        "te": "Tucker Kraft",
    },
    "HOU": {
        "name": "Houston Texans",
        "starting_qb": "C.J. Stroud",
        "rbs": ["David Montgomery", "Woody Marks"],
        "wrs": ["Nico Collins", "Tank Dell", "Kayshon Boutte"],
        "te": "Dalton Schultz",
    },
    "IND": {
        "name": "Indianapolis Colts",
        "starting_qb": "Daniel Jones",
        "rbs": ["Jonathan Taylor", "Tyler Goodson"],
        "wrs": ["Michael Pittman Jr.", "Josh Downs", "Alec Pierce"],
        "te": "Drew Ogletree",
    },
    "JAX": {
        "name": "Jacksonville Jaguars",
        "starting_qb": "Trevor Lawrence",
        "rbs": ["Tank Bigsby", "Bhayshul Tuten"],
        "wrs": ["Brian Thomas Jr.", "Parker Washington", "Christian Kirk"],
        "te": "Evan Engram",
    },
    "KC": {
        "name": "Kansas City Chiefs",
        "starting_qb": "Patrick Mahomes",
        "rbs": ["Kenneth Walker III", "Carson Steele", "Kareem Hunt"],
        "wrs": ["Rashee Rice", "Xavier Worthy", "Justin Watson"],
        "te": "Travis Kelce",
    },
    "LAC": {
        "name": "Los Angeles Chargers",
        "starting_qb": "Justin Herbert",
        "rbs": ["Omarion Hampton", "Gus Edwards", "J.K. Dobbins"],
        "wrs": ["Ladd McConkey", "Tre' Harris", "Quentin Johnston"],
        "te": "David Njoku",
    },
    "LAR": {
        "name": "Los Angeles Rams",
        "starting_qb": "Matthew Stafford",
        "rbs": ["Kyren Williams", "Blake Corum"],
        "wrs": ["Puka Nacua", "Demarcus Robinson", "Tutu Atwell"],
        "te": "Tyler Higbee",
    },
    "LV": {
        "name": "Las Vegas Raiders",
        "starting_qb": "Kirk Cousins",
        "rbs": ["Zamir White", "Alexander Mattison"],
        "wrs": ["Jakobi Meyers", "Tre Tucker"],
        "te": "Brock Bowers",
    },
    "MIA": {
        "name": "Miami Dolphins",
        "starting_qb": "Tua Tagovailoa",
        "rbs": ["De'Von Achane", "Jaylen Wright", "Raheem Mostert"],
        "wrs": ["Tyreek Hill", "Jaylen Waddle", "Malik Washington"],
        "te": "Jonnu Smith",
    },
    "MIN": {
        "name": "Minnesota Vikings",
        "starting_qb": "Carson Wentz",
        "rbs": ["Aaron Jones Sr.", "Ty Chandler"],
        "wrs": ["Justin Jefferson", "Jordan Addison", "Jalen Nailor"],
        "te": "T.J. Hockenson",
    },
    "NE": {
        "name": "New England Patriots",
        "starting_qb": "Drake Maye",
        "rbs": ["Rhamondre Stevenson", "TreVeyon Henderson"],
        "wrs": ["A.J. Brown", "DeMario Douglas", "Ja'Lynn Polk", "Mack Hollins"],
        "te": "Hunter Henry",
    },
    "NO": {
        "name": "New Orleans Saints",
        "starting_qb": "Tyler Shough",
        "rbs": ["Travis Etienne Jr.", "Alvin Kamara", "Kendre Miller"],
        "wrs": ["Chris Olave", "Devaughn Vele", "Rashid Shaheed"],
        "te": "Juwan Johnson",
    },
    "NYG": {
        "name": "New York Giants",
        "starting_qb": "Russell Wilson",
        "rbs": ["Tyrone Tracy Jr.", "Devin Singletary"],
        "wrs": ["Malik Nabers", "Wan'Dale Robinson", "Darius Slayton"],
        "te": "Theo Johnson",
    },
    "NYJ": {
        "name": "New York Jets",
        "starting_qb": "Geno Smith",
        "rbs": ["Breece Hall", "Braelon Allen"],
        "wrs": ["Garrett Wilson", "Adonai Mitchell", "Allen Lazard"],
        "te": "Kenyon Sadiq",
    },
    "PHI": {
        "name": "Philadelphia Eagles",
        "starting_qb": "Jalen Hurts",
        "rbs": ["Saquon Barkley", "Kenneth Gainwell"],
        "wrs": ["DeVonta Smith", "Hollywood Brown", "Dontayvion Wicks"],
        "te": "Dallas Goedert",
    },
    "PIT": {
        "name": "Pittsburgh Steelers",
        "starting_qb": "Aaron Rodgers",
        "rbs": ["Jaylen Warren", "Najee Harris"],
        "wrs": ["DK Metcalf", "Roman Wilson", "Van Jefferson"],
        "te": "Pat Freiermuth",
    },
    "SEA": {
        "name": "Seattle Seahawks",
        "starting_qb": "Sam Darnold",
        "rbs": ["Zach Charbonnet", "George Holani"],
        "wrs": ["Jaxon Smith-Njigba", "Cooper Kupp", "Jake Bobo"],
        "te": "Noah Fant",
    },
    "SF": {
        "name": "San Francisco 49ers",
        "starting_qb": "Brock Purdy",
        "rbs": ["Christian McCaffrey", "Jordan Mason", "Isaac Guerendo"],
        "wrs": ["Deebo Samuel", "Brandon Aiyuk", "Jauan Jennings"],
        "te": "George Kittle",
    },
    "TB": {
        "name": "Tampa Bay Buccaneers",
        "starting_qb": "Baker Mayfield",
        "rbs": ["Bucky Irving", "Rachaad White"],
        "wrs": ["Mike Evans", "Chris Godwin Jr.", "Emeka Egbuka"],
        "te": "Cade Otton",
    },
    "TEN": {
        "name": "Tennessee Titans",
        "starting_qb": "Cam Ward",
        "rbs": ["Tony Pollard", "Tyjae Spears"],
        "wrs": ["Calvin Ridley", "Carnell Tate", "Nick Westbrook-Ikhine"],
        "te": "Chig Okonkwo",
    },
    "WAS": {
        "name": "Washington Commanders",
        "starting_qb": "Jayden Daniels",
        "rbs": ["Brian Robinson Jr.", "Austin Ekeler"],
        "wrs": ["Terry McLaurin", "Stefon Diggs", "Luke McCaffrey"],
        "te": "Zach Ertz",
    },
}

NFL_2026_STARTING_QBS: dict[str, str] = {
    t: info["starting_qb"] for t, info in NFL_2026_FULL_DEPTH_CHARTS.items()
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


def get_team_depth_chart(team: str) -> dict[str, Any]:
    """Retrieve full verified depth chart for a team."""
    t_clean = team.strip().upper()
    if t_clean not in NFL_2026_FULL_DEPTH_CHARTS:
        raise ValueError(f"Unknown NFL franchise code: {team}")
    return dict(NFL_2026_FULL_DEPTH_CHARTS[t_clean])


def validate_analysis_text_for_roster_errors(text: str) -> list[str]:
    """Inspect qualitative text or generated reports for stale former-team affiliations.

    Returns:
        List of error descriptions. If non-empty, the text contains hallucinations.
    """
    errors: list[str] = []
    text_lower = text.lower()

    for player, meta in OFFSEASON_MOVES_2026.items():
        p_name = player.lower()
        if p_name not in text_lower:
            continue

        curr_team = meta["current_team"].lower()
        former_teams = [f.lower() for f in meta["former_teams"]]

        player_violation_found = False
        # Check if text associates player with former team
        for former in former_teams:
            # Match patterns like "Walker (SEA)", "Walker of the Seahawks", "Rodgers on the Jets"
            patterns = [
                rf"{p_name}[^.\n]*?(?:on|with|of|for)\s+(?:the\s+)?{former}",
                rf"{former}[^.\n]*?{p_name}",
                rf"{p_name}\s*\(\s*{former}\s*\)",
            ]
            for pat in patterns:
                if re.search(pat, text_lower):
                    errors.append(
                        f"Roster Violation: {player} was attributed to former team '{former.upper()}'. "
                        f"Current verified 2026 team is {meta['current_team']} ({meta['role']})."
                    )
                    player_violation_found = True
                    break
            if player_violation_found:
                break

    # Specific check for Anthony Richardson starting
    if "anthony richardson" in text_lower and any(kw in text_lower for kw in ["starting", "starts", "qb1", "starter"]):
        # Verify it is not explicitly saying he is NOT starting
        if not re.search(r"(?:not|isn't|is not)\s+starting", text_lower):
            errors.append(
                "Roster Violation: Anthony Richardson was referred to as starting. "
                "Daniel Jones is the verified 2026 starting QB for the Indianapolis Colts."
            )

    return errors


def build_team_roster_index(
    props: list[NflPlayerProp] | list[dict[str, Any]],
    include_league_baseline: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build a verified active roster index mapping each team code to their starting QB and key skill players.

    Args:
        props: Player prop records from Outlier normalized feeds.
        include_league_baseline: If True, populates all 32 NFL teams with baseline 2026 starting
            QBs and skill players so kickoff window filters do not erase league-wide ground truth.

    Returns:
        Dictionary keyed by canonical team code (e.g. 'IND', 'KC', 'PIT', 'NYJ') containing:
        - 'starting_qb': Name of the primary passer
        - 'key_rbs': List of top rushing personnel
        - 'key_pass_catchers': List of top receiving targets
    """
    rosters: dict[str, dict[str, Any]] = {}

    if include_league_baseline:
        for t, chart in NFL_2026_FULL_DEPTH_CHARTS.items():
            rosters[t] = {
                "team": t,
                "starting_qb": chart["starting_qb"],
                "key_rbs": list(chart.get("rbs", [])),
                "key_pass_catchers": list(chart.get("wrs", [])) + ([chart["te"]] if chart.get("te") else []),
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

        base_rbs = rosters.get(t, {}).get("key_rbs", [])
        base_wrs = rosters.get(t, {}).get("key_pass_catchers", [])

        # Merge feed rushers ahead of baseline
        combined_rbs = top_rbs + [r for r in base_rbs if r not in top_rbs]
        combined_wrs = top_wrs + [w for w in base_wrs if w not in top_wrs]

        rosters[t] = {
            "team": t,
            "starting_qb": top_qb[0][0] if top_qb else rosters.get(t, {}).get("starting_qb"),
            "key_rbs": combined_rbs,
            "key_pass_catchers": combined_wrs,
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

    # Check for known former-team violations
    for move_player, move_meta in OFFSEASON_MOVES_2026.items():
        if move_player.lower() in p_clean or p_clean in move_player.lower():
            if t_clean != move_meta["current_team"]:
                return False
            return True

    # If position is quarterback, enforce strict starter match
    if position and position.upper() == "QB":
        return bool(p_clean in verified_qb or verified_qb in p_clean)

    # Check QB match
    if verified_qb and (p_clean in verified_qb or verified_qb in p_clean):
        return True

    # Check known RBs/WRs from depth chart
    if team_info:
        for rb in team_info.get("key_rbs", []):
            if p_clean in str(rb).lower() or str(rb).lower() in p_clean:
                return True

        for wr in team_info.get("key_pass_catchers", []):
            if p_clean in str(wr).lower() or str(wr).lower() in p_clean:
                return True

    return False
