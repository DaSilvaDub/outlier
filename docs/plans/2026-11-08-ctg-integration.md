# Cleaning the Glass (CTG) Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the NBA team model use Cleaning the Glass's garbage-time-filtered team data in two ways. First, as a file-imported, as-of-safe data source. Second, as an A/B experiment that ends in a decision: does fitting the ridge on CTG's garbage-time-filtered per-game efficiency beat the current ESPN-box-score target out of sample and against the market?

**Architecture:** CTG data enters through the NBA data hub on Google Drive, `NBA_ROOT` = `G:\My Drive\Sports_Analytics\01_Active\NBA_Active`, where `nba-props-pipeline` already puts every NBA extract (Part B.2). A network-free import adapter, `outlier_nba/ingest/ctg.py`, reads that folder **read-only**: game-log JSON written by the legacy scraper, and CSVs the subscriber downloads by hand into the same folder. It rejects mislabeled files (Part A) and copies the rows into a local SQLite store outside git. Season-to-date tables become timestamped snapshots and are only ever read as of a date strictly before the slate. Per-game team logs are reconciled to the ESPN store by date, teams and both scores. They then feed an *optional* alternative efficiency target into `ratings/ridge.fit_ratings`, which the walk-forward backtest can switch on. Everything stays SHADOW, the default path is unchanged, and nothing reaches pricing until the pre-registered decision rule in Task 12 passes.

**Tech Stack:** Python 3.13, stdlib `csv`/`sqlite3`, numpy (already a dependency of `outlier_nba`), pytest. No Selenium, no HTTP client, no new dependency.

**Status (2026-11-08):**
* **Done:** Task 0 (ruling: "Yes, use them" for the existing Drive files; how new data is obtained is still open), Tasks 1-4, 5 (JSON fixtures; CSV step waits on a manual-export ruling), 6, 6b, and the `ctg-sync` part of Task 10. The first real sync imported 59 files, rejected 14 as Toronto's log under another name, and skipped 7. The store holds 1,140 regular-season 2025-26 games with zero disagreement between the two teams' logs (`docs/nba/data-sources.md` §6).
* **Deviations:**
  * `read_gamelog`'s `season` argument is optional; when omitted, the season is inferred from the row dates.
  * `season_type` is advisory, because one file's `regular_season` view contains playoff games.
  * The `manual\*.csv` glob is deferred until the CSV header is verified.
* **Next:** Task 7 (reconcile to ESPN), then Tasks 8, 9, the rest of Task 10, Task 11, and a 2025-26 *pilot* of Task 12.

---

## Part A — Research report (Cleaning the Glass)

Fetched 2026-11-08. **V** = verified on a fetched page or a real export on disk, **I** = inferred, **U** = unknown.

| Item | Status | Finding | Source |
|---|---|---|---|
| Public / official API | **U → none found** | No API, no data-licensing or business contact anywhere on the site | cleaningtheglass.com/stats, /about, /terms |
| Terms on scraping, bots, automated access | **V (silent)** | The terms (last updated 2017-10-10) have no clause on scraping, bots, crawling or data mining. Silence is not permission | cleaningtheglass.com/terms |
| Terms on export / CSV | **V (silent)** | A subscriber-facing "Download this table" control exists on the stats tables. Nothing permits or limits use of what it downloads | /stats/league/fourfactors, /stats/league/shots |
| Only stated usage permission | **V** | You may use stats "in articles, on podcasts, or on social media" with attribution. It says nothing about feeding a private betting model | cleaningtheglass.com/stats |
| CC BY-NC-SA clause | **V, does not apply** | Covers *user contributions* (forum posts), not the stats | /terms §3 |
| robots.txt | **V** | `User-agent: *` has `Crawl-delay: 5` and no Disallow. `/stats` is disallowed only for dotbot, Semrush and MegaIndex. robots.txt is not a licence | /robots.txt |
| Price | **V** | $5/month or $50/year, one-week free trial, non-refundable | /stats, /terms §4 |
| Paywall | **V (partial)** | Free view of the shooting page shows 5 teams and no defensive shooting. Subscribers get the full league and defensive tables. Gating of the other pages is unverified | /stats/league/shots |
| Seasons | **V** | Season filter 2003-04 → 2026-27. Regular season, playoffs and preseason | /stats/league/fourfactors |
| Update frequency | **U** | Not stated | — |
| Garbage time | **V** | 4th quarter only, with margin ≥25 (12:00-9:00), ≥20 (9:00-6:00) or ≥10 (last 6:00), **and** at most two starters on the floor across both teams. Once triggered it cannot revert. End-of-quarter possessions starting with ≤4 s in Q1-Q3 ("heaves") are also excluded. All site stats exclude both | /stats/guide/garbage_time |
| Possession definition | **V** | Starts on gaining possession, ends on losing it (no offensive-rebound reset). Technical free throws are excluded from pts/poss | /stats/guide/league_four_factors |
| Opponent / SOS adjustment | **U → treat as raw** | No page says the stats are opponent-adjusted. Opponent rank is a *filter* only | guide pages |
| Shot locations | **V** | Rim (<4 ft), short mid (4-13.75 ft), long mid (2s beyond 13.75 ft), corner 3, non-corner 3 (heaves excluded). The guide warns location data is "messy" and not exact | /stats/guide/league_shooting |
| Play context | **V** | Halfcourt, transition and putback splits (pts/play, frequency) | /stats/league/context |
| On/off, lineups, positions | **V (existence)** | CTG estimates positions for all players and publishes on/off and lineup tables. Definitions were not read | /stats |
| Data provider | **I** | A Sportradar logo appears on /stats, so it is probably CTG's play-by-play source. Not stated | /stats |

### Export formats verified from real files on disk

Thirteen legacy snapshots exist under `G:\My Drive\Sports_Analytics\01_Active\NBA_Active\LEAGUE\CLEANING_THE_GLASS\` (2026-03-17 → 2026-04-25, season 2025-26), plus per-team files under `...\TEAMS\<TRI>\CLEANINGdaGLASS\`. The old `nba-props-pipeline` produced them by **automated Selenium login** (`scrapers/cleaning_the_glass/ctg_export_league.py`, credentials from `.env`, saved cookies in `ctg_cookies.json`). This plan does **not** port, import or run that code (AGENTS.md: never import from `nba-props-pipeline`).

* `FOURFACTORS.csv` header (V): `Team,W,L,Diff Rank,Diff,OFFENSE: Pts/Poss Rank,OFFENSE: Pts/Poss,OFFENSE: eFG% Rank,OFFENSE: eFG%,OFFENSE: TOV% Rank,OFFENSE: TOV%,OFFENSE: ORB% Rank,OFFENSE: ORB%,OFFENSE: FT Rate Rank,OFFENSE: FT Rate,DEFENSE: Pts/Poss Rank,DEFENSE: Pts/Poss,DEFENSE: eFG% Rank,DEFENSE: eFG%,DEFENSE: TOV% Rank,DEFENSE: TOV%,DEFENSE: ORB% Rank,DEFENSE: ORB%,DEFENSE: FT Rate Rank,DEFENSE: FT Rate`
* `SUMMARY.csv` header (V): `Team,Point Diff Rank,Point Diff,W,L,Win%,Exp W82,Exp W,Win Diff Rank,Win Diff,Offense Rank,Offense,Defense Rank,Defense,Spread Diff Rank,Spread Diff,LAST 2 WEEKS: W,...`
* Second row is a league `Average` row. Team names are city-style: `Oklahoma City`, `LA Lakers`, `LA Clippers`, `Golden State`, `New York`. **27 of 30 do not resolve through `outlier_nba.identity.resolve_team`** (checked: only `LA Lakers`, `LA Clippers` and `Utah` do). Values look like `54.7%`, `+11.9`, `20.7`.
* Team game logs (V, as JSON from the legacy HTML scrape, **CSV export header U**): one row per game with `Date` (`04/07/26`), venue marker (`vs.` / `@`), `Opp` tricode, W/L, `Team Pts`, `Opp Pts`, `Spread`, then offense and defense four factors as value (`#`) plus percentile (`%`). There are also tables for halfcourt, transition and shooting frequency/accuracy. The legacy shot/context files hold only 5 teams, which matches the free-tier view.
* **Game-log JSON schema (V, read 2026-11-08 from 80 files on the Drive).** Two layouts exist:
  * Old (`TEAMS\<TRI>\CLEANINGdaGLASS\GAME_LOGS_<MM_DD_YYYY>.json`, March–May 2026) has a top-level `four_factors` list.
  * New (`Current_Matchups\CLEANING_THE_GLASS\<TRI>\CLEANINGdaGLASS\...`, May 25 onwards) has `season_views.{regular_season,playoffs}.{four_factors, offense_halfcourt, offense_transition, offense_shooting_frequency, offense_shooting_accuracy, defense_*}`, plus `team`, `scraped_at`, `row_counts` and `empty_tabs`.
  * Row keys used: `Date`, `column_2` (venue: `vs.`/`@`, absent in the old layout), `Opp`, `Team Pts`, `Opp Pts`, `Spread`, `Offense Pts/Poss #`, `Defense Pts/Poss #`, and the `Offense|Defense eFG%|TOV%|ORB% #` and FT-rate `#` keys. FT rate is `FT Rate #` for offense and `FT Rate #_1` for defense.
  * 12 files are unusable: empty, or with a broken header (`Col_0`, `Average`, …).
