# Outlier NFL Data Sourcing, Endpoints, and API Architecture Survey

**Date**: 2026-09-12  
**Author**: Explorer 2 (`teamwork_preview_explorer_survey_2`)  
**Target Module**: Standalone NFL Pipeline (`outlier_nfl`)  
**Target Repository**: `C:\Users\dasil\Dev\GitHub\outlier`

---

## Executive Summary

This survey establishes the complete technical specification for sourcing NFL betting data via the Outlier API ecosystem. Based on verified in-repo evidence, historical probe records (`docs/plans/2026-08-31-ncaaf-analytics-pipeline.md`), and existing pipeline scrapers (`outlier_scrapers/api.py`, `games.py`, `props.py`, `normalizer.py`), the NFL integration can be built cleanly as a standalone package (`outlier_nfl`) without modifying any MLB or WNBA code paths.

Key conclusions:
1. **League Identifier Token**: The authoritative Outlier token for NFL is **`NFL`** (e.g. `GET /sportsdata/leagues/NFL/schedule`). Probes confirmed it resolves cleanly (returning 260+ pregame events), unlike college football which required `NCAAFB`.
2. **Endpoints Architecture**: Outlier exposes two primary sourcing paths:
   - **Game-level fan-out**: `GET /sportsdata/events/{eventId}/markets?marketType={marketType}` (`GAMELINE`, `TEAM_PROP`, `PLAYER_PROP`).
   - **League-level bulk feed**: `GET /sportsdata/leagues/NFL/playerProps` (paginated bulk feed for all active NFL player props).
3. **Target Markets Extraction**:
   - **Game Totals**: `GAMELINE` with proposition `TOTAL`, sides `OVER`/`UNDER`.
   - **Team Totals**: `TEAM_PROP` with proposition `POINTS` (or `TOTAL`/`TEAM_TOTAL`), sides `OVER`/`UNDER`, mapped to home/away teams.
   - **Spreads**: `GAMELINE` with proposition `SPREAD`, positions `HOME`/`AWAY`, signed handicap lines.
   - **Player Props**: `PLAYER_PROP` across standard football categories (Passing Yards, Passing TDs, Rushing Yards, Receiving Yards, Receptions, etc.).
4. **Critical Gotchas**:
   - `marketType` is a **mandatory query parameter** on `/sportsdata/events/{eventId}/markets`; omitting it returns 0 markets.
   - Date scoping must use **US Eastern calendar date** (`America/New_York`), not raw UTC, because late-night Thursday/Sunday/Monday football games kick off across midnight UTC, leading to slate date misalignment.
   - In player props payloads, `outcome.books` is **not** parallel to `outcome.odds`; per-book odds must be extracted from the inner `book` field within each odds entry.
   - Control characters in Outlier JSON strings require `json.loads(..., strict=False)`.

---

## 1. Outlier API Client Architecture

### 1.1 Base Configuration & Transport
* **Base API URL**: `https://api.outlier.bet`
* **App Origin**: `https://app.outlier.bet`
* **Transport Protocol**: HTTP/1.1 with Gzip decompression support (checks magic header `\x1f\x8b`).
* **Parser Strictness**: Must pass `strict=False` to `json.loads()` because the upstream Outlier API intermittently returns unescaped control characters in string fields.

### 1.2 Authentication & Session Flow
* **Auth Scheme**: `Authorization: Bearer <token>`
* **Session Storage**: Managed by `outlier_scrapers/auth.py` via `config/.outlier_session/`:
  - `storage_state.json`: Playwright browser storage state containing cookies and localStorage entries.
  - `api_bearer_token.txt`: Standalone bearer token fallback.
  - `api_request_headers.json`: Captured browser request headers.
* **Standard Request Headers**:
  ```http
  Host: api.outlier.bet
  Accept: application/json
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36
  Origin: https://app.outlier.bet
  Referer: https://app.outlier.bet/
  Authorization: Bearer <jwt_or_bearer_token>
  Cookie: <session_cookies>
  ```

