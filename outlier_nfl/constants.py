"""Centralized constants for the standalone outlier_nfl pipeline.

This module defines API endpoints, retry policies, pagination limits, and
market types for NFL betting data ingestion. Completely decoupled from
outlier_scrapers.
"""

from __future__ import annotations

# Outlier API Base Configuration
API_BASE_URL: str = "https://api.outlier.bet"
APP_ORIGIN: str = "https://app.outlier.bet"
LEAGUE_TOKEN: str = "NFL"
SPORT_NAME: str = "football"

# HTTP Transport & Retry Policy Settings
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({403, 429, 500, 502, 503, 504})
MAX_RETRIES: int = 5
BASE_DELAY_SECONDS: float = 0.5
MAX_DELAY_SECONDS: float = 8.0
REQUEST_TIMEOUT_SECONDS: int = 60

# Pagination Controls
PAGINATION_MAX_PAGES: int = 60
TOKEN_PARAM_CANDIDATES: tuple[str, ...] = ("pageToken", "nextPageToken", "page_token")
NUMBER_PARAM_CANDIDATES: tuple[str, ...] = ("pageNumber", "page", "pageNo", "page_number")

# Outlier Event Market Types for NFL
MARKET_TYPE_GAMELINE: str = "GAMELINE"
MARKET_TYPE_TEAM_PROP: str = "TEAM_PROP"
MARKET_TYPE_PLAYER_PROP: str = "PLAYER_PROP"
MARKET_TYPE_GAME_PROP: str = "GAME_PROP"

NFL_GAME_MARKET_TYPES: tuple[str, ...] = (
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_TEAM_PROP,
    MARKET_TYPE_PLAYER_PROP,
)

# Canonical API Endpoint Routes
SCHEDULE_ENDPOINT: str = f"/sportsdata/leagues/{LEAGUE_TOKEN}/schedule"
PLAYER_PROPS_ENDPOINT: str = f"/sportsdata/leagues/{LEAGUE_TOKEN}/playerProps"
EVENT_MARKETS_ENDPOINT: str = "/sportsdata/events/{event_id}/markets"
EVENT_MATCHUP_ENDPOINT: str = "/sportsdata/events/{event_id}/matchup"
EVENT_INSIGHTS_ENDPOINT: str = "/sportsdata/events/{event_id}/insights"
TEAM_INJURIES_ENDPOINT: str = f"/sportsdata/leagues/{LEAGUE_TOKEN}/teams/{{team_id}}/injuries"
EVENT_METADATA_ENDPOINT: str = "/sportsdata/events/{event_id}"
MARKET_DETAIL_ENDPOINT: str = "/sportsdata/markets/{market_id}"

# Timezone for NFL game slates (night games kick off across midnight UTC)
SLATE_TIMEZONE: str = "America/New_York"

# Data Storage Layout Defaults
DEFAULT_DATA_DIR: str = "data/NFL"
RAW_DATA_SUBDIR: str = "raw"
NORMALIZED_DATA_SUBDIR: str = "normalized"
REPORTS_DATA_SUBDIR: str = "reports"
