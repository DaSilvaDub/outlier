# Outlier NCAAFB Props & Insights: Comprehensive Survey & Fixture Audit

**Author:** explorer_survey_2  
**Date:** 2026-09-06  
**Repository Worktree:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Git Branch:** `feat/outlier-props-insights`  
**Discovery Commit:** `a9371c35529c90d71551e726c387414500f1247f`  
**Reference Slate Date:** `2026-09-05` (US Eastern)  

---

## 1. Executive Summary

This survey examines the Outlier REST API discovery probe results, probe harness scripts, and committed test fixtures in the `cfb-analytics` worktree (`feat/outlier-props-insights`) to definitively determine what Outlier offers for NCAA Football (`NCAAFB`).

### Key Findings
1. **Gamelines (`GAMELINE`):** Fully operational and liquid. Probing `/sportsdata/events/{eventId}/markets?marketType=GAMELINE` yields extensive market cards for `SPREAD` (median 13 books/game), `TOTAL` (median 13 books/game), and `MONEYLINE` (median 9 books/game), along with unwhitelisted derivative props (`DOUBLE_RESULT`, `MONEYLINE_THREE_WAY`, `WINNING_MARGIN`).
2. **Team Props (`TEAM_PROP`):** Unoffered by Outlier. Probing `/sportsdata/events/{eventId}/markets?marketType=TEAM_PROP` returns HTTP 200 with an empty envelope: `{"markets": []}` (21 bytes).
3. **Player Props (`PLAYER_PROP`):** Unoffered by Outlier. Probing `/sportsdata/events/{eventId}/markets?marketType=PLAYER_PROP` returns HTTP 200 with an empty envelope: `{"markets": []}` (21 bytes). Player props are also explicitly out of scope per R2.
4. **Game Props (`GAME_PROP`):** Unoffered by Outlier. Probing `/sportsdata/events/{eventId}/markets?marketType=GAME_PROP` returns HTTP 200 with an empty envelope: `{"markets": []}` (21 bytes).
5. **Insights Endpoint (`/insights`):** Reachable but empty. Probing `/sportsdata/events/{eventId}/insights` returns HTTP 200 with an empty envelope: `{"insights": []}` (22 bytes). It does NOT 404 or 403.
6. **Player Identity Field:** Non-existent in Outlier NCAAFB betting markets. Because `PLAYER_PROP` returns empty payloads and `GAMELINE` outcomes contain only team/side labels, there are no `playerId`, `athleteId`, or player display names in market payloads. (Note: Outlier's team injury feed does provide `playerId`, but market data does not).
7. **Structural Traps Confirmed:**
   - **Trap 1 (Non-parallel books):** `outcomes[].books` list order does NOT match `outcomes[].odds[].book`. 661 ordering discrepancies were identified across the 3 sampled fixtures. Book attribution must strictly read from `odds[].book`.
   - **Trap 2 (Multi-row spanning):** Single propositions span multiple market cards per game (e.g., 25 `SPREAD` cards and 26 `TOTAL` cards across 3 games). Market unioning and deduplication on `(book, side, line)` are required.

---

## 2. Inventory of Probe Artifacts & Fixtures

### 2.1 Documentation & Discovery Probe Scripts
- **Probe Report:** `docs/probes/2026-09-05-ncaafb-props-discovery.md` (141 lines, 9,000 bytes). Authored during Discovery Milestone M0 at commit `a9371c3`.
- **Probe Script:** `scripts/probe_ncaafb_outlier.py` (890 lines, 38,195 bytes). A dual-mode (live HTTP / offline cache replay) discovery engine utilizing stdlib only (`urllib.request`, `json`, `hashlib`, `statistics`).
- **Fixture Cleanliness Test:** `tests/test_fixtures_cleanliness.py` (55 lines, 2,015 bytes). Asserts zero JWT tokens, cookies, or banned authorization keys exist in fixture JSON files.

### 2.2 Committed Fixtures (`tests/fixtures/outlier/`)
A total of 16 fixture files are committed under `tests/fixtures/outlier/`:

| File Name | File Size | Content Summary |
|---|---|---|
| `schedule_ncaafb.json` | 90,934 bytes | Full season schedule: 125 events total; 31 events on slate `2026-09-05` (US Eastern) |
| `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json` | 1,711,314 bytes | Thundering Herd @ Nittany Lions (58,066 lines of JSON, 13 books) |
| `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_TEAM_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_PLAYER_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAME_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_insights.json` | 22 bytes | `{"insights": []}` |
| `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_GAMELINE.json` | 1,578,735 bytes | Cardinals @ Buckeyes (53,841 lines of JSON, 13 books) |
| `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_TEAM_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_PLAYER_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_GAME_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_d3c27ca7c2343f149a695c5a810549c9cc4d6426_insights.json` | 22 bytes | `{"insights": []}` |
| `event_e04493d90a41b698b3c38d344c417330478d6d9d_GAMELINE.json` | 1,715,792 bytes | Mean Green @ Hoosiers (58,530 lines of JSON, 13 books) |
| `event_e04493d90a41b698b3c38d344c417330478d6d9d_TEAM_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_e04493d90a41b698b3c38d344c417330478d6d9d_PLAYER_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_e04493d90a41b698b3c38d344c417330478d6d9d_GAME_PROP.json` | 21 bytes | `{"markets": []}` |
| `event_e04493d90a41b698b3c38d344c417330478d6d9d_insights.json` | 22 bytes | `{"insights": []}` |

---

## 3. Analysis of Probed Market Types

Four market types were probed against Outlier's endpoint:
`GET /sportsdata/events/{eventId}/markets?marketType={marketType}`

### 3.1 `GAMELINE` (Resolves: YES)
- **HTTP Status:** 200 OK across all events.
- **Payload:** Populated JSON array of market cards.
- **Discovered Propositions:**
  - `SPREAD`: Full-game point spreads. 25 market cards across 3 games. 13 distinct sportsbooks (`BETRIVERS`, `CAESARS`, `DRAFTKINGS`, `FANATICS`, `FANDUEL`, `FLIFF`, `HARDROCK`, `HARDROCK_R`, `KALSHI`, `MIDNITE`, `NOVIG`, `PROPHETX`, `THESCOREBET`). Median 13.0 books per game. Admitted per R2.
  - `TOTAL`: Full-game over/under totals. 26 market cards across 3 games. 13 distinct sportsbooks (identical 13 books as SPREAD). Median 13.0 books per game. Admitted per R2.
  - `MONEYLINE`: Full-game straight moneyline. 19 market cards across 3 games. 12 distinct sportsbooks (excluding `CAESARS` in sampled games). Median 9.0 books per game. Admitted per R2 (`ML`).
  - `DOUBLE_RESULT`: Half-time / Full-time result. 3 market cards (1 per game). 3 distinct books (`DRAFTKINGS`, `FANATICS`, `MIDNITE`). Median 3.0 books per game. Dropped per R2 (unwhitelisted).
  - `MONEYLINE_THREE_WAY`: 60-minute regulation moneyline with Draw option. 8 market cards across 3 games. 3 distinct books (`DRAFTKINGS`, `FANATICS`, `THESCOREBET`). Median 2.0 books per game. Dropped per R2 (unwhitelisted).
  - `WINNING_MARGIN`: Margin of victory bands. 5 market cards across 3 games. 2 distinct books (`DRAFTKINGS`, `FANDUEL`). Median 2.0 books per game. Dropped per R2 (unwhitelisted).

### 3.2 `TEAM_PROP` (Resolves: EMPTY)
- **HTTP Status:** 200 OK.
- **Payload:** `{"markets": []}`.
- **Propositions:** None (0 cards, 0 books).
- **Evaluation:** Outlier does not publish team total points, team offensive yards, team receiving yards, or team rushing yards for NCAAFB on this slate.

### 3.3 `PLAYER_PROP` (Resolves: EMPTY)
- **HTTP Status:** 200 OK.
- **Payload:** `{"markets": []}`.
- **Propositions:** None (0 cards, 0 books).
- **Evaluation:** Outlier does not publish player props (passing, rushing, receiving, touchdowns) for college football. Note that even if offered, NCAAF player props are explicitly out of scope per R2 §61.

### 3.4 `GAME_PROP` (Resolves: EMPTY)
- **HTTP Status:** 200 OK.
- **Payload:** `{"markets": []}`.
- **Propositions:** None (0 cards, 0 books).
- **Evaluation:** No game props are published under this market type.

---

## 4. Analysis of Insights Endpoint

- **Endpoint Probed:** `GET /sportsdata/events/{eventId}/insights`
- **HTTP Status:** 200 OK across all 3 sampled events.
- **Payload:** `{"insights": []}` (22 bytes).
- **Does it 404 or 403?:** No. The endpoint is accessible and responds with HTTP 200, but the list of insights is completely empty.
- **Action per Requirements (R1 §40 & §80):**
  - R1 §40: *"Does an insights endpoint exist for NCAAFB? If it 404s or 403s, say so and stop — do not synthesise an insights feed."*
  - The probe report explicitly notes: *"Stop condition triggered for insights. Endpoint does not provide an active insights feed for NCAAFB; do not synthesize mock feed."*
  - `--with-insights` must default to disabled and produce zero records gracefully without errors.

---

## 5. Player Identity Field Assessment

- **Analysis:**
  - An inspection of all outcome objects in `tests/fixtures/outlier/*_GAMELINE.json` confirms the following keys:
    `['bookPrimary', 'books', 'label', 'line', 'lineLabel', 'odds', 'outcomeId', 'outcomeProps', 'position', 'primary', 'stats']`
  - Zero player identity fields (`playerId`, `athleteId`, `playerName`, `displayName`) exist on `GAMELINE` outcomes.
  - Since `PLAYER_PROP` returns empty payloads (`{"markets": []}`), zero player prop outcomes exist.
  - While Outlier's injury endpoint (`/sportsdata/leagues/NCAAFB/teams/{teamId}/injuries`) does expose `playerId` for injury tracking, betting market payloads contain no player IDs.
- **Database Schema Consequence:**
  - There is no need to widen `odds_snapshots` or `market_consensus` with a `player_id` column for NCAAFB betting ingestion.
  - Table schemas should remain focused on game and team markets (`game_id`, `market`, `side`, `line`, `book`, `price_american`, etc.).

---

## 6. Structural Traps Verified in Fixtures

### 6.1 Trap 1: `outcome.books` is Not Parallel to `outcome.odds[].book`
- **Verification:** An automated audit of all three `GAMELINE` fixtures detected **661 occurrences** where `outcome.books` does not match `[o['book'] for o in outcome.odds]`.
- **Concrete Example:** In `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json`:
  - `outcome.books` = `['THESCOREBET', 'DRAFTKINGS', 'PROPHETX', 'BETRIVERS', 'FLIFF', 'FANATICS', 'MIDNITE', 'NOVIG']`
  - `outcome.odds[].book` = `['DRAFTKINGS', 'FANATICS', 'PROPHETX', 'NOVIG', 'THESCOREBET', 'BETRIVERS', 'FLIFF', 'MIDNITE']`
- **Impact:** Zipping `outcome['books']` with `outcome['odds']` attributes prices to completely wrong sportsbooks (e.g. assigning DraftKings' price to theScore Bet). The parser must extract `book` solely from `odds_entry['book']`.

### 6.2 Trap 2: Propositions Span Multiple Market Rows
- **Verification:**
  - `SPREAD`: Spans 25 distinct market objects across the 3 fixtures (~8-9 market cards per event).
  - `TOTAL`: Spans 26 distinct market objects across the 3 fixtures (~8-9 market cards per event).
  - `MONEYLINE`: Spans 19 distinct market objects across the 3 fixtures (~6-7 market cards per event).
- **Impact:** No single market card contains full market depth. Full book consensus requires unioning all market rows for a given proposition and de-duplicating by `(book, side, line)`.

---

## 7. Usability Assessment & Architectural Recommendations

| Category | Offered by Outlier? | Recommended Pipeline Action |
|---|---|---|
| Full-Game Gamelines (`SPREAD`, `TOTAL`, `ML`) | **YES** (12-13 books, median 9-13) | Ingest into `odds_snapshots` & `market_consensus`. |
| Derivative Gamelines (`DOUBLE_RESULT`, etc.) | **YES** (2-3 books) | Drop at parser per R2 whitelist. |
| Team Props (`POINTS`, `YARDS`) | **NO** (returns `{"markets": []}`) | Implement schema & parser support; degrade gracefully when empty. |
| Player Props | **NO** (returns `{"markets": []}`) | Drop / ignore per R2 §61. Do not add `player_id` to database. |
| Game Props | **NO** (returns `{"markets": []}`) | Drop / ignore per R2. |
| Insights Feed | **NO** (returns `{"insights": []}`) | Disable `--with-insights`; do not synthesize mock data. |

### Conclusion for Downstream Milestones
Outlier provides rich, multi-book liquidity for standard college football gamelines (`SPREAD`, `TOTAL`, `MONEYLINE`). However, Outlier does **not** provide college football player props, team props, or insights. Downstream implementation must support graceful degradation, handling empty responses cleanly without crashing the primary gameline pipeline.
