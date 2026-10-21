# NBA data sources — Phase 0 spike

Spike run **2026-10-20** (opening night of the 2026-27 regular season). Scope: read-only,
free, unauthenticated endpoints plus the stored Outlier payloads already on disk. No paid
or cookie-authenticated source was used; no reasoning model or desk was called.

**Not done, and why.** No *authenticated* Outlier call was made. The Outlier session
material lives in the canonical checkout's `config/.outlier_session/`; the harness refused
to let this session read it, so every Outlier row below is either **verified from a stored
payload** (the WNBA raw feeds under `data/WNBA/raw/`, same API, same code path) or
**unverified for NBA**. The first real run of `python -m outlier_nba.cli capture` on an
authenticated machine is the verification step for the Outlier rows.

Status key: **V** verified today · **V-shape** shape verified on the sibling WNBA feed, NBA
assumed identical · **U** unverified · **X** tried, blocked or unusable.

## 1. Outlier (`https://api.outlier.bet`, existing `OutlierApiClient`)

| Item | Status | Evidence / note |
|---|---|---|
| `GET /sportsdata/leagues/NBA/schedule` | U | Same route as WNBA/MLB (`api.fetch_schedule`). Event shape (`eventId`, `scheduledTime`, `status`, `live`, `home/away.alias`) is V-shape. |
| `GET /sportsdata/leagues/NBA/playerProps` | U (V-shape) | WNBA payload 2026-10-20: 1,557 rows, single page (`stopped_reason: single_page`). |
| **Alt ladders inside playerProps** | **V-shape** | Yes. Rows carry `outcome.primary`; 1,107 of 1,557 WNBA rows are `primary:false`, and one `marketId` holds up to 29 outcomes at different lines. Alt rungs are almost all OVER-only (1,050 of 1,107). The plan's question ("do props include alt ladders?") is answered: they do, no extra endpoint needed. |
| Per-event game markets `GET /events/{id}/markets?marketType=GAMELINE\|TEAM_PROP` | U (V-shape) | MLB raw: spreads and totals are **ladders** (one market, many `line` outcomes, each with a per-book `odds` list). `periodLabel` marks partial periods ("1I" for MLB). NBA full-game labels are unverified, so capture treats any non-empty label outside `{FG, GAME, FULL, F}` as partial and skips it (fail-closed). |
| Injuries `GET /leagues/NBA/teams/{teamId}/injuries` | U | Route used for WNBA/MLB in `games.py`. NBA status vocabulary and any timestamp field are unverified. |
| Opponent rank | V-shape | Not returned for any league (registry comment, confirmed in this repo); `opp_rank_applicable=False`. |
| Book set | V-shape | WNBA books: FANDUEL, DRAFTKINGS, CAESARS, BETMGM, BETRIVERS, FANATICS, HARDROCK(+_R), THESCOREBET, NOVIG, PROPHETX, POLYMARKET_US/INTERNATIONAL, FLIFF, **PRIZEPICKS, UNDERDOG, SLEEPER**. |
| **DFS books carry non-prices** | **V-shape** | PrizePicks rows have `+100` with `odds_type: demon/goblin`; Underdog/Sleeper are flat pick'em multipliers. Mixing them into a de-vig consensus corrupts it, so `outlier_nba.market.consensus.DFS_BOOKS` excludes them from consensus *and* from best-price selection. |
| Sharp-book set for a sharp anchor (U13) | U | Exchange-style books (NOVIG, PROPHETX, Polymarket) are present on WNBA; whether they are sharp enough to anchor on needs OOS evidence, not assumption. Not used in Phase 1. |
| Alt-line one-sidedness | V-shape | Because alt rungs are OVER-only there is nothing to de-vig against, so Phase 1 stores **no** consensus probability for them (`ONE_SIDED_NO_DEVIG`) rather than a vig-inflated one. |

## 2. Results, box scores, play-by-play (ESPN public JSON)