* **Data-quality defect (V, cross-checked against ESPN).** **14 of 68 usable game-log files are mislabeled.** Every `Current_Matchups\CLEANING_THE_GLASS` file written from 2026-05-26 onwards contains **Toronto's** game log, whatever team its folder and `team` field claim (ATL, BKN, BOS, CHA, NYK, SAS).
  * The "SAS" file of 06/13 opens `10/22/25 @ATL` and has `04/12/26 vs BKN 136-101`. ESPN shows those as TOR @ ATL 138-118 and BKN @ TOR 101-136, while SAS opened @ DAL.
  * Detection rule: a team never appears as its own `Opp`, so a file in which the claimed team appears in `Opp` is mislabeled.
  * The older `TEAMS\` files all pass. Whether the matching `TEAM_STATS`/`ONOFF`/`PLAYERS`/`LINEUPS` files have the same defect is **U** (their contents differ by hash, so it is not proven either way).
  * Anything in `nba-props-pipeline` that read those files since 2026-05-26 (for example `analysis/ctg_game_totals.py`, `models/edge_context_engine.py`) was working from Toronto's numbers.
* Coverage from correctly labeled files: **season 2025-26 only, ≈1,139 of 1,230 regular-season games** (each game appears in both teams' logs, so one valid file per game is enough). Own logs exist for 26 teams; NOP, ORL, PHX and SAC have none but are recovered through their opponents' logs. **No 2016-17 → 2024-25 game logs exist on the Drive.**

### Access verdict

| Method | Verdict |
|---|---|
| Subscriber clicks "Download this table" and saves the CSV into a local drop folder, for private research | **Most defensible.** It uses a feature the site gives subscribers. The terms are silent, and the stated usage line covers publication, not modelling. **Recommended**, conditional on the owner's ruling in Task 0 |
| Automated login + scraping (Selenium, cookies, HTML table parsing) | **Not authorized by anything found.** Silent terms are not consent, and it reuses account credentials in automation. **Excluded** unless CTG says yes in writing |
| Bulk historical game logs (≈30 teams × 8 seasons = 240 downloads) | Manually feasible but tedious. **Ask CTG** (ben@cleaningtheglass.com) for written permission or a bulk file before Phase C |
| Official API / licensed feed | Does not exist publicly |

Precedent: the SBRO closing-line archive was used under an explicit owner ruling with the site's consent not obtained (`docs/nba/phase2-market-backtest.md`). This plan holds CTG to the same standard: the owner rules, the ruling is recorded in `docs/nba/data-sources.md`, and nothing in the repository settles it.

---

## Part B — Existing pipeline assessment (what CTG would join)

Verified by reading the code at `8bb0363`.

```
ESPN public JSON ──► ingest/espn.py ──► data/NBA/store/nba_games.sqlite3 (12,850 games, 2016-17→2025-26)
                                              │
                                              ▼
                     ratings/possessions.build_dataset  (poss = FGA+0.44FTA-OREB+TOV, both teams averaged)
                                              │
              ┌───────────────────────────────┼─────────────────────────────┐
              ▼                               ▼                             ▼
   ratings/ridge.fit_ratings         pace.fit_pace (log poss48)      ratings/elo.py
   y = pts/poss*100, home term,      decay + carry prior              (MOV Elo, backtest pool only)
   decay tau, carry prior
              └──────────────┬────────────────┘
                             ▼
     ratings/model.py: walk_forward + NoiseTracker (OOS sigma_e, rho, level bias, beta_pe, OT ratio)
                             ▼
     sim/engine.py (joint possessions × correlated efficiencies, OT) ──► pricing/team_markets.py
                             ▼
     team_run.run_team_shadow (stages: feeds → team_state → normalize → price → model_ledger)
                             ▼
     run bundle: team_shadow.csv, team_fair_ladders.csv, team_ratings.csv  ·  ledger nba_model_probs / nba_model_eval
                             ▼
     track/evidence.py ──► track/promotion.py (config/nba_promotion.json, all families SHADOW, units 0)

Market side: Outlier API capture (ingest/outlier.py) · SBRO closes 2018-19→2022-23 (ingest/sbro.py, market/closing.py)
```

**Facts that shape the integration:**

1. The team model is **already** an opponent-adjusted, decayed, per-100 offense/defense rating with a home term (`ridge.py`). CTG's season `Pts/Poss` is raw (not opponent-adjusted) and season-to-date (not decayed). As a *feature*, CTG season efficiency mostly duplicates what the ridge already estimates, and estimates worse.
2. What CTG has that ESPN does not is the **garbage-time and heave filter**. ESPN's per-100 target includes possessions where neither team is trying to win. That is noise for a *strength* estimate. This is the one place CTG plausibly adds real signal to the existing model: a cleaner target, not an extra feature.
3. Measured gaps (`docs/nba/phase2-team-model.md`, `phase2-market-backtest.md`):
   * sim log loss 0.6256 (n=10,229) vs Elo 0.6270, so the ridge stack adds little to win probability;
   * **market beats the model by 0.0210 log loss** [CI −0.0273, −0.0147] on 5,604 games, and it wins every season;
   * total MAE 14.77, margin MAE 10.89; the level bias had to be corrected;
   * no availability/injury term exists in the team model, and no rest/travel table (plan items M3, §5 of the master plan).
4. Player-level models (M6 minutes, M7 rates) **are not built**. CTG on/off, positions and lineups have nothing to plug into yet.
5. The master plan (`docs/plans/2026-10-20-nba-master-pipeline.md` §10) decided "Paid/cookie scraper dependence (RG, CTG): Not used; free sources only in v1". **This plan reopens that decision. The owner must explicitly overrule it in Task 0.**
6. House rules that apply: calibration lock (never write `calibration/` or `data/` from an agent session for pipeline output); cloud-sync filter (build SQLite in local temp, copy, verify from a fresh process). The CTG store therefore lives under `%LOCALAPPDATA%\outlier_nba\ctg\` (`OUTLIER_NBA_CACHE` honoured), the same pattern as the ESPN cache. The Drive folder is the **source**; the local store is a cache of it. The importer never writes to the Drive.

### B.2 The NBA data hub on Google Drive (where extracted NBA data lands)

Verified 2026-11-08 by listing the folder and reading `nba-props-pipeline/core/output_paths.py`.

```
nba-props-pipeline scrapers / models (NBA_ROOT env or default)
  │  categories in core/output_paths.py → folders under NBA_ROOT
  ▼
G:\My Drive\Sports_Analytics\01_Active\NBA_Active\
  Current_Matchups\            active_matchups (legacy name: MATCHUP\)
     CLEANING_THE_GLASS\<TRI>\CLEANINGdaGLASS\{GAME_LOGS,TEAM_STATS,ONOFF,PLAYERS,LINEUPS}_<date>.json
     CLEANING_THE_GLASS\<TRI>\PLAYERS\<Name>_{gamelogs,profile}_<date>.json
     CLEANING_THE_GLASS\SCRAPE_MANIFEST_<date>.json · <YYYYMMDD_HHMMSS>\{SUMMARY,FOURFACTORS,PLAYERS,LINEUPS}.csv
     BETS (HARDROCK) · RG Projections · hashtag · Starting LineUp · INJURY REPORT · OUTLIER · CourtIQ · TEAM DATA · ...
  TEAMS\<TRI>\CLEANINGdaGLASS\GAME_LOGS_<date>.json        (older per-team CTG logs, Mar–May 2026)
  LEAGUE\CLEANING_THE_GLASS\<date>\{SUMMARY,FOURFACTORS}.csv (league season tables, Mar–Apr 2026)
  PROP EDGES\prop_edges_latest.csv ──────────────────────────► outlier: scripts/export_nba_extra_pack.py
  processed\snapshot_warehouse\fact_*                          ──► data/NBA/exports/nba_only.csv ──► Drive packs
  game results\<date>\ · logs\ · cache\ (holds ctg_cookies.json; never read)
