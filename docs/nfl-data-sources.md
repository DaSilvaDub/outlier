# NFL data sources (surveyed 2026-09-28)

Reachability notes: "sandbox" = the Claude cloud container, whose proxy allows
`github.com/<org>/<repo>/releases/download/...` but blocks the GitHub API, raw
files (`/raw/`, `raw.githubusercontent.com`), ESPN, PFR, StatMuse and FootballDB.
The Windows pipeline machine has no such limits.

## A. nflverse release assets (`github.com/nflverse/nflverse-data/releases/download/<tag>/<file>`)

All verified downloadable from the sandbox for 2026 on 2026-09-28 unless noted.

| Priority | Tag / file | 2026 freshness | What it gives | Pipeline use |
|---|---|---|---|---|
| **Used** | `stats_team/stats_team_week_2026.csv` | through Wk 3 | 138 team box cols per game | Matchup tape (`outlier_nfl/tape_nflverse.py`) |
| **Used** | `schedules/games.csv` | full season | scores, **closing spread/total/ML + odds**, roof, surface, **temp, wind**, rest days, referee, starting QB names, coaches | Tape scores; also weather pillar, CLV backtests, rest edges |
| High | `injuries/injuries_2026.csv` | Wk 3 (Nacua Doubtful, C. Williams Out, Darnold Full) | report + practice status per player | Auto inactive list for `build_matchup_script(injuries=...)`; replaces hand-kept QB/injury notes |
| High | `depth_charts/depth_charts_2026.csv` | same-day (dt 2026-09-27 12:56Z) | pos_rank per team/position | Auto role fields (qb/rb1/te/wr) for the tape; fixes stale `roster.py` depth charts (e.g. LAR TE1 is Colby Parkinson, not Higbee) |
| High | `pfr_advstats/advstats_week_def_2026.csv` (+ `_pass`, `_rush`, `_rec`) | Wk 3 | **pressures**, hurries, QB hits, blitzes, missed tackles, coverage allowed; QB pressure/drops; RB YBC/YAC, broken tackles | Pass-rush grade from pressures instead of sacks (sacks decided tonight's Stafford signal by 0.5) |
| High | `espn_data/qbr_week_level.csv` | Wk 3 | QBR, EPA, points added per QB-game | `qb_grade` for all 32 teams (today only 4 teams had PFF grades) |
| High | `stats_player/stats_player_week_2026.csv` | Wk 3 | 150 cols: targets, **target share, air-yards share, WOPR**, carries, EPA | Prop projection / hit-rate re-basing (runbook pillar 1 & 4) |
| High | `snap_counts/snap_counts_2026.csv` | Wk 3 | offense/defense/ST snaps & % | Volume/role changes (vacated-touch reallocation) |
| Med | `pbp/play_by_play_2026.csv.gz` | Wk 3 (7.9k plays, 372 cols) | EPA, success, CPOE, xpass/pass-over-expected, WP, red zone | EPA-based unit grades; fills `outlier_nfl/external/pbp.py` stub |
| Med | `nextgen_stats/ngs_{passing,rushing,receiving}.csv.gz` (all seasons in one file) | Wk 3 | time-to-throw, aggressiveness, CPOE, RYOE, separation | Fills `outlier_nfl/external/ngs.py` stub |
| Med | `ftn_charting/ftn_charting_2026.csv` | Wk 3 | play-action, motion, RPO, box count, blitzers, pass rushers, drops | Scheme matchups (blitz-heavy D vs QB, light boxes vs RB) |
| Low | `weekly_rosters/roster_weekly_2026.csv`, `rosters/roster_2026.csv`, `players/players.csv` | current | IDs (gsis/espn/pfr/pff/sleeper), status | ID joins across sources |
| Low | `officials/officials.csv` | current | crew per game | Referee penalty/total tendencies |
| Low | `contracts`, `draft_picks`, `combine`, `trades`, `teams` | current | reference | Context only |
| n/a | `pbp_participation/pbp_participation_2026.csv` | **404** (not published for 2026) | on-field personnel | — |

Python loader: [nflverse/nflreadpy](https://github.com/nflverse/nflreadpy) wraps all of the above with caching
(replaces the deprecated [nfl_data_py](https://github.com/nflverse/nfl_data_py)). Not installable in the
sandbox (PyPI blocked), fine on the pipeline machine.

## B. Other GitHub sources

| Priority | Repo | What | Sandbox |
|---|---|---|---|
| High | [ffverse/ffopportunity](https://github.com/ffverse/ffopportunity) | Expected yards / TDs / fantasy points per player-week from an xgboost model on nflverse PBP; `latest-data` release has `ep_weekly_2026.csv` | ✅ release download works |
| Med | [dynastyprocess/data](https://github.com/dynastyprocess/data) | Player-ID crosswalk (ESPN, Sleeper, PFF, Yahoo, Fleaflicker, …) | ❌ raw blocked |
| Med | [greerreNFL/nfelo](https://github.com/greerreNFL/nfelo), [nfeloqb](https://github.com/greerreNFL/nfeloqb), [nfelounits](https://github.com/greerreNFL/nfelounits), [nfelosrs](https://github.com/greerreNFL/nfelosrs) | Market-aware Elo, QB Elo, EPA unit ratings, SRS; output CSVs in repo (check license, issue #9) | ❌ raw blocked |
| Med | [sportsdataverse/sportsdataverse-py](https://github.com/sportsdataverse/sportsdataverse-py) | Wrappers for ESPN + NFL.com "Shield" APIs (live injuries, depth, win prob, QBR) | ❌ ESPN blocked |
| Med | [pseudo-r/Public-ESPN-API](https://github.com/pseudo-r/Public-ESPN-API), gists [nntrn](https://gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1c) / [akeaswaran](https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b) | Undocumented ESPN endpoint docs (odds, injuries, depth, FPI/predictor) | ❌ ESPN blocked |
| Med | [Cuevman81/NFL_Weather_Shiny_App](https://github.com/Cuevman81/NFL_Weather_Shiny_App) | Pattern for live forecasts via the National Weather Service API + stadium long-axis bearing (crosswind vs along-field) | n/a (reference) |
| Med | [greerreNFL/Stadiums](https://github.com/greerreNFL/Stadiums) | Stadium metadata for weather joins | ❌ raw blocked |
| Low | [FinnedAI/sportsbookreview-scraper](https://github.com/FinnedAI/sportsbookreview-scraper) | SBR scraper + 2011–2021 NFL odds dataset (backtests) | ❌ raw blocked |
| Low | [jordantete/OddsHarvester](https://github.com/jordantete/OddsHarvester) | OddsPortal current/historical odds via Playwright (check site ToS) | n/a |
| Low | [pwu97/bettingtools](https://github.com/pwu97/bettingtools), [declanwalpole/sportsbook-odds-scraper](https://github.com/declanwalpole/sportsbook-odds-scraper) | Historical Vegas lines; single-match book scraper | n/a |
| Low | [ThompsonJamesBliss/WeatherData](https://github.com/ThompsonJamesBliss/WeatherData), [Nolanole/NFL-Weather-Project](https://github.com/Nolanole/NFL-Weather-Project) | Historical game weather (2000–2020) for backtests | ❌ raw blocked |
| Ref | [JovaniPink/awesome-nfl-data](https://github.com/JovaniPink/awesome-nfl-data), [awesome-sports-betting-data](https://github.com/matthieumarchand75-lab/awesome-sports-betting-data) | Curated lists for further discovery | — |

Odds history is largely redundant with nflverse `schedules` (closing lines + odds); Outlier
remains the source for live multi-book props.

## Suggested integration order

1. ~~Auto roles + inactives in the tape refresh from `depth_charts` + `injuries`.~~ Done (PR #198).
2. ~~Pass-rush grade from `pfr_advstats` pressures; `qb_grade` from ESPN QBR for all 32 teams.~~ Done (PR #198).
3. ~~Fill `outlier_nfl/external/` stubs (`ngs`, `pbp`, `schedule`) from nflverse.~~ Done (PR #198): records land in `data/NFL/normalized/nfl_external_metrics_*.json` (not yet consumed by the matchup engine).
4. Weather pillar from `schedules` temp/wind (+ NWS forecast on the pipeline machine).
5. Player prop context from `stats_player_week`, `snap_counts`, `ffopportunity`.