| Item | Status | Evidence / note |
|---|---|---|
| `site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard` | **V** | 200, 0.4 s, 53 KB. Season object `2026-27`, regular season, `2026-09-30` → `2027-06-26`. Today: BOS@DET final, PHI@NYK in progress, OKC@SAS scheduled. **U12 resolved: the season opened today.** |
| `.../nba/summary?event={id}` box score | **V** | Completed game: `boxscore.players[].statistics[]` with labels `MIN PTS FG 3PT FT REB AST TO STL BLK OREB DREB PF +/-`. Same grammar `results._boxscore_players` already parses for WNBA, so NBA grades PTS/REB/AST/3PTS/combos/DD/TD with no new parser (fixture test added). |
| ESPN play-by-play | **V** | `summary.plays` present (499 plays for the sample game). Fields not audited for lineup stints; not needed before Phase 2. |
| ESPN team abbreviations | **V-shape** | ESPN uses `GS NO NY SA UTAH WSH`; Outlier/registry tricodes are `GSW NOP NYK SAS UTA WAS`. `results.TEAM_ALIASES` now maps them (tested). |
| ESPN `pickcenter` | V | Single-book (DraftKings) spread and total on a finished game (`BOS -1.5`, `220.5`). One book, no timestamp: usable only as a coarse sanity check of a closing line, **not** as a verified close. |
| ESPN athlete game logs (`site.web.api.espn.com/.../athletes/{id}/gamelog`) | V | 200, 957 KB for one player. Candidate backfill source for D4; schema not audited. |
| Rate limits / ToS | U | No published limit or terms for these endpoints; `robots.txt` returned 403. Treat as undocumented: keep to a handful of requests per nightly run, cache raw responses. |

## 3. NBA-operated sources

| Item | Status | Evidence / note |
|---|---|---|
| `cdn.nba.com/static/json/liveData/*` (scoreboard, boxscore, play-by-play) | **X** | 403 to a plain client with an honest User-Agent, and `robots.txt` 403. Not worked around. |
| `stats.nba.com/stats/*` (box v3, lineups, shot charts, tracking) | **X** | Timed out after 20 s from this machine. Widely reported to block non-browser clients; terms of use not reviewed. **Phase 2/3 data for lineup stints, shot charts and tracking is therefore unproven.** Tracking-only fields (potential assists, rebound chances, defender matchups) have no confirmed free source. |
| Official injury report | **V** (listing) | `official.nba.com/nba-injury-report-2025-26-season/` returned 79 PDFs for 2026-10-20, **one every 15 minutes**, with the publication time in the file name (`Injury-Report_2026-10-20_07_30PM.pdf`, host `ak-static.cms.nba.com/referee/injury/`). **U5 resolved for point-in-time use:** the file name is a verifiable publication stamp. PDF contents were not parsed in this spike. |
| Referee assignments | **V** | `official.nba.com/referee-assignments/` has a table (Game, Crew Chief, Referee, Umpire, Alternate), posted about 09:00 ET on game day. There is no per-row timestamp, so point-in-time history requires **our own snapshots**; there is no historical archive on the page. ESPN `summary.gameInfo.officials` lists the three on-court officials after the fact. |
| Starting lineups (U6) | U | Not found in this spike. |
| Coach-by-date table (U8) | U | No source found. |

## 4. Historical closing lines (D4 / U3)

| Candidate | Status | Note |
|---|---|---|
| Sportsbook Reviews Online NBA odds archive | U | Page returns 200; the archive links were not machine-readable in this fetch, and season coverage was not confirmed (believed to end around 2022-23). Needs a manual look before relying on it. |
| ESPN `pickcenter` per game | V (coarse) | Single book, no close timestamp, depth of history unverified. |
| Paid odds-history APIs | not used | Out of scope by instruction. |
| Prospective capture | **V by construction** | `outlier_nba` Phase 1 stores every market snapshot with a fetch timestamp; this is the only source of verified-timestamp closes and of any prop history (U4). |

**Consequence for the plan.** Team-market "beat the market" evidence must come from Phase 1
shadow capture unless the SBRO archive turns out to be usable; prop families can only pass that
key prospectively. Phases 2-3 should not assume stats.nba.com tracking data until a permitted
access path is confirmed.