```

**Links today:**
* `outlier` reads exactly one thing from this hub: `PROP EDGES\prop_edges_latest.csv`, via `scripts/export_nba_extra_pack.py`.
* `outlier_nba`, the team model, reads **nothing** from it. Its only inputs are ESPN, the Outlier API and SBRO.

**Link this plan adds:**
* `outlier_nba/ingest/ctg.py` resolves `NBA_ROOT` the same way the legacy code does: the env var `NBA_ROOT`, else the Drive default above. It walks only the CTG paths listed, read-only.
* The folder layout is **copied as constants**, not imported, because AGENTS.md forbids importing from `nba-props-pipeline`. A test pins each constant to the folder names that exist today.
* Hand-downloaded CSVs go in `NBA_ROOT\Current_Matchups\CLEANING_THE_GLASS\manual\`. Manual and scraped data then sit in one hub, and one sync command reads both.

---

## Part C — Feature comparison and priorities

| Feature | Existing source | CTG equivalent | Incremental value | Priority |
|---|---|---|---|---|
| Offensive / defensive efficiency | Ridge per-100 on ESPN box (opp-adjusted, decayed) | Season / last-2-weeks Pts/Poss, raw, garbage-filtered | As a feature: low (duplicate, unadjusted). **As a per-game ridge target: the one testable hypothesis** | **P1 (as target, Phase C)** |
| Net rating | `off + deff` from ridge | `Diff` | Same as above | P1 (target) / P3 (feature) |
| Four Factors | Not modelled (box has FGA/FTA/OREB/TOV only) | eFG%, TOV%, ORB%, FT rate, offense and defense | Low for team totals/spreads (they recombine into pts/poss). Possible shrinkage prior for early season. Diagnostic only | P3 |
| Garbage-time exclusion | None | All tables | This is *the* differentiator; it is what P1 tests | P1 |
| Shot profile (rim / mid / 3 freq + accuracy, defensive) | None | Shooting page (defensive side subscriber-only) | Hypothesis: 3PA-heavy matchups widen total variance (`sigma_total`). Needs as-of history to test | P2 |
| Transition / halfcourt | Pace model only | Context page (freq, pts/play) | Pace is already one fitted engine. Transition frequency risks double-counting tempo (master plan: "pace must exist in exactly one place") | P3 (diagnostic) |
| On/off impact | None | Team on/off pages | Needed for availability adjustment and the player model (M3/M6/M7), neither of which exists. Very noisy, needs heavy shrinkage | P4 (after M3/M6 exist) |
| Lineup impact | None | Lineup pages | Tiny samples; unstable | P5 (not planned) |
| Positions | None | Estimated positions | Useful for M7 matchup rates later | P4 |
| `Spread` in game logs / `Spread Diff` | SBRO closes end 2022-23 | Per-game spread in CTG logs (book and timestamp **U**) | Could extend closing-line evidence past 2022-23 **only if** the provenance is confirmed. Never used as a model input (it is market data) | P2 (provenance check only) |

**Highest-value metrics to bring in first:** per-game team offense and defense `Pts/Poss` from CTG game logs (garbage-filtered). Second: season `FOURFACTORS`/`SUMMARY` snapshots for a forward-captured diagnostic. Shot profile comes after the P1 test.

**Statistical compatibility rules (enforced in code):**

* CTG per-100 values are on CTG's possession definition and exclude garbage time. ESPN points and possessions include it. They are **never averaged together in one column**. Phase C swaps the ridge *target* only. The simulator still predicts full-game points, and the existing out-of-sample level correction (`NoiseTracker.level_bias`) measures and absorbs the level difference. Task 11 tests that it does.
* Weights stay ESPN full-game possessions (CTG logs carry no possession count), recorded as an approximation.
* CTG season aggregates are read **only** through `snapshot_asof(...)`, which takes the latest import with `as_of_utc` strictly before the slate's Eastern midnight. A season-total file downloaded after the fact is never joined to that season's games (leakage).

---

## Part D — Tasks

Conventions for every task: tests live in `tests/test_nba_ctg*.py`. Run `pytest <file> -v`, then `python -m ruff check <files>`. Commit with a conventional message ending in `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never call a reasoning model. Never write to `calibration/` or `data/`. Never commit CTG data rows: fixtures use the verified **headers** with synthetic numbers.

### Task 0: Access ruling (owner decision, blocks everything else)

**Files:**
- Modify: `docs/nba/data-sources.md` (append section "6. Cleaning the Glass")
- Modify: `docs/plans/2026-10-20-nba-master-pipeline.md` §11 Decisions (record the overrule of §10 "free sources only")

**Step 1:** Ask the owner two things and record both answers verbatim, with the date.

First: may the game-log JSON **already on the Drive** be imported? The legacy automated scraper produced those files (Part A). Reading files the owner already holds is the owner's call; this plan does not run, port or extend the scraper either way.

Second, how will new data be obtained? Choose one:
- (a) manual subscriber export into `NBA_ROOT\Current_Matchups\CLEANING_THE_GLASS\manual\`, private research only, no redistribution, attribution in docs (recommended);
- (b) (a) plus an email to ben@cleaningtheglass.com asking for written OK for model use and for bulk historical team game logs;
- (c) do not integrate CTG.

**Step 2:** If (c), stop here. Mark this plan "not executed" and commit the doc change only.

**Step 3:** Commit.

```bash
git add docs/nba/data-sources.md docs/plans/2026-10-20-nba-master-pipeline.md
git commit -m "docs(nba): record owner ruling on Cleaning the Glass access"
```

### Task 1: CTG team-name resolution

**Files:**
- Create: `outlier_nba/ingest/ctg.py`
- Test: `tests/test_nba_ctg.py`

**Step 1: Write the failing test**

```python
"""Cleaning the Glass import adapter (file-based; no network)."""

from __future__ import annotations

import pytest

from outlier_nba.ingest import ctg
from outlier_scrapers.registry import NBA_TEAM_DISPLAY


def test_city_map_covers_exactly_the_registry_tricodes() -> None:
    assert set(ctg.CTG_TEAM_CITIES.values()) == set(NBA_TEAM_DISPLAY)
    assert len(ctg.CTG_TEAM_CITIES) == 30


@pytest.mark.parametrize(
    ("raw", "code"),
    [("Oklahoma City", "OKC"), ("LA Lakers", "LAL"), ("LA Clippers", "LAC"),
     ("Golden State", "GSW"), ("New York", "NYK"), ("  Brooklyn ", "BKN"),
     ("BKN", "BKN"), ("GSW", "GSW")],
)  # fmt: skip
def test_resolve_ctg_team(raw: str, code: str) -> None:
    assert ctg.resolve_ctg_team(raw) == code


def test_average_row_and_unknowns_are_not_teams() -> None:
    assert ctg.resolve_ctg_team("Average") is None
    assert ctg.resolve_ctg_team("LA") is None  # ambiguous: never guessed
    assert ctg.resolve_ctg_team("") is None
```

**Step 2: Run it to verify it fails**

Run: `pytest tests/test_nba_ctg.py -v`
Expected: FAIL, `ModuleNotFoundError: outlier_nba.ingest.ctg`

**Step 3: Write minimal implementation**

```python
"""Cleaning the Glass (CTG) import adapter: subscriber CSV exports -> local snapshot store.

There is no network code here on purpose. CTG has no public API, and its terms do not address
automated access, so the only supported path is a file the subscriber downloaded with the
site's own "Download this table" control (see docs/nba/data-sources.md section 6 for the
owner's ruling). All CTG stats exclude garbage time and end-of-quarter heaves, and they are
NOT opponent-adjusted, so they must never be averaged into a column that holds ESPN
full-game numbers.
"""

from __future__ import annotations

from typing import Any

from outlier_nba.identity import resolve_team

# CTG prints city-style names; the shared registry resolves only 3 of 30 of them. Kept local
# (not added to the shared registry) so bare city names can never leak into another league's
# alias table.
CTG_TEAM_CITIES: dict[str, str] = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA",
    "Chicago": "CHI", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN",
    "Detroit": "DET", "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND",
    "LA Clippers": "LAC", "LA Lakers": "LAL", "Memphis": "MEM", "Miami": "MIA",
    "Milwaukee": "MIL", "Minnesota": "MIN", "New Orleans": "NOP", "New York": "NYK",
    "Oklahoma City": "OKC", "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX",
    "Portland": "POR", "Sacramento": "SAC", "San Antonio": "SAS", "Toronto": "TOR",
    "Utah": "UTA", "Washington": "WAS",
}  # fmt: skip


