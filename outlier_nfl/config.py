"""Configuration, 32-team registry, and market taxonomy for outlier_nfl.

Provides canonical mappings for all 32 NFL franchises and comprehensive
alias normalization for NFL team props, game lines, and player props.
Completely standalone with zero runtime coupling to outlier_scrapers.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

# -----------------------------------------------------------------------------
# 32 NFL Franchises Registry
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class NflTeamInfo:
    code: str
    name: str
    city: str
    nickname: str
    conference: str
    division: str


NFL_TEAMS: Mapping[str, NflTeamInfo] = {
    # AFC East
    "BUF": NflTeamInfo("BUF", "Buffalo Bills", "Buffalo", "Bills", "AFC", "East"),
    "MIA": NflTeamInfo("MIA", "Miami Dolphins", "Miami", "Dolphins", "AFC", "East"),
    "NE": NflTeamInfo("NE", "New England Patriots", "New England", "Patriots", "AFC", "East"),
    "NYJ": NflTeamInfo("NYJ", "New York Jets", "New York", "Jets", "AFC", "East"),
    # AFC North
    "BAL": NflTeamInfo("BAL", "Baltimore Ravens", "Baltimore", "Ravens", "AFC", "North"),
    "CIN": NflTeamInfo("CIN", "Cincinnati Bengals", "Cincinnati", "Bengals", "AFC", "North"),
    "CLE": NflTeamInfo("CLE", "Cleveland Browns", "Cleveland", "Browns", "AFC", "North"),
    "PIT": NflTeamInfo("PIT", "Pittsburgh Steelers", "Pittsburgh", "Steelers", "AFC", "North"),
    # AFC South
    "HOU": NflTeamInfo("HOU", "Houston Texans", "Houston", "Texans", "AFC", "South"),
    "IND": NflTeamInfo("IND", "Indianapolis Colts", "Indianapolis", "Colts", "AFC", "South"),
    "JAX": NflTeamInfo("JAX", "Jacksonville Jaguars", "Jacksonville", "Jaguars", "AFC", "South"),
    "TEN": NflTeamInfo("TEN", "Tennessee Titans", "Tennessee", "Titans", "AFC", "South"),
    # AFC West
    "DEN": NflTeamInfo("DEN", "Denver Broncos", "Denver", "Broncos", "AFC", "West"),
    "KC": NflTeamInfo("KC", "Kansas City Chiefs", "Kansas City", "Chiefs", "AFC", "West"),
    "LV": NflTeamInfo("LV", "Las Vegas Raiders", "Las Vegas", "Raiders", "AFC", "West"),
    "LAC": NflTeamInfo("LAC", "Los Angeles Chargers", "Los Angeles", "Chargers", "AFC", "West"),
    # NFC East
    "DAL": NflTeamInfo("DAL", "Dallas Cowboys", "Dallas", "Cowboys", "NFC", "East"),
    "NYG": NflTeamInfo("NYG", "New York Giants", "New York", "Giants", "NFC", "East"),
    "PHI": NflTeamInfo("PHI", "Philadelphia Eagles", "Philadelphia", "Eagles", "NFC", "East"),
    "WAS": NflTeamInfo("WAS", "Washington Commanders", "Washington", "Commanders", "NFC", "East"),
    # NFC North
    "CHI": NflTeamInfo("CHI", "Chicago Bears", "Chicago", "Bears", "NFC", "North"),
    "DET": NflTeamInfo("DET", "Detroit Lions", "Detroit", "Lions", "NFC", "North"),
    "GB": NflTeamInfo("GB", "Green Bay Packers", "Green Bay", "Packers", "NFC", "North"),
    "MIN": NflTeamInfo("MIN", "Minnesota Vikings", "Minnesota", "Vikings", "NFC", "North"),
    # NFC South
    "ATL": NflTeamInfo("ATL", "Atlanta Falcons", "Atlanta", "Falcons", "NFC", "South"),
    "CAR": NflTeamInfo("CAR", "Carolina Panthers", "Carolina", "Panthers", "NFC", "South"),
    "NO": NflTeamInfo("NO", "New Orleans Saints", "New Orleans", "Saints", "NFC", "South"),
    "TB": NflTeamInfo("TB", "Tampa Bay Buccaneers", "Tampa Bay", "Buccaneers", "NFC", "South"),
    # NFC West
    "ARI": NflTeamInfo("ARI", "Arizona Cardinals", "Arizona", "Cardinals", "NFC", "West"),
    "LAR": NflTeamInfo("LAR", "Los Angeles Rams", "Los Angeles", "Rams", "NFC", "West"),
    "SF": NflTeamInfo("SF", "San Francisco 49ers", "San Francisco", "49ers", "NFC", "West"),
    "SEA": NflTeamInfo("SEA", "Seattle Seahawks", "Seattle", "Seahawks", "NFC", "West"),
}


def _compact_key(value: Any) -> str:
    """Normalize string by removing non-alphanumeric characters and uppercasing."""
    if value is None or isinstance(value, (bool, dict, list, set, tuple)):
        return ""
    text = str(value).upper()
    return "".join(ch for ch in text if ch.isalnum())


# Comprehensive alias dictionary mapping raw names, cities, acronyms, code+nickname composites, and Outlier teamIds
NFL_TEAM_ALIASES: dict[str, str] = {
    # -------------------------------------------------------------------------
    # AFC East
    # -------------------------------------------------------------------------
    "BUF": "BUF",
    "BUFFALO": "BUF",
    "BUFFALOBILLS": "BUF",
    "BILLS": "BUF",
    "BUFBILLS": "BUF",
    "BILLSBUF": "BUF",
    "MIA": "MIA",
    "MIAMI": "MIA",
    "MIAMIDOLPHINS": "MIA",
    "DOLPHINS": "MIA",
    "MIADOLPHINS": "MIA",
    "DOLPHINSMIA": "MIA",
    "MIAPHINS": "MIA",
    "MIAMIPHINS": "MIA",
    "PHINSMIA": "MIA",
    "PHINS": "MIA",
    "NE": "NE",
    "NWE": "NE",
    "NEWENGLAND": "NE",
    "NEWENGLANDPATRIOTS": "NE",
    "PATRIOTS": "NE",
    "NEPATRIOTS": "NE",
    "PATRIOTSNE": "NE",
    "NWEPATRIOTS": "NE",
    "PATRIOTSNWE": "NE",
    "NEPATS": "NE",
    "PATSNE": "NE",
    "NWEPATS": "NE",
    "PATSNWE": "NE",
    "NEWENGLANDPATS": "NE",
    "PATS": "NE",
    "NYJ": "NYJ",
    "NEWYORKJETS": "NYJ",
    "JETS": "NYJ",
    "NYJETS": "NYJ",
    "JETSNY": "NYJ",
    "NYJJETS": "NYJ",
    "JETSNYJ": "NYJ",

    # -------------------------------------------------------------------------
    # AFC North
    # -------------------------------------------------------------------------
    "BAL": "BAL",
    "BALTIMORE": "BAL",
    "BALTIMORERAVENS": "BAL",
    "RAVENS": "BAL",
    "BALRAVENS": "BAL",
    "RAVENSBAL": "BAL",
    "CIN": "CIN",
    "CINCINNATI": "CIN",
    "CINCINNATIBENGALS": "CIN",
    "BENGALS": "CIN",
    "CINBENGALS": "CIN",
    "BENGALSCIN": "CIN",
    "CLE": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELANDBROWNS": "CLE",
    "BROWNS": "CLE",
    "CLEBROWNS": "CLE",
    "BROWNSCLE": "CLE",
    "PIT": "PIT",
    "PITTSBURGH": "PIT",
    "PITTSBURGHSTEELERS": "PIT",
    "STEELERS": "PIT",
    "PITSTEELERS": "PIT",
    "STEELERSPIT": "PIT",

    # -------------------------------------------------------------------------
    # AFC South
    # -------------------------------------------------------------------------
    "HOU": "HOU",
    "HOUSTON": "HOU",
    "HOUSTONTEXANS": "HOU",
    "TEXANS": "HOU",
    "HOUTEXANS": "HOU",
    "TEXANSHOU": "HOU",
    "IND": "IND",
    "INDIANAPOLIS": "IND",
    "INDIANAPOLISCOLTS": "IND",
    "COLTS": "IND",
    "INDCOLTS": "IND",
    "COLTSIND": "IND",
    "INDY": "IND",
    "INDYCOLTS": "IND",
    "COLTSINDY": "IND",
    "JAX": "JAX",
    "JAC": "JAX",
    "JACKSONVILLE": "JAX",
    "JACKSONVILLEJAGUARS": "JAX",
    "JAGUARS": "JAX",
    "JAXJAGUARS": "JAX",
    "JAGUARSJAX": "JAX",
    "JACJAGUARS": "JAX",
    "JAGUARSJAC": "JAX",
    "JAXJAGS": "JAX",
    "JAGSJAX": "JAX",
    "JACJAGS": "JAX",
    "JAGSJAC": "JAX",
    "JACKSONVILLEJAGS": "JAX",
    "JAGS": "JAX",
    "TEN": "TEN",
    "TENNESSEE": "TEN",
    "TENNESSEETITANS": "TEN",
    "TITANS": "TEN",
    "TENTITANS": "TEN",
    "TITANSTEN": "TEN",

    # -------------------------------------------------------------------------
    # AFC West
    # -------------------------------------------------------------------------
    "DEN": "DEN",
    "DENVER": "DEN",
    "DENVERBRONCOS": "DEN",
    "BRONCOS": "DEN",
    "DENBRONCOS": "DEN",
    "BRONCOSDEN": "DEN",
    "KC": "KC",
    "KAN": "KC",
    "KANSASCITY": "KC",
    "KANSASCITYCHIEFS": "KC",
    "CHIEFS": "KC",
    "KCCHIEFS": "KC",
    "CHIEFSKC": "KC",
    "KANCHIEFS": "KC",
    "CHIEFSKAN": "KC",
    "LV": "LV",
    "LVR": "LV",
    "LAS": "LV",
    "LASVEGAS": "LV",
    "LASVEGASRAIDERS": "LV",
    "RAIDERS": "LV",
    "OAK": "LV",
    "OAKLAND": "LV",
    "OAKLANDRAIDERS": "LV",
    "LVRAIDERS": "LV",
    "RAIDERSLV": "LV",
    "LVRRAIDERS": "LV",
    "RAIDERSLVR": "LV",
    "LASRAIDERS": "LV",
    "OAKRAIDERS": "LV",
    "LAC": "LAC",
    "LOSANGELESCHARGERS": "LAC",
    "LACHARGERS": "LAC",
    "CHARGERS": "LAC",
    "LACCHARGERS": "LAC",
    "CHARGERSLAC": "LAC",
    "CHARGERSLA": "LAC",
    "SD": "LAC",
    "SANDIEGO": "LAC",
    "SANDIEGOCHARGERS": "LAC",
    "SDCHARGERS": "LAC",
    "CHARGERSSD": "LAC",
    "LABOLTS": "LAC",
    "LACBOLTS": "LAC",
    "BOLTS": "LAC",

    # -------------------------------------------------------------------------
    # NFC East
    # -------------------------------------------------------------------------
    "DAL": "DAL",
    "DALLAS": "DAL",
    "DALLASCOWBOYS": "DAL",
    "COWBOYS": "DAL",
    "DALCOWBOYS": "DAL",
    "COWBOYSDAL": "DAL",
    "NYG": "NYG",
    "NEWYORKGIANTS": "NYG",
    "GIANTS": "NYG",
    "NYGIANTS": "NYG",
    "GIANTSNY": "NYG",
    "NYGGIANTS": "NYG",
    "GIANTSNYG": "NYG",
    "GMEN": "NYG",
    "NYGMEN": "NYG",
    "NYGGMEN": "NYG",
    "PHI": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIAEAGLES": "PHI",
    "EAGLES": "PHI",
    "PHIEAGLES": "PHI",
    "EAGLESPHI": "PHI",
    "PHILLY": "PHI",
    "PHILLYEAGLES": "PHI",
    "EAGLESPHILLY": "PHI",
    "WAS": "WAS",
    "WSH": "WAS",
    "WASHINGTON": "WAS",
    "WASHINGTONCOMMANDERS": "WAS",
    "COMMANDERS": "WAS",
    "FOOTBALLTEAM": "WAS",
    "WASHINGTONFOOTBALLTEAM": "WAS",
    "REDSKINS": "WAS",
    "WASHINGTONREDSKINS": "WAS",
    "WASCOMMANDERS": "WAS",
    "COMMANDERSWAS": "WAS",
    "WSHCOMMANDERS": "WAS",
    "COMMANDERSWSH": "WAS",
    "WFT": "WAS",
    "WASFOOTBALLTEAM": "WAS",
    "WSHFOOTBALLTEAM": "WAS",
    "WASREDSKINS": "WAS",
    "WSHREDSKINS": "WAS",

    # -------------------------------------------------------------------------
    # NFC North
    # -------------------------------------------------------------------------
    "CHI": "CHI",
    "CHICAGO": "CHI",
    "CHICAGOBEARS": "CHI",
    "BEARS": "CHI",
    "CHIBEARS": "CHI",
    "BEARSCHI": "CHI",
    "DET": "DET",
    "DETROIT": "DET",
    "DETROITLIONS": "DET",
    "LIONS": "DET",
    "DETLIONS": "DET",
    "LIONSDET": "DET",
    "GB": "GB",
    "GNB": "GB",
    "GREENBAY": "GB",
    "GREENBAYPACKERS": "GB",
    "PACKERS": "GB",
    "GBPACKERS": "GB",
    "PACKERSGB": "GB",
    "GNBPACKERS": "GB",
    "PACKERSGNB": "GB",
    "MIN": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTAVIKINGS": "MIN",
    "VIKINGS": "MIN",
    "MINVIKINGS": "MIN",
    "VIKINGSMIN": "MIN",
    "MINVIKES": "MIN",
    "VIKESMIN": "MIN",
    "MINNESOTAVIKES": "MIN",
    "VIKES": "MIN",

    # -------------------------------------------------------------------------
    # NFC South
    # -------------------------------------------------------------------------
    "ATL": "ATL",
    "ATLANTA": "ATL",
    "ATLANTAFALCONS": "ATL",
    "FALCONS": "ATL",
    "ATLFALCONS": "ATL",
    "FALCONSATL": "ATL",
    "CAR": "CAR",
    "CAROLINA": "CAR",
    "CAROLINAPANTHERS": "CAR",
    "PANTHERS": "CAR",
    "CARPANTHERS": "CAR",
    "PANTHERSCAR": "CAR",
    "NO": "NO",
    "NOR": "NO",
    "NEWORLEANS": "NO",
    "NEWORLEANSSAINTS": "NO",
    "SAINTS": "NO",
    "NOSAINTS": "NO",
    "SAINTSNO": "NO",
    "NORSAINTS": "NO",
    "SAINTSNOR": "NO",
    "NOLA": "NO",
    "NOLASAINTS": "NO",
    "SAINTSNOLA": "NO",
    "TB": "TB",
    "TAM": "TB",
    "TAMPABAY": "TB",
    "TAMPABAYBUCCANEERS": "TB",
    "BUCCANEERS": "TB",
    "BUCS": "TB",
    "TBBUCS": "TB",
    "BUCSTB": "TB",
    "TBBUCCANEERS": "TB",
    "BUCCANEERSTB": "TB",
    "TAMBUCS": "TB",
    "BUCSTAM": "TB",
    "TAMBUCCANEERS": "TB",
    "BUCCANEERSTAM": "TB",
    "TAMPABAYBUCS": "TB",
    "TAMPABUCS": "TB",
    "TAMPA": "TB",

    # -------------------------------------------------------------------------
    # NFC West
    # -------------------------------------------------------------------------
    "ARI": "ARI",
    "AZ": "ARI",
    "ARIZONA": "ARI",
    "ARIZONACARDINALS": "ARI",
    "CARDINALS": "ARI",
    "ARICARDINALS": "ARI",
    "CARDINALSARI": "ARI",
    "AZCARDINALS": "ARI",
    "CARDINALSAZ": "ARI",
    "ARICARDS": "ARI",
    "CARDSARI": "ARI",
    "AZCARDS": "ARI",
    "CARDSAZ": "ARI",
    "ARIZONACARDS": "ARI",
    "CARDS": "ARI",
    "LAR": "LAR",
    "LOSANGELESRAMS": "LAR",
    "LARAMS": "LAR",
    "RAMS": "LAR",
    "LA": "LAR",
    "LARRAMS": "LAR",
    "RAMSLAR": "LAR",
    "RAMSLA": "LAR",
    "STL": "LAR",
    "STLOUIS": "LAR",
    "STLOUISRAMS": "LAR",
    "STLRAMS": "LAR",
    "RAMSSTL": "LAR",
    "SF": "SF",
    "SFO": "SF",
    "SANFRANCISCO": "SF",
    "SANFRANCISCO49ERS": "SF",
    "49ERS": "SF",
    "NINERS": "SF",
    "SF49ERS": "SF",
    "49ERSSF": "SF",
    "SFO49ERS": "SF",
    "49ERSSFO": "SF",
    "SFNINERS": "SF",
    "NINERSSF": "SF",
    "SFONINERS": "SF",
    "NINERSSFO": "SF",
    "SANFRANCISCONINERS": "SF",
    "SEA": "SEA",
    "SEATTLE": "SEA",
    "SEATTLESEAHAWKS": "SEA",
    "SEAHAWKS": "SEA",
    "SEASEAHAWKS": "SEA",
    "SEAHAWKSSEA": "SEA",
    "SEATTLEHAWKS": "SEA",
}


def normalize_team(value: Any) -> str | None:
    """Normalize any team alias, city, nickname, or abbreviation to canonical code."""
    compact = _compact_key(value)
    if not compact:
        return None
    return NFL_TEAM_ALIASES.get(compact)


def get_team_info(team_code: str) -> NflTeamInfo | None:
    """Retrieve structured metadata for a canonical team code."""
    canonical = normalize_team(team_code)
    return NFL_TEAMS.get(canonical) if canonical else None


def get_team_display_name(team_code: str) -> str:
    """Return full display name (e.g. 'Kansas City Chiefs') or fallback to code."""
    info = get_team_info(team_code)
    return info.name if info else str(team_code)


# -----------------------------------------------------------------------------
# Market Taxonomy & Proposition Mappings
# -----------------------------------------------------------------------------

# Gameline Canonical Proposition Codes
PROP_SPREAD: str = "SPREAD"
PROP_TOTAL: str = "TOTAL"
PROP_MONEYLINE: str = "ML"
PROP_MONEYLINE_3WAY: str = "ML_3WAY"

# Team Prop Canonical Proposition Codes
PROP_TEAM_TOTAL_POINTS: str = "POINTS"
PROP_TEAM_OFF_YARDS: str = "OFF_YDS"
PROP_TEAM_PASS_YARDS: str = "PASS_YDS"
PROP_TEAM_RUSH_YARDS: str = "RUSH_YDS"

# Player Prop Canonical Proposition Codes
PROP_PASS_YARDS: str = "PASS_YDS"
PROP_PASS_TDS: str = "PASS_TD"
PROP_PASS_COMP: str = "PASS_COMP"
PROP_PASS_ATT: str = "PASS_ATT"
PROP_INT: str = "INT"
PROP_LONG_PASS: str = "LONG_PASS"
PROP_RUSH_YARDS: str = "RUSH_YDS"
PROP_RUSH_ATT: str = "RUSH_ATT"
PROP_LONG_RUSH: str = "LONG_RUSH"
PROP_REC_YARDS: str = "REC_YDS"
PROP_RECEPTIONS: str = "REC"
PROP_LONG_REC: str = "LONG_REC"
PROP_RUSH_REC_YARDS: str = "RUSH_REC_YDS"
PROP_PASS_RUSH_YARDS: str = "PASS_RUSH_YDS"
PROP_ANYTIME_TD: str = "ANYTIME_TD"
PROP_FIRST_TD: str = "FIRST_TD"
PROP_FGM: str = "FGM"
PROP_KICK_PTS: str = "KICK_PTS"
PROP_TKL_AST: str = "TKL_AST"
PROP_SACKS: str = "SACKS"

# Aliases mapping raw Outlier API proposition strings to canonical codes
NFL_MARKET_ALIASES: dict[str, str] = {
    # Spreads
    "SPREAD": PROP_SPREAD,
    "POINTSPREAD": PROP_SPREAD,
    "SPREADS": PROP_SPREAD,
    "HANDICAP": PROP_SPREAD,
    # Game Totals
    "TOTAL": PROP_TOTAL,
    "TOTALS": PROP_TOTAL,
    "TOTALPOINTS": PROP_TOTAL,
    "OVERUNDER": PROP_TOTAL,
    "GAMEPOINTS": PROP_TOTAL,
    # Moneylines
    "MONEYLINE": PROP_MONEYLINE,
    "ML": PROP_MONEYLINE,
    "MONEYLINE3WAY": PROP_MONEYLINE_3WAY,
    "3WAYMONEYLINE": PROP_MONEYLINE_3WAY,
    "THREEWAYMONEYLINE": PROP_MONEYLINE_3WAY,
    # Team Props / Team Totals
    "POINTS": PROP_TEAM_TOTAL_POINTS,
    "TEAMTOTAL": PROP_TEAM_TOTAL_POINTS,
    "TEAMTOTALS": PROP_TEAM_TOTAL_POINTS,
    "TEAMTOTALPOINTS": PROP_TEAM_TOTAL_POINTS,
    "OFFENSIVEYARDS": PROP_TEAM_OFF_YARDS,
    "TEAMOFFENSIVEYARDS": PROP_TEAM_OFF_YARDS,
    # Player Props - Passing
    "PASSINGYARDS": PROP_PASS_YARDS,
    "PASSYARDS": PROP_PASS_YARDS,
    "PASSYDS": PROP_PASS_YARDS,
    "PASSINGTOUCHDOWNS": PROP_PASS_TDS,
    "PASSINGTDS": PROP_PASS_TDS,
    "PASSTDS": PROP_PASS_TDS,
    "PASSTOUCHDOWNS": PROP_PASS_TDS,
    "PASSTDSCORE": PROP_PASS_TDS,
    "PASSCOMPLETIONS": PROP_PASS_COMP,
    "COMPLETIONS": PROP_PASS_COMP,
    "PASSINGATTEMPTS": PROP_PASS_ATT,
    "PASSATTEMPTS": PROP_PASS_ATT,
    "ATTEMPTS": PROP_PASS_ATT,
    "INTERCEPTIONS": PROP_INT,
    "PASSINTERCEPTIONS": PROP_INT,
    "PASSINGINTERCEPTIONS": PROP_INT,
    "INTS": PROP_INT,
    "LONGESTCOMPLETION": PROP_LONG_PASS,
    "LONGESTPASS": PROP_LONG_PASS,
    # Player Props - Rushing
    "RUSHINGYARDS": PROP_RUSH_YARDS,
    "RUSHYARDS": PROP_RUSH_YARDS,
    "RUSHYDS": PROP_RUSH_YARDS,
    "RUSHINGATTEMPTS": PROP_RUSH_ATT,
    "RUSHATTEMPTS": PROP_RUSH_ATT,
    "CARRIES": PROP_RUSH_ATT,
    "LONGESTRUSH": PROP_LONG_RUSH,
    "LONGESTRUSHINGATTEMPT": PROP_LONG_RUSH,
    # Player Props - Receiving
    "RECEIVINGYARDS": PROP_REC_YARDS,
    "RECYARDS": PROP_REC_YARDS,
    "RECYDS": PROP_REC_YARDS,
    "RECEPTIONS": PROP_RECEPTIONS,
    "CATCHES": PROP_RECEPTIONS,
    "REC": PROP_RECEPTIONS,
    "LONGESTRECEPTION": PROP_LONG_REC,
    "LONGESTCATCH": PROP_LONG_REC,
    # Player Props - Combos
    "RUSHINGRECEIVINGYARDS": PROP_RUSH_REC_YARDS,
    "RUSHRECYARDS": PROP_RUSH_REC_YARDS,
    "RUSHRECYDS": PROP_RUSH_REC_YARDS,
    "PASSINGRUSHINGYARDS": PROP_PASS_RUSH_YARDS,
    "PASSRUSHYARDS": PROP_PASS_RUSH_YARDS,
    # Player Props - Touchdowns / Scoring
    "ANYTIMETOUCHDOWN": PROP_ANYTIME_TD,
    "ANYTIMETD": PROP_ANYTIME_TD,
    "TOUCHDOWNS": PROP_ANYTIME_TD,
    "TDSCORER": PROP_ANYTIME_TD,
    "FIRSTTOUCHDOWN": PROP_FIRST_TD,
    "FIRSTTD": PROP_FIRST_TD,
    # Player Props - Special Teams / Defense
    "FIELDGOALSMADE": PROP_FGM,
    "FIELDGOALS": PROP_FGM,
    "FGM": PROP_FGM,
    "KICKINGPOINTS": PROP_KICK_PTS,
    "KICKERPOINTS": PROP_KICK_PTS,
    "TACKLESASSISTS": PROP_TKL_AST,
    "TOTALTACKLES": PROP_TKL_AST,
    "TACKLES": PROP_TKL_AST,
    "SACKS": PROP_SACKS,
}

# Sets for quick market identification
GAME_TOTAL_PROPOSITIONS: frozenset[str] = frozenset({
    "TOTAL",
    "TOTALS",
    "TOTALPOINTS",
    "OVERUNDER",
    "GAMEPOINTS",
})

TEAM_TOTAL_PROPOSITIONS: frozenset[str] = frozenset({
    "POINTS",
    "TOTAL",
    "TEAM_TOTAL",
    "TEAMTOTAL",
    "TOTAL_POINTS",
    "TOTALPOINTS",
})

SPREAD_PROPOSITIONS: frozenset[str] = frozenset({
    "SPREAD",
    "POINTSPREAD",
    "SPREADS",
    "HANDICAP",
})

MONEYLINE_PROPOSITIONS: frozenset[str] = frozenset({
    "MONEYLINE",
    "ML",
    "MONEYLINE3WAY",
    "3WAYMONEYLINE",
    "THREEWAYMONEYLINE",
})


def normalize_market(value: Any) -> str | None:
    """Normalize raw proposition string to canonical proposition code."""
    compact = _compact_key(value)
    if not compact:
        return None
    return NFL_MARKET_ALIASES.get(compact)


def is_game_total(prop: Any) -> bool:
    """Return True if proposition represents a game total."""
    compact = _compact_key(prop)
    return compact in GAME_TOTAL_PROPOSITIONS or normalize_market(compact) == PROP_TOTAL


def is_team_total(prop: Any) -> bool:
    """Return True if proposition represents a team total."""
    compact = _compact_key(prop)
    return compact in TEAM_TOTAL_PROPOSITIONS or normalize_market(compact) == PROP_TEAM_TOTAL_POINTS


def is_spread(prop: Any) -> bool:
    """Return True if proposition represents a point spread."""
    compact = _compact_key(prop)
    return compact in SPREAD_PROPOSITIONS or normalize_market(compact) == PROP_SPREAD


def is_moneyline(prop: Any) -> bool:
    """Return True if proposition represents a moneyline."""
    compact = _compact_key(prop)
    return compact in MONEYLINE_PROPOSITIONS or normalize_market(compact) in (
        PROP_MONEYLINE,
        PROP_MONEYLINE_3WAY,
    )


# -----------------------------------------------------------------------------
# Football Game Scope & Period Detection
# -----------------------------------------------------------------------------

SCOPE_FULL_GAME: str = "full_game"
SCOPE_FIRST_HALF: str = "first_half"
SCOPE_SECOND_HALF: str = "second_half"
SCOPE_FIRST_QUARTER: str = "first_quarter"
SCOPE_SECOND_QUARTER: str = "second_quarter"
SCOPE_THIRD_QUARTER: str = "third_quarter"
SCOPE_FOURTH_QUARTER: str = "fourth_quarter"

_PERIOD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (SCOPE_FIRST_HALF, re.compile(r"\b(1st half|first half|1h)\b", re.IGNORECASE)),
    (SCOPE_SECOND_HALF, re.compile(r"\b(2nd half|second half|2h)\b", re.IGNORECASE)),
    (SCOPE_FIRST_QUARTER, re.compile(r"\b(1st quarter|first quarter|1q)\b", re.IGNORECASE)),
    (SCOPE_SECOND_QUARTER, re.compile(r"\b(2nd quarter|second quarter|2q)\b", re.IGNORECASE)),
    (SCOPE_THIRD_QUARTER, re.compile(r"\b(3rd quarter|third quarter|3q)\b", re.IGNORECASE)),
    (SCOPE_FOURTH_QUARTER, re.compile(r"\b(4th quarter|fourth quarter|4q)\b", re.IGNORECASE)),
)


def detect_scope(period_label: str | None, market_label: str | None = None) -> str:
    """Detect whether a market applies to full game or a specific quarter/half.

    Defaults to 'full_game' if period_label is None or not matched.
    """
    for candidate in (period_label, market_label):
        if not candidate:
            continue
        text = str(candidate).strip()
        for scope_name, pattern in _PERIOD_PATTERNS:
            if pattern.search(text):
                return scope_name
    return SCOPE_FULL_GAME