### 1.3 Retry Policy & Error Classification
* **Retry Policy** (`RetryPolicy` in `outlier_scrapers/api.py`):
  - Exponential backoff with +/-50% jitter: `delay = min(max_delay, base_delay * (2 ** (attempt - 1))) * (0.5 + rng() / 2)`
  - Default: `base_delay_seconds = 0.5`, `max_delay_seconds = 8.0`, `max_retries = 5`, `timeout_seconds = 60`.
* **Status Code Behavior**:
  - `401 Unauthorized`: Non-retryable. Raises `AuthRequiredError`.
  - `403 Forbidden`: **Retryable**. Outlier frequently throws transient rate-limit or burst-protection 403s on rapid market requests.
  - `429 Too Many Requests`: Retryable.
  - `500, 502, 503, 504`: Retryable server/gateway errors. (Note: 502 also occurs on invalid league tokens like `NCAAF`).
* **Error Redaction**: All logged HTTP exceptions must use `_safe_http_error_message()` (summarizing status, URL, body length, and structural shape without printing raw personal or betting data).

### 1.4 Pagination Mechanism
For endpoints returning `props` or `insights`, Outlier uses cursor-based pagination:
* **Payload Structure**:
  - Nested: `payload["_page"]["nextPageToken"]`, `payload["_page"]["pages"]`, `payload["_page"]["total"]`, `payload["_page"]["pageNumber"]`.
  - Top-level fallback: `payload["nextPageToken"]`.
* **Query Parameter Probing**:
  - Token parameter candidates: `("pageToken", "nextPageToken", "page_token")`.
  - Page number candidates: `("pageNumber", "page", "pageNo", "page_number")`.
* **Guard against Infinite Loops**:
  - Page fingerprinting (`_records_signature` using length, first 3 IDs, and last 3 IDs).
  - Stops immediately upon encountering a duplicate token, identical page signature, exhaustion, or reaching `MAX_PROP_PAGES` (default 60).

---

## 2. NFL Endpoints & Query Specifications

### 2.1 League & Event Identifiers
* **League Token**: `NFL`
* **Sport Name**: `football`
* **App Routes**:
  - Games: `/nfl/games`
  - Props: `/nfl/props`
  - Insights: `/nfl/trending/insights`

### 2.2 Endpoint Catalog

#### Endpoint 1: League Schedule (Slate Events)
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/leagues/NFL/schedule`
* **Purpose**: Retrieves all pregame, scheduled, live, and recent final events for the league.
* **Query Parameters**: None.
* **Sample Response**:
  ```json
  {
    "events": [
      {
        "eventId": "nfl-event-2026-w1-kc-bal",
        "id": "nfl-event-2026-w1-kc-bal",
        "scheduledTime": "2026-09-13T17:00:00+00:00",
        "startTime": "2026-09-13T17:00:00+00:00",
        "status": "scheduled",
        "season": 2026,
        "week": 1,
        "venue": "Arrowhead Stadium",
        "network": "CBS",
        "dayOfWeek": 6,
        "home": {
          "teamId": "kc-chiefs",
          "name": "Kansas City Chiefs",
          "alias": "KC",
          "market": "Kansas City"
        },
        "away": {
          "teamId": "bal-ravens",
          "name": "Baltimore Ravens",
          "alias": "BAL",
          "market": "Baltimore"
        }
      }
    ]
  }
  ```

#### Endpoint 2: Event Markets (Game Lines, Team Props, Game Props)
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/events/{eventId}/markets?marketType={marketType}`
* **Query Parameters**:
  - `marketType` (**REQUIRED**): Must be one of:
    - `GAMELINE`: Moneylines, spreads, totals, margin.
    - `TEAM_PROP`: Team totals, team scoring props.
    - `PLAYER_PROP`: Event-scoped player props.
    - `GAME_PROP`: Game-level proposition bets.