def resolve_ctg_team(value: Any) -> str | None:
    """Canonical tricode for a CTG team label (city name or tricode), else None."""
    text = str(value or "").strip()
    if not text:
        return None
    return CTG_TEAM_CITIES.get(text) or resolve_team(text)
```

If `test_city_map_covers_exactly_the_registry_tricodes` fails, fix the map to the registry's codes. Do not edit the registry.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nba_ctg.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add outlier_nba/ingest/ctg.py tests/test_nba_ctg.py
git commit -m "feat(nba): CTG team-name resolution for the import adapter"
```

### Task 2: Value parsing

**Files:**
- Modify: `outlier_nba/ingest/ctg.py`
- Test: `tests/test_nba_ctg.py`

**Step 1: Write the failing test**

```python
@pytest.mark.parametrize(
    ("raw", "value"),
    [("54.7%", 0.547), ("+11.9", 11.9), ("-2.7", -2.7), ("20.7", 20.7), ("64", 64.0),
     (" 115.9 ", 115.9), ("", None), ("-", None), ("NaN", None), (None, None)],
)  # fmt: skip
def test_parse_value(raw, value) -> None:
    got = ctg.parse_value(raw)
    assert got == (pytest.approx(value) if value is not None else None)


def test_parse_value_rejects_garbage() -> None:
    with pytest.raises(ctg.CTGParseError):
        ctg.parse_value("12.3abc")
```

**Step 2:** Run `pytest tests/test_nba_ctg.py -k parse_value -v`. Expected: FAIL (`AttributeError: parse_value`).

**Step 3: Implementation** (append to `ctg.py`)

```python
_BLANK = frozenset({"", "-", "\u2014", "nan", "none"})


class CTGParseError(ValueError):
    """A CTG export cell or file that does not have the documented shape."""


def parse_value(raw: Any) -> float | None:
    """``"54.7%"`` -> 0.547, ``"+11.9"`` -> 11.9, blank/dash/NaN -> None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text.lower() in _BLANK:
        return None
    pct = text.endswith("%")
    try:
        number = float(text[:-1] if pct else text)
    except ValueError as exc:
        raise CTGParseError(f"not a CTG number: {raw!r}") from exc
    return number / 100.0 if pct else number
```

**Step 4:** Run the same command. Expected: PASS.

**Step 5:** Commit: `git commit -am "feat(nba): parse CTG percent/signed/blank cells"`

### Task 3: Header schema detection for season tables

**Files:**
- Modify: `outlier_nba/ingest/ctg.py`
- Create: `tests/fixtures/ctg/fourfactors_synthetic.csv`, `tests/fixtures/ctg/summary_synthetic.csv`
- Test: `tests/test_nba_ctg.py`

**Step 1: Create fixtures.** Use the verified headers from Part A and a synthetic `Average` row plus three teams with **made-up** numbers. Example `fourfactors_synthetic.csv`:

```
Team,W,L,Diff Rank,Diff,OFFENSE: Pts/Poss Rank,OFFENSE: Pts/Poss,OFFENSE: eFG% Rank,OFFENSE: eFG%,OFFENSE: TOV% Rank,OFFENSE: TOV%,OFFENSE: ORB% Rank,OFFENSE: ORB%,OFFENSE: FT Rate Rank,OFFENSE: FT Rate,DEFENSE: Pts/Poss Rank,DEFENSE: Pts/Poss,DEFENSE: eFG% Rank,DEFENSE: eFG%,DEFENSE: TOV% Rank,DEFENSE: TOV%,DEFENSE: ORB% Rank,DEFENSE: ORB%,DEFENSE: FT Rate Rank,DEFENSE: FT Rate
Average,10.0,10.0,,0.0,,110.0,,50.0%,,15.0%,,25.0%,,20.0,,110.0,,50.0%,,15.0%,,25.0%,,20.0
Boston,15,5,1,+6.0,1,114.0,1,53.0%,1,13.0%,2,26.0%,2,19.0,1,108.0,1,48.0%,1,16.0%,1,24.0%,1,18.0
Golden State,10,10,2,0.0,2,110.0,2,50.0%,2,15.0%,1,27.0%,1,21.0,2,110.0,2,50.0%,2,15.0%,2,25.0%,2,20.0
LA Lakers,5,15,3,-6.0,3,106.0,3,47.0%,3,17.0%,3,22.0%,3,20.0,3,112.0,3,52.0%,3,14.0%,3,26.0%,3,22.0
```

Build `summary_synthetic.csv` the same way, using the SUMMARY header verified in Part A (all 26 columns). Copy the header from a real export in the drop folder or from the legacy file; numbers synthetic.

**Step 2: Write the failing test**

```python
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "ctg"


def test_read_season_table_detects_fourfactors_and_drops_average() -> None:
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    assert table.name == "fourfactors"
    assert [r.team for r in table.rows] == ["BOS", "GSW", "LAL"]
    bos = table.rows[0].metrics
    assert bos["off_pts_poss"] == pytest.approx(114.0)
    assert bos["def_pts_poss"] == pytest.approx(108.0)
    assert bos["off_efg"] == pytest.approx(0.53)
    assert "Diff Rank" not in bos and not any(k.endswith("_rank") for k in bos)


def test_read_season_table_rejects_unknown_header(tmp_path) -> None:
    bad = tmp_path / "x.csv"
    bad.write_text("Team,Foo\nBoston,1\n", encoding="utf-8")
    with pytest.raises(ctg.CTGParseError, match="unrecognised CTG table"):
        ctg.read_season_table(bad)


def test_read_season_table_rejects_unresolved_team(tmp_path) -> None:
    src = (FIX / "fourfactors_synthetic.csv").read_text(encoding="utf-8")
    bad = tmp_path / "ff.csv"
    bad.write_text(src.replace("Boston,", "Bostn,"), encoding="utf-8")
    with pytest.raises(ctg.CTGParseError, match="Bostn"):
        ctg.read_season_table(bad)
```

**Step 3:** Run `pytest tests/test_nba_ctg.py -k season_table -v`. Expected: FAIL.

**Step 4: Implementation** (append)

```python
import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# Required columns -> canonical metric name. Only value columns; "... Rank" columns are dropped
# (a rank is a function of the other 29 teams and adds nothing a model can use).
SEASON_TABLES: dict[str, dict[str, str]] = {
    "fourfactors": {
        "W": "w", "L": "l", "Diff": "diff",
        "OFFENSE: Pts/Poss": "off_pts_poss", "OFFENSE: eFG%": "off_efg",
        "OFFENSE: TOV%": "off_tov", "OFFENSE: ORB%": "off_orb", "OFFENSE: FT Rate": "off_ftr",
        "DEFENSE: Pts/Poss": "def_pts_poss", "DEFENSE: eFG%": "def_efg",
        "DEFENSE: TOV%": "def_tov", "DEFENSE: ORB%": "def_orb", "DEFENSE: FT Rate": "def_ftr",
    },
    "summary": {
        "W": "w", "L": "l", "Point Diff": "point_diff", "Exp W": "exp_w",
        "Offense": "off_pts_poss", "Defense": "def_pts_poss",
        "LAST 2 WEEKS: W": "l2w_w", "LAST 2 WEEKS: L": "l2w_l",
        "LAST 2 WEEKS: Offense": "l2w_off_pts_poss", "LAST 2 WEEKS: Defense": "l2w_def_pts_poss",
    },
}  # fmt: skip
# "Spread Diff" is deliberately not imported: it is derived from market lines, never a model input.


@dataclass(frozen=True)
class TeamRow:
    team: str
    metrics: dict[str, float | None]


@dataclass(frozen=True)
class SeasonTable:
    name: str
    rows: tuple[TeamRow, ...]
    sha256: str
    extra_columns: tuple[str, ...] = field(default=())


def _detect(header: list[str]) -> str:
    cols = set(header)
    hits = [name for name, req in SEASON_TABLES.items() if "Team" in cols and set(req) <= cols]
    if len(hits) != 1:
        raise CTGParseError(f"unrecognised CTG table (header starts {header[:6]})")
    return hits[0]


def read_season_table(path: Path) -> SeasonTable:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    header = list(reader.fieldnames or [])
    name = _detect(header)
    wanted = SEASON_TABLES[name]
    rows: list[TeamRow] = []
    for rec in reader:
        label = (rec.get("Team") or "").strip()
        if label == "Average":
            continue
        team = resolve_ctg_team(label)
        if team is None:
            raise CTGParseError(f"{path.name}: unresolved CTG team {label!r}")
        rows.append(TeamRow(team, {m: parse_value(rec.get(c)) for c, m in wanted.items()}))
    if len({r.team for r in rows}) != len(rows):
        raise CTGParseError(f"{path.name}: duplicate team rows")
    known = set(wanted) | {"Team"} | {c for c in header if c.endswith(" Rank")}
    extra = tuple(c for c in header if c not in known)
    return SeasonTable(name, tuple(rows), hashlib.sha256(raw).hexdigest(), extra)
```

