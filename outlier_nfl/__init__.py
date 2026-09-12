"""Standalone NFL betting data pipeline package.

This package provides independent NFL data ingestion, API interaction,
taxonomy normalization, and extraction for game totals, spreads, team totals,
and player props. It is completely decoupled from MLB and WNBA code paths.
"""

from __future__ import annotations

from outlier_nfl.api import (
    AuthRequiredError,
    NotFoundError,
    OutlierNflApiClient,
    OutlierNflApiError,
    RateLimitError,
    RetryPolicy,
)
from outlier_nfl.config import (
    NFL_TEAMS,
    NflTeamInfo,
    detect_scope,
    get_team_display_name,
    get_team_info,
    is_game_total,
    is_moneyline,
    is_spread,
    is_team_total,
    normalize_market,
    normalize_team,
)
from outlier_nfl.constants import (
    API_BASE_URL,
    APP_ORIGIN,
    LEAGUE_TOKEN,
    MARKET_TYPE_GAME_PROP,
    MARKET_TYPE_GAMELINE,
    MARKET_TYPE_PLAYER_PROP,
    MARKET_TYPE_TEAM_PROP,
    NFL_GAME_MARKET_TYPES,
    SPORT_NAME,
)
from outlier_nfl.models import (
    BookPrice,
    NflEvent,
    NflExtractionSummary,
    NflGameLine,
    NflPlayerProp,
)
from outlier_nfl.schema import (
    validate_event_markets_payload,
    validate_game_line_record,
    validate_normalized_dataset,
    validate_player_prop_record,
    validate_player_props_payload,
    validate_schedule_payload,
)
from outlier_nfl.utils import (
    format_signed_line,
    parse_iso_datetime,
    safe_read_json,
    safe_write_json,
    to_eastern_date,
)

__version__: str = "0.1.0"

__all__: list[str] = [
    "__version__",
    # Constants
    "API_BASE_URL",
    "APP_ORIGIN",
    "LEAGUE_TOKEN",
    "SPORT_NAME",
    "MARKET_TYPE_GAMELINE",
    "MARKET_TYPE_TEAM_PROP",
    "MARKET_TYPE_PLAYER_PROP",
    "MARKET_TYPE_GAME_PROP",
    "NFL_GAME_MARKET_TYPES",
    # Config & Registry
    "NFL_TEAMS",
    "NflTeamInfo",
    "normalize_team",
    "get_team_info",
    "get_team_display_name",
    "normalize_market",
    "is_game_total",
    "is_team_total",
    "is_spread",
    "is_moneyline",
    "detect_scope",
    # Models
    "BookPrice",
    "NflGameLine",
    "NflPlayerProp",
    "NflEvent",
    "NflExtractionSummary",
    # API Client
    "OutlierNflApiClient",
    "OutlierNflApiError",
    "AuthRequiredError",
    "RateLimitError",
    "NotFoundError",
    "RetryPolicy",
    # Schema
    "validate_schedule_payload",
    "validate_event_markets_payload",
    "validate_player_props_payload",
    "validate_game_line_record",
    "validate_player_prop_record",
    "validate_normalized_dataset",
    # Utilities
    "safe_write_json",
    "safe_read_json",
    "parse_iso_datetime",
    "to_eastern_date",
    "format_signed_line",
]