* **Sample Response (`marketType=GAMELINE`)**:
  ```json
  {
    "markets": [
      {
        "marketId": "m-spread-kc-bal",
        "eventId": "nfl-event-2026-w1-kc-bal",
        "marketType": "GAMELINE",
        "proposition": "SPREAD",
        "label": "Point Spread",
        "periodLabel": null,
        "includeOvertime": true,
        "books": ["DRAFTKINGS", "FANDUEL", "BETMGM", "CAESARS"],
        "publicMoney": [
          {"position": "HOME", "percentage": 55, "money": 68},
          {"position": "AWAY", "percentage": 45, "money": 32}
        ],
        "outcomes": [
          {
            "outcomeId": "o-spread-kc",
            "position": "HOME",
            "line": -3.5,
            "odds": [
              {"book": "DRAFTKINGS", "american": -110, "decimal": 1.91},
              {"book": "FANDUEL", "american": -108, "decimal": 1.93}
            ],
            "stats": {"curSeason": 0.65, "l5": 0.80, "l10": 0.70, "h2h": 0.60}
          },
          {
            "outcomeId": "o-spread-bal",
            "position": "AWAY",
            "line": 3.5,
            "odds": [
              {"book": "DRAFTKINGS", "american": -110, "decimal": 1.91},
              {"book": "FANDUEL", "american": -112, "decimal": 1.89}
            ],
            "stats": {"curSeason": 0.60, "l5": 0.60, "l10": 0.60, "h2h": 0.40}
          }
        ]
      },
      {
        "marketId": "m-tot-kc-bal",
        "eventId": "nfl-event-2026-w1-kc-bal",
        "marketType": "GAMELINE",
        "proposition": "TOTAL",
        "label": "Total Points",
        "periodLabel": null,
        "outcomes": [
          {
            "outcomeId": "o-tot-over",
            "position": "OVER",
            "line": 47.5,
            "odds": [{"book": "DRAFTKINGS", "american": -110, "decimal": 1.91}]
          },
          {
            "outcomeId": "o-tot-under",
            "position": "UNDER",
            "line": 47.5,
            "odds": [{"book": "DRAFTKINGS", "american": -110, "decimal": 1.91}]
          }
        ]
      }
    ]
  }
  ```

#### Endpoint 3: Event Markets (`marketType=TEAM_PROP`)
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/events/{eventId}/markets?marketType=TEAM_PROP`
* **Sample Response**:
  ```json
  {
    "markets": [
      {
        "marketId": "m-tt-kc",
        "eventId": "nfl-event-2026-w1-kc-bal",
        "marketType": "TEAM_PROP",
        "proposition": "POINTS",
        "label": "Kansas City Chiefs - Total Points",
        "periodLabel": null,
        "outcomes": [
          {
            "outcomeId": "o-tt-kc-over",
            "position": "OVER",
            "line": 25.5,
            "teamId": "kc-chiefs",
            "odds": [{"book": "DRAFTKINGS", "american": -115, "decimal": 1.87}]
          },
          {
            "outcomeId": "o-tt-kc-under",
            "position": "UNDER",
            "line": 25.5,
            "teamId": "kc-chiefs",
            "odds": [{"book": "DRAFTKINGS", "american": -105, "decimal": 1.95}]
          }
        ]
      }
    ]
  }
  ```

#### Endpoint 4: Bulk Player Props (League-Wide)
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/leagues/NFL/playerProps`
* **Query Parameters**:
  - `pageToken` or `nextPageToken`: Cursor string for pagination.
* **Sample Response**:
  ```json
  {
    "props": [
      {
        "outcome": {
          "active": true,
          "eventId": "nfl-event-2026-w1-kc-bal",
          "marketId": "m-prop-mahomes-py",
          "outcomeId": "o-mahomes-py-265-5-o",
          "marketLabel": "Patrick Mahomes - Passing Yards",
          "proposition": "PASSING_YARDS",
          "position": "OVER",
          "line": 265.5,
          "teamId": "kc-chiefs",
          "bestOdds": -115,
          "books": ["DRAFTKINGS", "FANDUEL", "BETMGM"],
          "bookOdds": {
            "DRAFTKINGS": {"odds": -115},
            "FANDUEL": {"odds": -110},
            "BETMGM": {"odds": -115}
          }
        },
        "stats": {
          "l5": 0.80,
          "l10": 0.70,
          "l20": 0.65,
          "h2h": 0.75,
          "curSeason": 0.68,
          "prevSeason": 0.62,
          "oppRank": 14
        }
      }
    ],
    "_page": {
      "nextPageToken": "cursor_token_xyz",
      "pageNumber": 1,
      "pages": 12,
      "total": 550
    }
  }
  ```