**Step 5:** Run the tests. Expected: PASS. Then commit:

```bash
git add outlier_nba/ingest/ctg.py tests/test_nba_ctg.py tests/fixtures/ctg/
git commit -m "feat(nba): detect and parse CTG four-factors/summary exports"
```

### Task 4: Local snapshot store with as-of reads

**Files:**
- Modify: `outlier_nba/ingest/ctg.py`
- Test: `tests/test_nba_ctg_store.py`

**Step 1: Write the failing test**

```python
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from outlier_nba.ingest import ctg

FIX = Path(__file__).parent / "fixtures" / "ctg"
T1 = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)


def _store(tmp_path):
    conn = sqlite3.connect(tmp_path / "ctg.sqlite3")
    ctg.ensure_schema(conn)
    return conn


def test_import_is_idempotent_by_file_hash(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    first = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    again = ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T2)
    assert first.inserted and not again.inserted
    assert conn.execute("SELECT COUNT(*) FROM ctg_import").fetchone()[0] == 1


def test_snapshot_asof_is_strictly_before_the_slate(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    ctg.import_season_table(conn, table, season="2026-27", as_of_utc=T1)
    # a snapshot taken on the slate's own Eastern date is not usable for that slate
    assert ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-03") is None
    snap = ctg.snapshot_asof(conn, "fourfactors", "2026-27", "2026-11-04")
    assert snap is not None and snap.as_of_utc == T1
    assert snap.teams["BOS"]["off_pts_poss"] == pytest.approx(114.0)


def test_future_as_of_is_rejected(tmp_path) -> None:
    conn = _store(tmp_path)
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    with pytest.raises(ctg.CTGParseError, match="future"):
        ctg.import_season_table(
            conn, table, season="2026-27", as_of_utc=datetime(2999, 1, 1, tzinfo=UTC)
        )
```

**Step 2:** Run `pytest tests/test_nba_ctg_store.py -v`. Expected: FAIL.

**Step 3: Implementation** (append)

```python
import os
import sqlite3
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ctg_import (
    import_id    INTEGER PRIMARY KEY,
    file_sha256  TEXT NOT NULL UNIQUE,
    table_name   TEXT NOT NULL,
    season       TEXT NOT NULL,
    season_type  TEXT NOT NULL DEFAULT 'regular',
    team         TEXT,                 -- set for per-team tables (game logs)
    as_of_utc    TEXT NOT NULL,        -- when the subscriber downloaded it (upper bound on content)
    imported_utc TEXT NOT NULL,
    n_rows       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ctg_team_metric (
    import_id INTEGER NOT NULL REFERENCES ctg_import(import_id),
    team      TEXT NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL,
    PRIMARY KEY (import_id, team, metric)
);
CREATE INDEX IF NOT EXISTS ix_ctg_import_lookup ON ctg_import(table_name, season, season_type, as_of_utc);
"""


def default_store() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(os.environ.get("OUTLIER_NBA_CACHE", Path(base) / "outlier_nba")) / "ctg" / "ctg.sqlite3"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass(frozen=True)
class ImportResult:
    inserted: bool
    import_id: int


@dataclass(frozen=True)
class Snapshot:
    as_of_utc: datetime
    teams: dict[str, dict[str, float | None]]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds")


def import_season_table(
    conn: sqlite3.Connection, table: SeasonTable, *, season: str, as_of_utc: datetime,
    season_type: str = "regular",
) -> ImportResult:
    if as_of_utc.tzinfo is None:
        raise CTGParseError("as_of_utc must be timezone-aware")
    if as_of_utc > datetime.now(UTC):
        raise CTGParseError(f"as_of {as_of_utc} is in the future")
    hit = conn.execute(
        "SELECT import_id FROM ctg_import WHERE file_sha256 = ?", (table.sha256,)
    ).fetchone()
    if hit:
        return ImportResult(False, int(hit[0]))
    with conn:
        cur = conn.execute(
            "INSERT INTO ctg_import (file_sha256, table_name, season, season_type, as_of_utc,"
            " imported_utc, n_rows) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table.sha256, table.name, season, season_type, _iso(as_of_utc),
             _iso(datetime.now(UTC)), len(table.rows)),
        )  # fmt: skip
        import_id = int(cur.lastrowid or 0)
        conn.executemany(
            "INSERT INTO ctg_team_metric (import_id, team, metric, value) VALUES (?, ?, ?, ?)",
            [(import_id, r.team, m, v) for r in table.rows for m, v in r.metrics.items()],
        )
    return ImportResult(True, import_id)


def slate_cutoff_utc(slate_date: str) -> datetime:
    """Eastern midnight that opens the slate date: anything downloaded later is unusable."""
    return datetime.combine(date.fromisoformat(slate_date), time(0), tzinfo=ET).astimezone(UTC)


def snapshot_asof(
    conn: sqlite3.Connection, table: str, season: str, slate_date: str,
    season_type: str = "regular",
) -> Snapshot | None:
    row = conn.execute(
        "SELECT import_id, as_of_utc FROM ctg_import WHERE table_name = ? AND season = ?"
        " AND season_type = ? AND team IS NULL AND as_of_utc < ? ORDER BY as_of_utc DESC LIMIT 1",
        (table, season, season_type, _iso(slate_cutoff_utc(slate_date))),
    ).fetchone()
    if row is None:
        return None
    teams: dict[str, dict[str, float | None]] = {}
    for team, metric, value in conn.execute(
        "SELECT team, metric, value FROM ctg_team_metric WHERE import_id = ?", (row[0],)
    ):
        teams.setdefault(team, {})[metric] = value
    return Snapshot(datetime.fromisoformat(row[1]), teams)
```

Note: ISO strings with the same UTC offset sort chronologically, which is why `_iso` normalises everything to UTC.

**Step 4:** Run the tests. Expected: PASS.

**Step 5:** Commit: `git commit -am "feat(nba): CTG snapshot store with strict as-of reads"`

### Task 5: Game-log fixtures from the verified Drive schema (and the CSV header, if manual export is allowed)

The JSON game-log schema is **verified** (Part A). The hand-downloaded CSV header is still **unverified**.

**Step 1:** Create the two JSON fixtures with the exact key names from Part A and synthetic numbers. Never copy real rows.
- `tests/fixtures/ctg/gamelog_old_synthetic.json`: top-level `four_factors`, no `column_2`, 4 BOS games.
- `tests/fixtures/ctg/gamelog_new_synthetic.json`: `season_views.regular_season.four_factors`, with `column_2`, the same 4 BOS games.

Two of the 4 games must match the ESPN synthetic fixture in Task 7 (same dates, opponents and scores).

**Step 2:** Create `tests/fixtures/ctg/gamelog_mislabeled.json`. It claims `"team": "BOS"`, but one row has `"Opp": "BOS"`. This copies the real 2026-05-26 defect.

**Step 3:** Only if Task 0 allows manual export: the owner downloads one team's game log into the `manual\` folder. Record the header line only, with no data rows, in `docs/nba/data-sources.md` §6, then add `gamelog_synthetic.csv` with that header.

**Step 4:** Commit: `git commit -m "test(nba): CTG game-log fixtures from the verified Drive schema"`

### Task 6: Parse and store team game logs

**Files:**
- Modify: `outlier_nba/ingest/ctg.py`
- Test: `tests/test_nba_ctg_games.py`

Map the Part A keys into `GAMELOG_COLUMNS`: `Date`, `column_2` (venue), `Opp`, `Team Pts`, `Opp Pts`, `Offense Pts/Poss #`, `Defense Pts/Poss #`, and the offense/defense eFG%, TOV%, ORB% and FT-rate `#` keys. `read_gamelog` accepts both JSON layouts and, later, the CSV.

Other rules:
* Keys ending in `%`, `percentile_rank`, `percentile_cell_classes` or `percentile_background_color` are dropped.
* `Spread` is kept in `metrics_json` only and is never a model input.
* In the old layout `column_2` is missing, so `venue` is `None`. Orientation always comes from ESPN in Task 7.
* **Identity guard:** if the claimed team appears in any row's `Opp`, raise `CTGIdentityError`, a subclass of `CTGParseError`. The message names the file, the claimed team, and the team the rows actually belong to: the one tricode that never appears in `Opp`. This is the rule that catches the 14 Toronto files.

**Step 1: Write the failing test**

