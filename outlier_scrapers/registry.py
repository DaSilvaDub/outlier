from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SportConfig:
    league_id: str
    app_route: str
    enabled_by_default: bool
    team_aliases: Mapping[str, str]
    market_aliases: Mapping[str, str]
    opp_rank_applicable: bool

    @property
    def props_route(self) -> str:
        return f"/{self.app_route}/props"

    @property
    def insights_route(self) -> str:
        return f"/{self.app_route}/trending/insights"

    @property
    def games_route(self) -> str:
        return f"/{self.app_route}/games"


def _compact(value: object) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


GAME_MARKET_TYPES = ("GAMELINE", "PLAYER_PROP", "TEAM_PROP", "GAME_PROP")


WNBA_TEAM_ALIASES = {
    "ATL": "ATL",
    "ATLANTADREAM": "ATL",
    "CHI": "CHI",
    "CHICAGOSKY": "CHI",
    "CON": "CON",
    "CONNECTICUTSUN": "CON",
    "DAL": "DAL",
    "DALLASWINGS": "DAL",
    "GSV": "GSV",
    "GOLDENSTATEVALKYRIES": "GSV",
    "IND": "IND",
    "INDIANAFEVER": "IND",
    "LAS": "LAS",
    "LOSANGELESSPARKS": "LAS",
    "LVA": "LVA",
    "LASVEGASACES": "LVA",
    "MIN": "MIN",
    "MINNESOTALYNX": "MIN",
    "NYL": "NYL",
    "NEWYORKLIBERTY": "NYL",
    "PHX": "PHX",
    "PHO": "PHX",
    "PHOENIXMERCURY": "PHX",
    "SEA": "SEA",
    "SEATTLESTORM": "SEA",
    "TOR": "TOR",
    "TORONTOTEMPO": "TOR",
    "TEMPO": "TOR",
    "WAS": "WAS",
    "WASHINGTONMYSTICS": "WAS",
}


MLB_TEAM_ALIASES = {
    token: token
    for token in (
        "ARI",
        "ATL",
        "BAL",
        "BOS",
        "CHC",
        "CIN",
        "CLE",
        "COL",
        "CWS",
        "DET",
        "HOU",
        "KC",
        "LAA",
        "LAD",
        "MIA",
        "MIL",
        "MIN",
        "NYM",
        "NYY",
        "ATH",
        "PHI",
        "PIT",
        "SD",
        "SEA",
        "SF",
        "STL",
        "TB",
        "TEX",
        "TOR",
        "WSH",
    )
}
MLB_TEAM_ALIASES.update(
    {
        "AZ": "ARI",
        "ARIZONADIAMONDBACKS": "ARI",
        "ATLANTABRAVES": "ATL",
        "BALTIMOREORIOLES": "BAL",
        "BOSTONREDSOX": "BOS",
        "CHICAGOCUBS": "CHC",
        "CHICAGOWHITESOX": "CWS",
        "CLEVELANDGUARDIANS": "CLE",
        "COLORADOROCKIES": "COL",
        "DETROITTIGERS": "DET",
        "HOUSTONASTROS": "HOU",
        "KANSASCITYROYALS": "KC",
        "LOSANGELESANGELS": "LAA",
        "LOSANGELESDODGERS": "LAD",
        "MIAMIMARLINS": "MIA",
        "MILWAUKEEBREWERS": "MIL",
        "MINNESOTATWINS": "MIN",
        "NEWYORKMETS": "NYM",
        "NEWYORKYANKEES": "NYY",
        "ATHLETICS": "ATH",
        "OAKLANDATHLETICS": "ATH",
        "PHILADELPHIAPHILLIES": "PHI",
        "PITTSBURGHPIRATES": "PIT",
        "SANDIEGOPADRES": "SD",
        "SEATTLEMARINERS": "SEA",
        "SANFRANCISCOGIANTS": "SF",
        "STLOUISCARDINALS": "STL",
        "TAMPABAYRAYS": "TB",
        "TEXASRANGERS": "TEX",
        "TORONTOBLUEJAYS": "TOR",
        "WASHINGTONNATIONALS": "WSH",
    }
)


BASKETBALL_MARKET_ALIASES = {
    "POINTS": "PTS",
    "PTS": "PTS",
    "REBOUNDS": "REB",
    "REB": "REB",
    "ASSISTS": "AST",
    "AST": "AST",
    "THREEPOINTERS": "3PTS",
    "THREE_POINTERS": "3PTS",
    "3POINTERS": "3PTS",
    "POINTSREBOUNDS": "PR",
    "POINTS_REBOUNDS": "PR",
    "POINTSASSISTS": "PA",
    "POINTS_ASSISTS": "PA",
    "REBOUNDSASSISTS": "RA",
    "REBOUNDS_ASSISTS": "RA",
    "POINTSREBOUNDSASSISTS": "PRA",
    "POINTS_REBOUNDS_ASSISTS": "PRA",
    "STEALSBLOCKS": "S+B",
    "STEALS_BLOCKS": "S+B",
    "TURNOVERS": "TO",
    "FANTASYSCORE": "FANTASY",
    "FANTASYSCOREPP": "FANTASY_PP",
    "FANTASYSCOREUD": "FANTASY_UD",
    # Keys are matched after _compact() (uppercased, alnum-only), so the
    # canonical lookup keys carry no separators. Proposition strings are tried
    # first by the normalizer; the "*ATT" variants cover the market_raw fallback
    # labels ("Three Pointers Att", etc.).
    "STEALS": "STL",
    "BLOCKS": "BLK",
    "MADEFIELDGOALS": "FGM",
    "FIELDGOALSATTEMPTED": "FGA",
    "FIELDGOALSATT": "FGA",
    "TWOPOINTERS": "2PTS",
    "TWOPOINTERSATTEMPTED": "2PA",
    "TWOPOINTERSATT": "2PA",
    "THREEPOINTERSATTEMPTED": "3PA",
    "THREEPOINTERSATT": "3PA",
    "FREETHROWS": "FTM",
    "FREETHROWSATTEMPTED": "FTA",
    "FREETHROWSATT": "FTA",
    "OFFENSIVEREBOUNDS": "OREB",
    "DEFENSIVEREBOUNDS": "DREB",
    "DOUBLEDOUBLE": "DD",
    "TRIPLEDOUBLE": "TD",
    "MONEYLINE": "ML",
    "SPREAD": "SPREAD",
    "TOTAL": "TOTAL",
    "MONEYLINE_THREE_WAY": "ML_3WAY",
    "MONEYLINETHREEWAY": "ML_3WAY",
    "WINNING_MARGIN": "MARGIN",
    "WINNINGMARGIN": "MARGIN",
}