#### Endpoint 5: Single Event Metadata
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/events/{eventId}`
* **Purpose**: Fetches metadata for an event when schedule omission occurs.

#### Endpoint 6: Event Matchup & Lineups
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/events/{eventId}/matchup`
* **Returns**: `{"matchup_type": "regular", "lineups": {"home": {...}, "away": {...}}}`.

#### Endpoint 7: Event Insights
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/events/{eventId}/insights`
* **Returns**: Paginated list of game-specific historical performance insights and betting trends.

#### Endpoint 8: Team Injuries
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/leagues/NFL/teams/{teamId}/injuries`
* **Returns**: `{"players": [{"playerId": ..., "name": ..., "position": ..., "injury": {"status": "Questionable", "injury": "Ankle", "headline": ...}}]}`.

#### Endpoint 9: Market Detail & Line Movement
* **Method**: `GET`
* **URL**: `https://api.outlier.bet/sportsdata/markets/{marketId}`
* **Returns**: Detailed market object with `evOutcomes` (positive EV calculations) and historical book odds price changes.

---

## 3. Market Coverage & Normalization Taxonomy

### 3.1 Target Markets (R3 Specification)

| Market Category | Outlier `marketType` | Outlier `proposition` | Allowed Positions / Sides | Line Format | Scope |
|---|---|---|---|---|---|
| **Game Total** | `GAMELINE` | `TOTAL` | `OVER`, `UNDER` | Numeric (e.g. `47.5`) | `full_game` |
| **Team Total** | `TEAM_PROP` | `POINTS`, `TOTAL`, `TEAM_TOTAL` | `OVER`, `UNDER` | Numeric (e.g. `24.5`) | `full_game` |
| **Spread** | `GAMELINE` | `SPREAD` | `HOME`, `AWAY` | Signed float (e.g. `-3.5`, `+3.5`) | `full_game` |
| **Moneyline** | `GAMELINE` | `MONEYLINE` | `HOME`, `AWAY` | None (odds only) | `full_game` |
| **Player Props** | `PLAYER_PROP` | Standard Football Propositions (see below) | `OVER`, `UNDER` (or `YES`/`NO`) | Numeric threshold | `full_game` |

### 3.2 NFL Player Prop Proposition Mapping

| Category | Outlier `proposition` Token | Normalized Code | Example Line |
|---|---|---|---|
| Passing Yards | `PASSING_YARDS`, `PASS_YDS` | `PY` | 265.5 |
| Passing Touchdowns | `PASSING_TDS`, `PASSING_TOUCHDOWNS` | `PTD` | 1.5 |
| Pass Completions | `PASS_COMPLETIONS`, `COMPLETIONS` | `PC` | 22.5 |
| Pass Attempts | `PASSING_ATTEMPTS`, `PASS_ATTEMPTS` | `PA` | 34.5 |
| Interceptions | `INTERCEPTIONS` | `INT` | 0.5 |
| Rushing Yards | `RUSHING_YARDS`, `RUSH_YDS` | `RUY` | 65.5 |
| Rushing Attempts | `RUSHING_ATTEMPTS`, `RUSH_ATTEMPTS` | `RUA` | 14.5 |
| Receiving Yards | `RECEIVING_YARDS`, `REC_YDS` | `REY` | 55.5 |
| Receptions | `RECEPTIONS` | `REC` | 4.5 |
| Anytime Touchdown | `ANYTIME_TOUCHDOWN`, `TOUCHDOWNS` | `ATTD` | 0.5 |
| Kicking Points | `KICKING_POINTS` | `KP` | 7.5 |
| Field Goals Made | `FIELD_GOALS_MADE`, `FIELD_GOALS` | `FGM` | 1.5 |
| Tackles + Assists | `TACKLES_ASSISTS` | `TKL_AST` | 6.5 |
| Sacks | `SACKS` | `SACK` | 0.5 |