```python
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from outlier_nba.ingest import ctg

FIX = Path(__file__).parent / "fixtures" / "ctg"


@pytest.mark.parametrize("name", ["gamelog_old_synthetic.json", "gamelog_new_synthetic.json"])
def test_both_json_layouts_parse_identically(name: str) -> None:
    games = ctg.read_gamelog(FIX / name, team="BOS", season="2026-27")
    assert [(g.game_date, g.opp, g.team_pts, g.opp_pts, g.off_pp100, g.def_pp100) for g in games] == [
        (g.game_date, g.opp, g.team_pts, g.opp_pts, g.off_pp100, g.def_pp100)
        for g in ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2026-27")
    ]


def test_mislabeled_file_is_rejected_and_names_the_real_team() -> None:
    with pytest.raises(ctg.CTGIdentityError, match="BOS"):
        ctg.read_gamelog(FIX / "gamelog_mislabeled.json", team="BOS", season="2026-27")


def test_read_gamelog_rows() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2026-27")
    assert len(games) == 4
    g = games[0]
    assert g.team == "BOS" and g.opp in ctg.CTG_TEAM_CITIES.values()
    assert isinstance(g.game_date, date) and g.venue in {"home", "away", None}
    assert g.off_pp100 is not None and g.def_pp100 is not None


def test_gamelog_date_year_is_two_digit_and_season_bounded() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2026-27")
    assert all(date(2026, 9, 1) <= g.game_date <= date(2027, 7, 31) for g in games)


def test_store_gamelog_replaces_by_game_key(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "ctg.sqlite3")
    ctg.ensure_schema(conn)
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2026-27")
    ctg.store_gamelog(conn, games, file_sha256="a" * 64)
    ctg.store_gamelog(conn, games, file_sha256="b" * 64)  # a later, fuller download
    assert conn.execute("SELECT COUNT(*) FROM ctg_team_game").fetchone()[0] == 4
```

**Step 2:** Run it. Expected: FAIL.