MLB_MARKET_ALIASES = {
    # Outlier sends proposition "BASES" (label "Bases") for total bases; there
    # is no TOTAL_BASES proposition in the live feed.
    "BASES": "TB",
    "HITS": "H",
    "HIT": "H",
    "RUNS": "R",
    "RBI": "RBI",
    "RBIS": "RBI",
    "HOMERUNS": "HR",
    "HOME_RUNS": "HR",
    "HOME_RUN": "HR",
    "STOLENBASES": "SB",
    "STOLEN_BASES": "SB",
    "SINGLES": "1B",
    "DOUBLES": "2B",
    "TRIPLES": "3B",
    "STRIKEOUTS": "SO",
    "PITCHERSTRIKEOUTS": "SO",
    "PITCHER_STRIKEOUTS": "SO",
    "OUTS": "OUTS",
    "PITCHINGOUTS": "OUTS",
    "PITCHING_OUTS": "OUTS",
    "EARNEDRUNS": "ER",
    "EARNED_RUNS": "ER",
    "EARNEDRUNSALLOWED": "ER",
    "HITSALLOWED": "HA",
    "HITS_ALLOWED": "HA",
    "WALKS": "BB",
    # Full-game additions confirmed from the live feed (2026-06-20). Scoped
    # variants (1st inning, etc.) are gated to market=None by the normalizer,
    # so these only apply to full-game markets.
    "BATTERSTRIKEOUTS": "BSO",
    "HITSRUNSRBIS": "HRR",
    "RUNSRBIS": "RR",
    "HITSSTOLENBASES": "HSB",
    "HITSRUNSSTOLENBASES": "HRSB",
    "HITSWALKSSTOLENBASES": "HWSB",
    "HITSWALKSEARNEDRUNSALLOWED": "HWER",
    "EXTRABASEHITS": "XBH",
    "WALKSALLOWED": "BBA",
    "PITCHESTHROWN": "PT",
    "BATTERSFACED": "BF",
    "FANTASYSCORE": "FANTASY",
    "FANTASYSCOREPP": "FANTASY_PP",
    "FANTASYSCOREUD": "FANTASY_UD",
    "MONEYLINE": "ML",
    "SPREAD": "SPREAD",
    "TOTAL": "TOTAL",
    "MONEYLINE_THREE_WAY": "ML_3WAY",
    "MONEYLINETHREEWAY": "ML_3WAY",
    "WINNING_MARGIN": "MARGIN",
    "WINNINGMARGIN": "MARGIN",
}


SPORTS: dict[str, SportConfig] = {
    "MLB": SportConfig(
        league_id="MLB",
        app_route="MLB",
        enabled_by_default=True,
        team_aliases=MLB_TEAM_ALIASES,
        market_aliases=MLB_MARKET_ALIASES,
        opp_rank_applicable=False,
    ),
    "WNBA": SportConfig(
        league_id="WNBA",
        app_route="WNBA",
        enabled_by_default=True,
        team_aliases=WNBA_TEAM_ALIASES,
        market_aliases=BASKETBALL_MARKET_ALIASES,
        # The playerProps API does not return opponent rank for any league
        # (confirmed via discovery 2026-06-20). Treat as not-applicable for the
        # API-only V1; revisit if a d-rank endpoint is found.
        opp_rank_applicable=False,
    ),
    "NBA": SportConfig(
        league_id="NBA",
        app_route="NBA",
        enabled_by_default=False,
        team_aliases={},
        market_aliases=BASKETBALL_MARKET_ALIASES,
        opp_rank_applicable=True,
    ),
}


def supported_leagues(include_disabled: bool = False) -> list[str]:
    return [
        league
        for league, config in SPORTS.items()
        if include_disabled or config.enabled_by_default
    ]


def get_sport_config(league: str, *, allow_disabled: bool = False) -> SportConfig:
    token = league.strip().upper()
    try:
        config = SPORTS[token]
    except KeyError as exc:
        supported = ", ".join(supported_leagues(include_disabled=allow_disabled))
        raise ValueError(f"Unsupported league {league!r}. Supported: {supported}") from exc
    if not allow_disabled and not config.enabled_by_default:
        raise ValueError(f"{token} is disabled by default in this standalone V1 project")
    return config


def normalize_team(config: SportConfig, value: object) -> str | None:
    """Return the canonical alias for a team, or None when it is unknown.

    A miss returns None on purpose: callers keep the original under ``team_raw``.
    Passing unknown tokens through would let a non-canonical value masquerade as
    canonical, so ``team`` is only ever a value present in ``team_aliases``.
    """
    token = _compact(value)
    if not token:
        return None
    return config.team_aliases.get(token)


def normalize_market(config: SportConfig, value: object) -> str | None:
    token = _compact(value)
    if not token:
        return None
    return config.market_aliases.get(token)