### 3.3 NFL Team Aliases & Canonical Codes (32 Teams)
A comprehensive alias dictionary must map Outlier's variations (city, nickname, acronyms) to 32 canonical NFL codes:

| Division | Teams & Canonical Codes |
|---|---|
| **AFC East** | `BUF` (Buffalo Bills), `MIA` (Miami Dolphins), `NE` (New England Patriots), `NYJ` (New York Jets) |
| **AFC North** | `BAL` (Baltimore Ravens), `CIN` (Cincinnati Bengals), `CLE` (Cleveland Browns), `PIT` (Pittsburgh Steelers) |
| **AFC South** | `HOU` (Houston Texans), `IND` (Indianapolis Colts), `JAX` (Jacksonville Jaguars), `TEN` (Tennessee Titans) |
| **AFC West** | `DEN` (Denver Broncos), `KC` (Kansas City Chiefs), `LV` (Las Vegas Raiders), `LAC` (Los Angeles Chargers) |
| **NFC East** | `DAL` (Dallas Cowboys), `NYG` (New York Giants), `PHI` (Philadelphia Eagles), `WAS` (Washington Commanders) |
| **NFC North** | `CHI` (Chicago Bears), `DET` (Detroit Lions), `GB` (Green Bay Packers), `MIN` (Minnesota Vikings) |
| **NFC South** | `ATL` (Atlanta Falcons), `CAR` (Carolina Panthers), `NO` (New Orleans Saints), `TB` (Tampa Bay Buccaneers) |
| **NFC West** | `ARI` (Arizona Cardinals), `LAR` (Los Angeles Rams), `SF` (San Francisco 49ers), `SEA` (Seattle Seahawks) |

---

## 4. Architectural Separation: The `outlier_nfl` Package

To satisfy Requirements R1 and R2 while leaving existing MLB and WNBA pipelines intact:

```
outlier/
├── outlier_nfl/                      # NEW STANDALONE PACKAGE
│   ├── __init__.py
│   ├── api.py                        # NFL-specific Outlier API client
│   ├── registry.py                   # NFL teams, markets, propositions, and configs
│   ├── paths.py                      # Data paths under data/NFL/...
│   ├── normalizer.py                 # Normalizes NFL games (totals, spreads) & props
│   ├── extractor.py                  # Slate & market extraction logic
│   └── verify.py                     # Verification harness
├── data/
│   ├── NFL/                          # Isolated data directory
│   │   ├── raw/                      # nfl_games_raw_*.json, nfl_props_raw_*.json
│   │   ├── normalized/               # nfl_games_latest.json, nfl_props_latest.json
│   │   └── reports/                  # nfl_extraction_status_latest.json
├── tests/
│   ├── fixtures/
│   │   ├── nfl_schedule.json         # Mock NFL slate
│   │   ├── nfl_game_markets.json     # Mock totals, spreads, team totals
│   │   └── nfl_player_props.json     # Mock passing, rushing, receiving props
│   └── test_nfl_pipeline.py          # Unit test suite for outlier_nfl
└── verify_nfl_pipeline.py            # Root-level end-to-end verification script
```

### Module Responsibilities:
1. **`outlier_nfl.api`**:
   - Specializes API calls for `league="NFL"`.
   - Embeds the standard retry policy, bearer auth, gzip decoding, and control-character-safe JSON parsing.
   - Provides methods: `fetch_schedule()`, `fetch_event_markets(event_id, market_type)`, `fetch_player_props(max_pages)`.
2. **`outlier_nfl.registry`**:
   - Defines `NFL_SPORT_CONFIG` with 32 NFL team aliases and football market aliases.
   - Whitelists game totals, team totals, spreads, and valid player prop families.