**Step 3: Implementation.** Add to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS ctg_team_game (
    season     TEXT NOT NULL,
    game_date  TEXT NOT NULL,   -- Eastern calendar date as printed by CTG
    team       TEXT NOT NULL,
    opp        TEXT NOT NULL,
    venue      TEXT CHECK (venue IS NULL OR venue IN ('home', 'away')),  -- NULL in the old JSON layout
    team_pts   INTEGER,
    opp_pts    INTEGER,
    off_pp100  REAL,            -- garbage time + heaves excluded (CTG definition)
    def_pp100  REAL,
    metrics_json TEXT NOT NULL,
    file_sha256  TEXT NOT NULL,
    PRIMARY KEY (season, game_date, team)
);
```

Implement a `CTGGame` frozen dataclass, `read_gamelog(path, *, team, season)` and `store_gamelog(conn, games, *, file_sha256)` (`INSERT OR REPLACE`). Parse dates with `datetime.strptime(s, "%m/%d/%y").date()`. Then reject any date outside `[Sep 1 of the season's first year, Jul 31 of its second]` with `CTGParseError`. Map `vs.` to `home` and `@` to `away`. CTG does not mark neutral sites, so a neutral game reads `vs.`/`@` and Task 7 must resolve it against ESPN, not trust the marker. Resolve `Opp` through `resolve_ctg_team`, and raise on `None`.

**Step 4:** Run. Expected: PASS.

**Step 5:** Commit: `git commit -am "feat(nba): parse and store CTG team game logs"`

### Task 6b: Sync from the NBA Drive hub (read-only)

**Files:**
- Modify: `outlier_nba/ingest/ctg.py`
- Test: `tests/test_nba_ctg_sync.py`

Add:
* `nba_root() -> Path`: `NBA_ROOT` env, else `G:\My Drive\Sports_Analytics\01_Active\NBA_Active`.
* `GAMELOG_GLOBS`, which covers:
  * `TEAMS/*/CLEANINGdaGLASS/GAME_LOGS_*.json`
  * `Current_Matchups/CLEANING_THE_GLASS/*/CLEANINGdaGLASS/GAME_LOGS_*.json`
  * `Current_Matchups/CLEANING_THE_GLASS/manual/*.csv`
* `discover_gamelogs(root) -> list[(path, claimed_team, file_date)]`. The claimed team is the `<TRI>` folder, cross-checked against the JSON `team` field when present. The file date is parsed from `_MM_DD_YYYY`.
* `sync_from_hub(conn, root, *, season) -> SyncReport`:
  * Files are processed oldest first. A file whose SHA-256 is already in `ctg_import` is skipped, so re-running the sync is safe and cheap.
  * Empty files and files with a broken header (Part A's 12) are recorded as `skipped_unusable`.
  * A `CTGIdentityError` file is recorded as `rejected_mislabeled`, with the team its rows really belong to. Its rows are never stored.
  * The report lists per-file outcomes plus totals.
  * The function never opens a file for writing under `root`, never reads `cache/`, and never follows a path outside `root`.

**Step 1: Write failing tests**, building a fake hub in `tmp_path` with the Task 5 fixtures placed at the real relative paths:
- both layouts import, and the shared games are stored once (`ctg_team_game` key `(season, game_date, team)`);
- the mislabeled file appears in `report.rejected` with `actual="<TRI>"`, and none of its rows are stored;
- a second sync reports everything `already_imported`;
- a test asserts the glob folder names equal `{"TEAMS", "CLEANINGdaGLASS", "Current_Matchups", "CLEANING_THE_GLASS"}`, so a rename in the hub is a visible test change;
- monkeypatching `NBA_ROOT` changes `nba_root()`.

**Step 2:** Run; FAIL. **Step 3:** Implement. **Step 4:** Run; PASS.

**Step 5 (manual, owner's machine, only if Task 0 allowed the existing files):** run `python -m outlier_nba.cli ctg-sync --season 2025-26` against the real Drive. Expected, from the 2026-11-08 survey: 14 files rejected as mislabeled (all with `actual=TOR`) and 12 skipped as unusable. Record the real counts in `docs/nba/data-sources.md` §6, not these expected ones.

**Step 6:** Commit: `git commit -am "feat(nba): read-only CTG sync from the NBA Drive hub"`

### Task 7: Reconcile CTG games to the ESPN store

**Files:**
- Create: `outlier_nba/ingest/ctg_reconcile.py`
- Test: `tests/test_nba_ctg_reconcile.py`

**Also closes a known gap in the identity guard.** The importer catches a mislabeled file only when the claimed team appears as its own opponent. Early in a season a file claiming BOS that holds another team's first six games can pass, if that team hasn't played BOS yet. Reconciliation must therefore report, per source file, the share of rows whose (date, claimed team, opponent, scores) match ESPN. A file below 95% is flagged as mislabeled and its rows are excluded from `alt_eff`.

The rule follows `ingest/sbro.py`. Match by Eastern date (±1 day), the unordered team pair **and both final scores**. Orientation comes from ESPN. When both teams' logs exist, cross-check that `home.off_pp100 ≈ away.def_pp100` within 0.15 (they describe the same possessions).

**Step 1: Write the failing test**

```python
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from outlier_nba.ingest.ctg import CTGGame
from outlier_nba.ingest.ctg_reconcile import reconcile
from outlier_nba.ingest.espn import GameRow


def _espn(gid, day, home, away, hp, ap) -> GameRow:
    return GameRow(game_id=gid, season="2026-27", season_type=2,
                   start_utc=datetime(2026, 11, day, 0, 30, tzinfo=UTC),  # evening ET of day-1
                   home=home, away=away, home_pts=hp, away_pts=ap, periods=4, ot_periods=0,
                   neutral=False, attendance=1, note="")  # fmt: skip


def _ctg(day, team, opp, venue, tp, op, off, de) -> CTGGame:
    return CTGGame(season="2026-27", game_date=date(2026, 11, day), team=team, opp=opp,
                   venue=venue, team_pts=tp, opp_pts=op, off_pp100=off, def_pp100=de,
                   metrics={})  # fmt: skip


def test_matches_on_date_teams_and_scores_and_orients_from_espn() -> None:
    espn = [_espn("e1", 4, "BOS", "NYK", 110, 100)]
    logs = [_ctg(3, "BOS", "NYK", "home", 110, 100, 115.0, 104.0),
            _ctg(3, "NYK", "BOS", "away", 100, 110, 104.1, 115.0)]  # fmt: skip
    out = reconcile(logs, espn)
    assert out.alt_eff == {"e1": (pytest.approx(115.0), pytest.approx(104.0))}
    assert out.counts["matched"] == 1 and out.counts["cross_check_fail"] == 0


def test_score_mismatch_is_counted_not_joined() -> None:
    espn = [_espn("e1", 4, "BOS", "NYK", 110, 100)]
    out = reconcile([_ctg(3, "BOS", "NYK", "home", 111, 100, 115.0, 104.0)], espn)
    assert out.alt_eff == {} and out.counts["score_mismatch"] == 1


def test_disagreeing_team_logs_fail_the_cross_check() -> None:
    espn = [_espn("e1", 4, "BOS", "NYK", 110, 100)]
    logs = [_ctg(3, "BOS", "NYK", "home", 110, 100, 115.0, 104.0),
            _ctg(3, "NYK", "BOS", "away", 100, 110, 104.0, 117.0)]  # fmt: skip
    out = reconcile(logs, espn)
    assert out.alt_eff == {} and out.counts["cross_check_fail"] == 1
```

**Step 2:** Run. Expected: FAIL.

**Step 3: Implementation**

```python
"""Join CTG team game logs to ESPN games (date +/- 1, team pair, both scores)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from outlier_nba.ingest.ctg import CTGGame
from outlier_nba.ingest.espn import GameRow
from outlier_nba.ratings.possessions import ET

CROSS_TOL = 0.15  # pts/100: two logs of the same game must describe the same possessions


@dataclass(frozen=True)
class Reconciled:
    alt_eff: dict[str, tuple[float, float]]  # espn game_id -> (home off/100, away off/100)
    counts: Counter[str] = field(default_factory=Counter)


def reconcile(logs: Sequence[CTGGame], espn: Sequence[GameRow]) -> Reconciled:
    by_key: dict[tuple[frozenset[str], int, int], list[GameRow]] = {}
    for g in espn:
        key = (frozenset((g.home, g.away)), max(g.home_pts, g.away_pts), min(g.home_pts, g.away_pts))
        by_key.setdefault(key, []).append(g)
    counts: Counter[str] = Counter()
    seen: dict[str, dict[str, CTGGame]] = {}
    for c in logs:
        if c.team_pts is None or c.opp_pts is None or c.off_pp100 is None or c.def_pp100 is None:
            counts["incomplete"] += 1
            continue
        key = (frozenset((c.team, c.opp)), max(c.team_pts, c.opp_pts), min(c.team_pts, c.opp_pts))
        hits = [g for g in by_key.get(key, [])
                if abs((g.start_utc.astimezone(ET).date() - c.game_date).days) <= 1
                and (g.home_pts, g.away_pts) == ((c.team_pts, c.opp_pts) if g.home == c.team
                                                 else (c.opp_pts, c.team_pts))]  # fmt: skip
        if len(hits) != 1:
            near = any(abs((g.start_utc.astimezone(ET).date() - c.game_date).days) <= 1
                       for k, gs in by_key.items() if k[0] == key[0] for g in gs)  # fmt: skip
            counts["score_mismatch" if near else "unmatched"] += 1
            continue
        seen.setdefault(hits[0].game_id, {})[c.team] = c
    alt: dict[str, tuple[float, float]] = {}
    espn_by_id = {g.game_id: g for g in espn}
    for gid, sides in seen.items():
        g = espn_by_id[gid]
        h, a = sides.get(g.home), sides.get(g.away)
        if h and a and (abs(h.off_pp100 - a.def_pp100) > CROSS_TOL
                        or abs(a.off_pp100 - h.def_pp100) > CROSS_TOL):  # fmt: skip
            counts["cross_check_fail"] += 1
            continue
        home_off = h.off_pp100 if h else a.def_pp100  # type: ignore[union-attr]
        away_off = a.off_pp100 if a else h.def_pp100  # type: ignore[union-attr]
        alt[gid] = (float(home_off), float(away_off))
        counts["matched"] += 1
    return Reconciled(alt, counts)
```

Fix the `score_mismatch` vs `unmatched` heuristic so the second test passes: a team pair on a near date with a different score counts as `score_mismatch`. Index ESPN games by `(frozenset(teams), et_date)` as a second dict to make that lookup O(1). Keep the function under 50 lines by splitting out `_index` and `_match_one`.

**Step 4:** Run. Expected: PASS.

**Step 5:** Commit: `git commit -am "feat(nba): reconcile CTG game logs to ESPN games"`

### Task 8: Optional alternative efficiency target in the dataset and ridge

**Files:**
- Modify: `outlier_nba/ratings/possessions.py` (`Dataset`, `build_dataset`)
- Modify: `outlier_nba/ratings/ridge.py:126-127` (`y[r_home]`, `y[r_away]`)
- Test: `tests/test_nba_ratings.py`

**Step 1: Write the failing tests**

```python
def test_alt_efficiency_defaults_to_none_and_changes_nothing() -> None:
    ds = P.build_dataset([_game()])
    assert ds.alt_home is None and ds.alt_away is None


def test_alt_efficiency_replaces_only_matched_games_in_the_target() -> None:
    a = _game(game_id="a")
    b = _game(game_id="b", start_utc=START + timedelta(days=1))
    ds = P.build_dataset([a, b], alt_eff={"a": (120.0, 90.0)})
    assert ds.alt_home[0] == 120.0 and math.isnan(ds.alt_home[1])
    base = _fit(P.build_dataset([a, b]))
    alt = _fit(ds)
    assert alt.mu != base.mu  # the target moved


def test_alt_efficiency_survives_a_rebuild_from_a_subset() -> None:
    games = [_game(game_id="a"), _game(game_id="b", start_utc=START + timedelta(days=1))]
    ds = P.build_dataset(games[:1], alt_eff={"a": (120.0, 90.0), "b": (1.0, 1.0)})
    assert list(ds.alt_home) == [120.0]
```

(Add `import math` and `from datetime import timedelta` at the top of the test file if they are missing.)

**Step 2:** Run `pytest tests/test_nba_ratings.py -k alt_efficiency -v`. Expected: FAIL (`unexpected keyword alt_eff`).

**Step 3: Implementation**

In `possessions.py`, add two trailing fields to `Dataset` (defaults keep every existing constructor valid):

```python
    alt_home: FArr | None = None  # CTG garbage-filtered pts/100 (NaN where unmatched); None = off
    alt_away: FArr | None = None
```

Change the signature to `def build_dataset(games: list[GameRow], alt_eff: Mapping[str, tuple[float, float]] | None = None) -> Dataset:`. At the end, build the arrays from `ordered`:

```python
    alt_home = alt_away = None
    if alt_eff is not None:
        pairs = [alt_eff.get(g.game_id, (math.nan, math.nan)) for g in ordered]
        alt_home = np.array([p[0] for p in pairs], dtype=np.float64)
        alt_away = np.array([p[1] for p in pairs], dtype=np.float64)
```

Pass `alt_home=alt_home, alt_away=alt_away` to `Dataset(...)`. Import `math` and `Mapping`.

In `ridge.py`, replace lines 126-127:

```python
    raw_h = ds.hp[idx] / ds.poss[idx] * 100.0
    raw_a = ds.ap[idx] / ds.poss[idx] * 100.0
    if ds.alt_home is not None and ds.alt_away is not None:
        # CTG target where reconciled; ESPN full-game per-100 elsewhere. Never averaged.
        raw_h = np.where(np.isnan(ds.alt_home[idx]), raw_h, ds.alt_home[idx])
        raw_a = np.where(np.isnan(ds.alt_away[idx]), raw_a, ds.alt_away[idx])
    y[r_home] = raw_h
    y[r_away] = raw_a
```

**Step 4:** Run the new tests, then the full NBA suite to prove the default path is unchanged:

Run: `pytest tests/test_nba_ratings.py tests/test_nba_backtest.py tests/test_nba_sim.py tests/test_nba_team_run.py tests/test_nba_market_compare.py -v`
Expected: all PASS, with the same pass count as on `master` plus 3.

**Step 5:** Commit: `git commit -am "feat(nba): optional alternative per-100 target for the ridge (default off)"`

### Task 9: Thread `alt_eff` through the walk-forward backtest

**Files:**
- Modify: `outlier_nba/track/backtest.py:347-398` (`run_backtest`)
- Test: `tests/test_nba_backtest.py`

**Step 1: Write the failing test.** It uses the existing synthetic history (`tests/nba_synth.py`). Make an "alt" target equal to the true per-100 plus small noise on half the games. Assert that `run_backtest(..., alt_eff=mapping, tune_params=False)` returns `report["data"]["alt_target_games"] == len(mapping)`. Also assert it differs from the baseline report in `metrics` while `scored_seasons` is unchanged.

```python
def test_backtest_reports_alt_target_coverage() -> None:
    games = synth_history()
    mapping = {g.game_id: (g.home_pts / 1.0, g.away_pts / 1.0) for g in games[::2]}
    base = run_backtest(games, first_scored="2018-19", last_scored="2018-19",
                        tune_season="2017-18", tune_params=False, n_draws=200)
    alt = run_backtest(games, first_scored="2018-19", last_scored="2018-19",
                       tune_season="2017-18", tune_params=False, n_draws=200, alt_eff=mapping)
    assert alt["data"]["alt_target_games"] == len(mapping)
    assert "alt_target_games" not in base["data"]
    assert alt["scored_seasons"] == base["scored_seasons"]
```

(Match the imports and season labels that `tests/test_nba_backtest.py` already uses. If `synth_history` seasons differ, use its labels.)

**Step 2:** Run it. Expected: FAIL (`unexpected keyword alt_eff`).

**Step 3: Implementation.** Add `alt_eff: Mapping[str, tuple[float, float]] | None = None` to `run_backtest`. Pass it to **both** `build_dataset` calls, the tuning dataset and the full one, so tuning sees the same target as scoring. After `describe(ds)`, add `if alt_eff is not None: report["data"]["alt_target_games"] = int(np.sum(~np.isnan(ds.alt_home)))`. Also record `report["efficiency_source"] = "ctg" if alt_eff is not None else "espn"`.

**Step 4:** Run `pytest tests/test_nba_backtest.py -v`. Expected: PASS.

**Step 5:** Commit: `git commit -am "feat(nba): backtest accepts a CTG efficiency target"`

### Task 10: CLI — `ctg-import` and `backtest --ctg-store`

**Files:**
- Modify: `outlier_nba/cli.py` (new sub-parser next to `sbro-fetch`, near line 484; handler beside the backtest handler near line 311)
- Test: `tests/test_nba_cli.py`

Behaviour:
* `python -m outlier_nba.cli ctg-import FILE [FILE...] --season 2026-27 [--as-of ISO] [--team TRI] [--store PATH]`
  * `--season` defaults to `nba_season_for_date(today)` (`outlier_nba/run_context.py:44`).
  * `--as-of` defaults to the file's mtime in UTC. Print it so the owner sees it.
  * With `--team`, the file is a game log (Task 6); without it, a season table (Task 3).
  * Exit 0 on success, printing `imported` / `already_imported` per file. Exit 4 on `CTGParseError`, printing the message. Never writes inside the repo.
* `python -m outlier_nba.cli ctg-sync [--nba-root PATH] [--season S] [--store PATH]` runs Task 6b and prints the report. It exits 0 even if files were rejected, because rejections are data-quality findings, not crashes. It exits 5 only if `--nba-root` does not exist (Drive not mounted).
* `backtest --ctg-store PATH`: loads every `ctg_team_game` row, reconciles to the store's games (Task 7), prints the `counts`, and passes `alt_eff` to `run_backtest`. It fails with exit 6 (same code as the SBRO coverage gate) if fewer than 400 games per scored season matched, unless `--allow-partial` is given.

**Step 1: Write failing tests** with `capsys` and `tmp_path`, following the existing `tests/test_nba_cli.py` style:
- importing `fourfactors_synthetic.csv` into a tmp store exits 0 and prints `imported`, and a second import prints `already_imported`;
- a bad header exits 4.

**Step 2:** Run them and watch them fail. **Step 3:** Implement. **Step 4:** Run them and confirm they pass. **Step 5:** Commit: `git commit -am "feat(nba): ctg-import command and backtest --ctg-store"`

### Task 11: Level-compatibility test (garbage-filtered target vs full-game outcomes)

**Files:**
- Test: `tests/test_nba_backtest.py`

CTG per-100 excludes garbage time, while scores and totals do not. The OOS level correction (`ratings/model.py`, `NoiseTracker.level_bias`) is supposed to absorb a constant offset. Prove it on synthetic data instead of assuming it.

**Step 1: Write the test.** Build an alt target equal to the true per-100 **+3.0** for every game. Run the backtest with `tune_params=False`. Assert that after the warm-up (`MIN_BIAS_N`), the scored total bias satisfies `abs(report[...]["total_bias"]) < 1.0`. Look up the key name in `score()`. Then assert the same run with `bias_weight=0` (via `start_params`) shows a bias near +6 (3 per team × 2) scaled by possessions/100. That second assertion proves the correction is doing the work.

**Step 2:** Run it. If it fails, **stop and report**. A target with a different level must not be fed to live pricing until this holds.

**Step 3:** Commit: `git commit -am "test(nba): level correction absorbs a garbage-time target offset"`

### Task 12: Pre-registered evaluation (run only after data access is settled)

**Files:**
- Create: `docs/nba/phase2d-ctg-target.md`

**Decision rule, written before any result is seen.** Adopt the CTG target for live shadow pricing only if **all** of these hold on the scored window (2018-19 → latest season with CTG logs):
1. Paired log-loss gain of sim(CTG) over sim(ESPN) on home-win probability has a 95% interval above zero, both resampling **dates** and resampling **games** (existing `track/metrics.py` bootstrap).
2. Margin MAE and total MAE do not get worse (point estimate), and |total bias| ≤ 0.5.
3. On the SBRO overlap (2018-19 → 2022-23, 5,604 games), the market gap (sim − market log loss, currently +0.0210) **shrinks**, and its interval excludes the old point estimate.
4. Calibration ECE does not rise by more than 0.005.

Otherwise, record a **negative or null result** and leave the default as ESPN. No result in this document may be estimated, rounded up or written before the command has run.

**Step 1:** Data. The Drive hub holds **2025-26 only** (≈1,139 valid games), which allows only a one-season check: no SBRO overlap and no tuning seasons. Rules 1, 2 and 4 can run on 2025-26 as a **pilot**; rule 3 cannot. A full decision needs team game logs for 2016-17 → current (tuning needs 2016-17/2017-18), 30 teams per season, obtained per the Task 0 ruling and dropped into the `manual\` folder for `ctg-sync`. Label any pilot result "pilot, n = one season" and do not adopt on it.

**Step 2:** Run both arms with identical seeds and parameters:

```bash
python -m outlier_nba.cli backtest --no-tune --sbro
python -m outlier_nba.cli backtest --no-tune --sbro --ctg-store "%LOCALAPPDATA%\outlier_nba\ctg\ctg.sqlite3"
```

Run them `--no-tune` first, so the arms differ only in the target. Then run with tuning, because the target can change the optimal `lam`/`tau`.

**Step 3:** Write `phase2d-ctg-target.md`. Include the reconciliation counts per season, both arms' tables, the paired CIs, the decision against each rule, and every caveat: CTG possession definition, ESPN weights, unknown source and timing of the `Spread` column, and that CTG data is not stored in git (only aggregates).

**Step 4:** Commit: `git commit -m "docs(nba): CTG garbage-filtered target - pre-registered A/B result"`

### Task 13: Forward-captured season snapshots as diagnostics (optional, after Task 12)

Only if Task 0 allows ongoing manual exports. A weekly `FOURFACTORS` + `SUMMARY` import becomes a timestamped snapshot series. Add **diagnostic-only** columns to `team_ratings.csv` through `ratings_rows` in `team_run.py:187`: `ctg_net_raw`, `ctg_l2w_net_raw`, `ctg_asof`, `ctg_gp`. They are read with `snapshot_asof` for the slate date. Columns stay blank when no snapshot exists. **They change no probability.**

Test: a run with no CTG store produces byte-identical `team_shadow.csv`/`team_ratings.csv` field lists except for the new blank columns. A run with a snapshot dated on the slate day leaves them blank (as-of guard).

Later, the ledger joins these against `nba_model_eval` residuals to test the P2 shot-profile and variance hypotheses prospectively, under the same pre-registration discipline.

### Explicitly out of scope (and why)

* **Automated CTG login or scraping.** Not authorized by anything found (Part A).
* **On/off, lineups, positions.** M3 (availability) and M6/M7 (player model) do not exist yet; revisit with those modules. On/off must be shrunk toward zero by minutes (empirical-Bayes, prior variance fitted out of sample) before use.
* **Using CTG `Spread` as a closing line.** Book and timestamp unknown. A provenance check is a separate item.
* **Editing the shared registry** with city aliases (collision risk across leagues).

---

## Verification commands (whole plan)

```bash
pytest tests/test_nba_ctg.py tests/test_nba_ctg_store.py tests/test_nba_ctg_games.py tests/test_nba_ctg_sync.py tests/test_nba_ctg_reconcile.py -v
pytest tests -k nba -q
python -m ruff check outlier_nba tests/test_nba_ctg*.py
python -m mypy outlier_nba/ingest/ctg.py outlier_nba/ingest/ctg_reconcile.py
```

Expected: all new tests pass. The NBA suite count equals master's plus the new tests. Ruff reports no new findings, and mypy reports no new errors in the two new modules.