3. **`outlier_nfl.normalizer`**:
   - `normalize_games()`: Emits normalized records for totals, spreads, and team totals, extracting per-book odds from `odds[]`.
   - `normalize_player_props()`: Parses player names, stat propositions, lines, and book odds from the bulk feed.
4. **`outlier_nfl.extractor`**:
   - Orchestrates the daily extraction: queries schedule -> scopes games by date -> queries game markets -> queries props -> writes raw & normalized artifacts.
5. **`verify_nfl_pipeline.py`**:
   - Top-level CLI script. Supports replay/mock mode for CI/offline verification and live mode when auth is present.
   - Asserts the final normalized data structures contain valid game totals, team totals, spreads, and player props.

---

## 5. Implementation Traps & Mitigation Checklist

| # | Trap / Pitfall | Root Cause | Mitigation in `outlier_nfl` |
|---|---|---|---|
| **1** | **Empty Markets on `/events/{id}/markets`** | Omitting `marketType` returns 0 markets. | Always pass `marketType={market_type}` in URL query. Loop over `("GAMELINE", "TEAM_PROP", "PLAYER_PROP")`. |
| **2** | **Slate Misalignment via UTC Dates** | NFL night games kick off at 00:00–01:15 UTC next day. | Convert `scheduledTime` to US Eastern calendar date (`America/New_York`) before slate grouping. |
| **3** | **Book Alignment in Props** | `outcome.books` is not parallel to `outcome.bookOdds`. | Always iterate through `outcome.bookOdds` keys or inspect the inner `book` field within each odds entry. |
| **4** | **Unescaped JSON Control Chars** | Upstream API raw strings contain unescaped tabs/newlines. | Always parse API responses using `json.loads(text, strict=False)`. |
| **5** | **Memory Exhaustion on Serializing** | Calling `json.dumps()` then `write_text()` loads entire JSON string into RAM. | Use file streaming with `json.dump(data, f, indent=2)` in all file writers. |
| **6** | **Multiple Rows per Total/Spread Line** | Books offer alternate lines; each line can be a distinct market row. | Deduplicate and group by `(event_id, market_type, proposition, line, position)`. |
| **7** | **Transient 403 on Event Markets** | Rapid bursts of event-market requests trigger short-lived 403s. | Treat 403 as retryable in `RetryPolicy`; apply exponential backoff with jitter. |
| **8** | **Cross-Contamination with MLB/WNBA** | Shared mutable dictionaries or modifying `outlier_scrapers/`. | Isolate NFL logic completely in `outlier_nfl/`. Keep all MLB/WNBA rules and scrapers untouched. |

---

## 6. Verification & Test Plan

1. **Unit Test Suite (`tests/test_nfl_pipeline.py`)**:
   - Uses pre-recorded fixtures in `tests/fixtures/` (`nfl_schedule.json`, `nfl_game_markets.json`, `nfl_player_props.json`).
   - Verifies schedule parsing and US Eastern date resolution.
   - Verifies game total extraction (`OVER`/`UNDER` rows, consensus odds, line).
   - Verifies spread extraction (`HOME`/`AWAY` rows, signed lines like `-3.5`/`+3.5`).
   - Verifies team total extraction (team matching from label or ID, line).
   - Verifies player prop extraction (passing, rushing, receiving, tackles).
   - Verifies retry policy on 403/502 and graceful handling of missing events.
2. **End-to-End Verification Script (`verify_nfl_pipeline.py`)**:
   - Executes the extraction pipeline end-to-end.
   - In offline mode (`--replay` or default test mode), loads mock data and executes full normalization.
   - Automatically asserts that the resulting records contain:
     - At least one valid game total (`GAMELINE`, `TOTAL`)
     - At least one valid team total (`TEAM_PROP`, `POINTS`)
     - At least one valid spread (`GAMELINE`, `SPREAD`)
     - At least one valid player prop (`PLAYER_PROP`)
   - Exits 0 on success with detailed output summary, non-zero on assertion failure.
