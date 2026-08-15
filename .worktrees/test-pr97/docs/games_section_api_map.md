# Outlier "Games" Section — API Map & Pipeline Integration Plan

Recon captured live from `app.outlier.bet/WNBA/games` on 2026-06-22 (logged-in
session, Browser 1). All endpoints share base `https://api.outlier.bet` and the
**same `Authorization: Bearer <token>` auth your `OutlierApiClient` already uses**
— so no auth changes are needed to add these.

App nav surfaces (left rail): Insights, Popular, **Games**, Props, EV+, Boosts,
Arbitrage, Middle Bets. This doc covers **Games** and its sub-pages.

---

## 1. Endpoint inventory

| # | Endpoint | Params | Returns | In repo today? |
|---|----------|--------|---------|----------------|
| 1 | `GET /sportsdata/leagues/{LG}/schedule` | — | `events[]` (the slate) | ✅ `fetch_schedule` |
| 2 | `GET /sportsdata/events/{eventId}` | — | single event detail (home/away `teamId`, alias, venue, scheduledTime, status, live, season) | ❌ NEW |
| 3 | `GET /sportsdata/events/{eventId}/markets` | `marketType=` **required** | `markets[]` — the core game data | ❌ NEW |
| 4 | `GET /sportsdata/events/{eventId}/insights` | paginated (`nextPageToken`) | `insights[]` game-scoped | ❌ NEW (player insights are league-wide today) |
| 5 | `GET /sportsdata/events/{eventId}/matchup` | — | `matchup_type, lineups` (powers Team Rankings + starting lineups) | ❌ NEW |
| 6 | `GET /sportsdata/leagues/{LG}/teams/{teamId}/injuries` | — | `players[]` w/ `injury{status,headline,analysis,returnDate,...}` | ❌ NEW |
| — | `GET /sportsdata/markets/{marketId}` | — | per-market detail + history + `evOutcomes` | ✅ `fetch_market` (reuse for game-market line movement/EV) |

`marketType` is a hard filter — calling `/markets` with no `marketType` returns
**0 markets**. Confirmed values (each = one game-detail sub-tab):

| Sub-tab | `marketType` | Count (CHI@CON) | Propositions seen |
|---------|-------------|-----------------|-------------------|
| Gamelines | `GAMELINE` | 22 | `MONEYLINE`, `MONEYLINE_THREE_WAY`, `SPREAD`, `TOTAL`, `WINNING_MARGIN` |
| Player props | `PLAYER_PROP` | 52 | `POINTS`, `REBOUNDS`, `ASSISTS`, `POINTS_REBOUNDS`, `POINTS_ASSISTS`, `POINTS_REBOUNDS_ASSISTS`, `THREE_POINTERS`, `DOUBLE_DOUBLE` |
| Team props | `TEAM_PROP` | 8 | `POINTS` (team totals, full/2H/3Q/4Q) |
| Game props | `GAME_PROP` | 0 here | (empty for WNBA today; tab exists for other sports) |

`periodLabel` spans full-game (`null`), `2H`, `3Q`, `4Q`; `includeOvertime` flag
+ "(Exc. OT)" labels distinguish OT treatment.

**Two confirmed call facts:** (a) `marketType` alone is the working API filter — the
`marketGroup=GAMELINES` seen in the app URL is UI-only and not required. (b) `schedule`
returns a **multi-day** slate (the Games UI is date-tabbed: Today / next dates), so a
day's game count ≠ the schedule length — the captured 2026-06-22 WNBA "Today" tab
had **4 games** while `schedule` listed **8 events**. Date scoping is therefore a
build decision (§4.3, §5).

---

## 2. Market object schema (endpoint #3) — the high-value payload

Each element of `markets[]`:

```
marketId, eventId, leagueId, sport
marketType            # GAMELINE | PLAYER_PROP | TEAM_PROP | GAME_PROP
marketGroupId, marketGroupSortOrder
label                 # "Money Line", "Sky - 2nd Half Points (Exc. OT)", "Natasha Cloud - Points"
proposition           # MONEYLINE | SPREAD | TOTAL | POINTS | ...
propType              # STRAIGHT | TOTAL
periodLabel, periods[]  # null/2H/3Q/4Q
includeOvertime, isActive
books[]               # ~5 book identifiers quoting this market
publicMoney[]         # per side: {position, percentage=tickets%, money=handle%}  ← not in your feed
outcomeMap[], referenceOutcomeMap[]
outcomes[]:
    outcomeId, label, line, position    # OVER/UNDER/HOME/AWAY
    primary, bookPrimary
    books[]
    odds[]:  { book, american, decimal, fraction, identifier }   # PER-BOOK price ← line shopping
    stats:   { awaySummaryStat|homeSummaryStat: { curSeason, prevSeason, h2h, l5, l10, l20, l10Results } }
```

> **`publicMoney` confirmed** (2026-06-22, DAL@SEA Money Line, pregame): array of
> per-position entries `{ position, percentage, money }` where **`percentage` = % of
> bets/tickets** and **`money` = % of handle/$** (each side's pair sums to 100). Both
> are provided, so tickets-vs-handle is moot — capture both; the **divergence** is the
> signal (low `percentage` + high `money` on a side = sharp money). Populated only on
> pregame/upcoming **core** markets (empty once a game is live/final, and absent on
> deep alt markets). `outcomeMap`/`referenceOutcomeMap` element purpose remains
> unconfirmed (low priority).

**What this adds over the current league `playerProps` feed:**
- **Game markets** (ML / spread / total / winning margin, plus quarter & half splits) — entirely absent today.
- **Team totals** (`TEAM_PROP`) — absent today.
- **`publicMoney`** — per side `{ percentage = tickets %, money = handle % }`; **both**
  provided (the UI red/green bars), so bets-vs-handle divergence (sharp-side detection)
  is computable directly — no assumption needed.
- **Per-book `odds[]` arrays** — multiple books per outcome → real line shopping, best price, multi-book no-vig (vs the single-line `bestOdds`/`bookOdds` you store now).
- **Per-outcome `stats` splits** attached directly to the market outcome.
- **Injuries** + **matchup/team rankings** + **game-scoped insights** as joinable context.

---

## 3. Join spine (fits your existing model)

```
schedule.events[].eventId
   └─ event detail (#2)        → home.teamId / away.teamId
   └─ markets (#3, ×4 types)   → marketId → outcomes[].outcomeId   ← normalized row grain
   └─ insights (#4)            → marketOutcomeId  (= outcomes[].outcomeId)
   └─ matchup (#5)             → team rankings + lineups, keyed by teamId
   └─ teams/{teamId}/injuries  → playerId
```

`outcome_id` is the normalized row grain for game markets; `market_id` stays the
group/card key where one UI card contains multiple outcomes. `eventId` and `teamId`
become first-class join keys (today `eventId` rides inside `outcome`). Player
markets from #3 reconcile to your league `playerProps` by `outcomeId`.

---

> **Status (2026-06-23):** Implemented. The games path exists across
> `games.py`, `normalizer.py` (`normalize_games`), `line_movement.py`
> (`source="games"`), and `cards.py` (`build_game_cards_payload`). A 2026-06-23
> code review found and fixed five defects: per-book odds extraction from the
> Games `odds[]` shape (§2), team-side survival in games line-movement/EV (§4.6),
> game-card market identity, partial-failure surfacing in `games.py`, and a CLI
> status-report `NameError`. See `tests/test_games.py` and
> `tests/test_line_movement.py` for the regression coverage.
>
> Locked decisions: PLAYER_PROP = enrich-only (§4.5); scopes = metadata not aliases
> (§4.4); `publicMoney` = `{percentage=tickets%, money=handle%}` confirmed (§2);
> line-movement = full-game-only v1 (§4.6); `WINNING_MARGIN` = store-only, unranked
> (§4.7).

## 4. Proposed integration (gated on your GO)

Mirror the existing module pattern — additive only, player path untouched.

### 4.1 `api.py` — new fetchers
Add `fetch_event(event_id)`, `fetch_event_markets(event_id, market_type)`,
`fetch_event_insights(event_id)` (paginated like insights), `fetch_event_matchup(event_id)`,
`fetch_team_injuries(league, team_id)`. Reuse existing retry/gzip/pagination plumbing.
No auth change.

**Event-detail policy (P1):** `/events/{id}` returns the same `home`/`away`
(teamId, alias, name), `scheduledTime`, `venue`, `status`, `season` fields that
`schedule.events[]` already carries (verified in `discovery_latest.json`). So
the slate fan-out (§4.3) sources event/team context from `schedule` and does **not**
call `fetch_event` per event — keeping the budget flat. `fetch_event` is retained
only for a single-event refresh path (e.g. `games --event <id>`); not in the slate
loop.

### 4.2 Outcome-side taxonomy — fixes the OVER/UNDER-only assumption (P1)
`SIDES = ("OVER","UNDER")` is hardcoded in `line_movement.py:28`, the normalizer
gate `normalizer.py:312`, and the card buckets `cards.py:528` (`rows_by_side =
{"OVER","UNDER"}`). Game markets carry other `position` values, so add a **side
taxonomy** keyed off `proposition` — in the new game path only:

| Proposition | Positions |
|---|---|
| TOTAL (game/team) | OVER / UNDER |
| SPREAD | HOME / AWAY |
| MONEYLINE | HOME / AWAY |
| MONEYLINE_THREE_WAY | HOME / AWAY / DRAW |
| WINNING_MARGIN | N-way: one outcome per margin bucket |

Do **not** widen the existing `SIDES` constant (it would leak into the player
pipeline). Add a `game_sides(proposition)` helper instead. `position` is descriptive
metadata on each row — it is **not** the row key (see the row grain in §4.4 / §4.9,
which keys on `outcome_id` so alt lines and N-way margin buckets are never collapsed).

### 4.3 `games.py` (new CLI) — fetch + raw
Enumerate `schedule.events` → per event fan out the 4 `marketType` calls + matchup
+ insights; fetch injuries **once per unique `teamId`** across the slate (dedupe).
Write `data/<LG>/raw/<lg>_games_raw_*.json`.

**Date + status scoping (P1):** `schedule` is multi-day, so filter `events` by
`scheduledTime` local date **before** fanning out — default **today**, with a
`--date` / `--days N` override. Confirmed `status` values: **`pregame`**, **`live`**
(final once complete); events also carry a `code` (`REG`, `CC` = Commissioner's Cup).
Default to **`pregame`** events — verified 2026-06-22 that a `live` game's `/markets`
returns **0 markets** and `publicMoney` only populates pregame, so live/final games
yield nothing useful for pre-bet signal. `live` may be opted in (flagged) for
in-game; this filter is the main budget lever alongside full-game-only (§5).

### 4.4 `normalizer.py` — game rows + scope-as-metadata (P2)
New `normalize_games()` — do **not** reuse the player `normalize_props` OVER/UNDER
gate (`normalizer.py:312`). **One row per outcome**, primary grain
`league + event_id + market_id + outcome_id` (fallback `position + line + label`
only when `outcome_id` is missing) so alt lines and N-way `WINNING_MARGIN` buckets
are never collapsed. Each row carries `event_id`, `market_id`, `outcome_id`,
`proposition`, `position`, `line`, per-book `odds[]`, `public_money`, `stats`, plus
**scope metadata** `scope`, `period_label`, `periods`, `include_overtime`. Quarter/half scopes are preserved as metadata exactly like the
existing `detect_scope` pattern (`normalizer.py:321-324`, `:371`) — **not**
canonicalized into `*_MARKET_ALIASES` (`registry.py:135`). Display/grouping keys use
`(proposition, period_label, include_overtime)`, but row identity remains
`outcome_id`-first.

**Team identity (build note) — differs by `marketType`:**
- `GAMELINE` spread/ML: `position` is HOME/AWAY → map directly to the event's
  `home`/`away` `teamId`.
- `TEAM_PROP`: outcomes are **OVER/UNDER** (a team total), so `position` does **not**
  name the team. Resolve in priority order: (1) a market/outcome `teamId` if the
  payload carries one — **confirm at build**, it was not present in the captured
  `GAMELINE` sample; else (2) parse the team token from the market `label`
  ("Connecticut Sun - Points", "Sky - 2nd Half Points") and match it against **this
  event's** `home`/`away` `name`/`alias`/`market` (per-event local match). Do **not**
  rely on global `WNBA_TEAM_ALIASES` (`registry.py:29`) — it has full names/tricodes
  but not bare "Sun"/"Sky"/"Wings", so a global lookup would silently miss team totals.

**Output envelope:** `*_games_latest.json` = `{ generated_at, league, records[],
context{} }`. The `records[]` envelope matches props so existing `cards.py`
loaders (`load_latest`, `_records`) read it unchanged; `context{}` is per §4.9.

### 4.5 PLAYER_PROP enrich-only — explicit output contract (P2)
A separate games file does not reach existing player cards on its own: props rows
hold `outcome_id` under `sport_context.outcome_id` (`normalizer.py:372`) and
`cards.py` builds from `props_by_market` directly. So enrich-only means an
**enrichment map**, not new player rows:
- `games.py` emits `<lg>_games_enrichment_latest.json` = `{ outcome_id: { per_book_odds[], public_money } }`, built from the per-event `PLAYER_PROP` markets.
- `cards.py` loads it alongside props; in `assemble_card` each side already exposes its `outcome_id` (`cards.py:564`), so look it up and attach `per_book_odds`/`public_money` to that side view.
- No new player `market_id`s enter the card set (`market_ids`, `cards.py:707`) → zero double-count.
- **Enrichment is optional, and player-card changes are additive-only (P1):** the
  map is loaded only if present.
  - **Absent** `*_games_enrichment_latest.json` ⇒ `cards --league` runs and
    `cards_latest.json` is **byte-identical** to today (regression guard).
  - **Present** ⇒ each player side view gains **additive, nullable** fields
    (`per_book_odds`, `public_money`); the card set, `market_id` grain, ranking, board
    routing, and every existing field are unchanged. So "player cards unchanged" means
    no change to existing structure — **not** literal byte-identity when enrichment is
    present. (This resolves the earlier wording that claimed byte-identity even with
    games inputs.)

### 4.6 Line movement for game markets — source + side identity (P1)
`line_movement.py` reads IDs only from `*_props_latest.json`
(`_ordered_market_ids`, `:73`) and keeps only OVER/UNDER via `_side_token` (`:68`).
Two explicit choices before coding:
1. **ID source:** add a `--source games` mode (or a sibling `game_line_movement`
   pass) that pulls **unique** `market_id`s from `*_games_latest.json` instead of
   props. The games file is outcome-grain, so dedupe market IDs before detail calls
   and keep representative context by `market_id`.
2. **Side identity:** in that path, replace the OVER/UNDER `_side_token`/`SIDES`
   filter with the §4.2 taxonomy so HOME/AWAY/DRAW/margin outcomes keep identity
   instead of being dropped.
Game-market EV still comes only from the `/sportsdata/markets/{id}` `evOutcomes`
field (same as today); `/events/.../markets` exposes `publicMoney` but no EV. This
market-detail pass is high-cost and should be opt-in separately from the base games
fetch.

**v1 scope — DECIDED (2026-06-22): full-game only.** The detail pass covers only
full-game core markets (Money Line, Spread, Total, full-game team totals). Quarter/
half scopes are still fetched and normalized into `*_games_latest.json`, but are
**not** sent through `/markets/{id}` in v1 — cutting the detail budget from ~30 to
~5-8 markets/event. Scoped detail can be enabled later behind a flag.

### 4.7 `cards.py` / `cards_html.py` — game board is a separate builder (P1)
The game board is **not** shoehorned into the OVER/UNDER `assemble_card` path; it is
a separate builder with explicitly defined inputs:
- required input: `*_games_latest.json` (game/team markets).
- optional input: the widened **game** line-movement file (§4.6, keyed by game
  `market_id`) for movement/EV fields when the high-cost detail pass has run.
- the player-only `*_games_enrichment_latest.json` (§4.5) is not a game-board input;
  it enriches existing player cards only.
- emits Game-board cards at ML/spread/total/team-total grain. Per-book no-vig +
  `public_money` (tickets%/handle% + their divergence) are **signal** only (never EV,
  per the Board A/B rule); EV appears only if the optional game `/markets/{id}` detail
  pass returns `evOutcomes`.
- **`WINNING_MARGIN` is stored in `*_games_latest.json` but EXCLUDED from the ranked
  board** (DECIDED 2026-06-22: high-vig N-way exotic, kept as data only).
- **output (mirrors the existing exporter, P2):** following
  `export_cards_for_league` (`cards.py:768-794`), write
  `data/<LG>/cards/{lg}_games_cards_latest.json`, a timestamped archive
  (`paths.timestamped(cards_dir, "games_cards")`), `{lg}_games_cards_latest.html`,
  and `reports/games_cards_status_latest.json` (coverage / missing_feeds). It is a
  **separate** file set — never merged into the player `cards_latest.json`.
- the game-board builder does **not** touch the player card builder. The only
  optional change to player cards is the §4.5 enrichment — a distinct mechanism
  (additive nullable side-view fields inside `build_cards_payload`), not this builder.

### 4.8 `registry.py` + `refresh.py` — config & orchestration
`registry.py`: add `games_route` and a `GAME_MARKET_TYPES` tuple. Add only **true
alias** entries (e.g. `WINNING_MARGIN`, three-way ML) to `*_MARKET_ALIASES` — scope
is handled as metadata per §4.4, not as aliases. `refresh.py`: add `--games`
(fetch → normalize → game cards from event-market data) and a separate
`--game-line-movement` / `--games-with-detail` flag for the high-cost market-detail
EV/movement pass. Keep all games work independent of the player path so a games
failure cannot break player cards.

**`--all` semantics (P2 — decision):** `--all` today expands to props/insights/
line-movement/cards (`refresh.py:18,30-34`). Given the corrected games budget (§5),
games stays opt-in and is **not** folded into player `--all`. Base `--games` runs
the lower-cost date-scoped event-market path; `--game-line-movement` (or equivalent)
opts into the detail phase. A future `--games-all` may bundle base games + detail,
but the default `--all` cost profile is unchanged.

### 4.9 Normalized context — matchup, lineups, injuries (P2)
`fetch_event_matchup` and `fetch_team_injuries` are fetched but were not contracted
into normalized output. They do **not** become market rows. `normalize_games()`
emits a sibling **context** object alongside `records[]` in `*_games_latest.json`:

```
context: {
  events:  { <event_id>: { matchup_type, team_rankings[], lineups[] } },   # from /events/{id}/matchup
  teams:   { <team_id>:  { injuries[]: { player_id, name, position, status,
                                         headline, return_date } } },        # from teams/{teamId}/injuries
  insights:{ <event_id>: [ <event-scoped insight> ] }                       # from /events/{id}/insights
}
```

Game-board cards reference these by `event_id` / `team_id` as **context fields**
(non-ranking display + flags such as `key_injury`), never as EV or signal inputs
unless a future scoring rule opts in. Empty arrays are valid (no injuries / lineups
not posted).

### 4.10 Implementation contract (consolidated — what coding must honor)
- **Row grain:** `league + event_id + market_id + outcome_id`; fallback
  `position + line + label` only when `outcome_id` is null. `position` is metadata,
  not a key (§4.2, §4.4).
- **Event-detail policy:** slate fan-out uses `schedule` for event/team context;
  `fetch_event` is single-event-refresh only — **+0 calls** to the slate budget (§4.1).
- **Date/status scoping:** filter `schedule.events` by `scheduledTime` date (default
  today; `--date`/`--days` override) and skip final events before fan-out (§4.3).
- **Game detail phase:** dedupe unique `market_id`s from the outcome-grain games file
  before `/markets/{id}` calls; keep detail/EV opt-in, not part of base `--games`
  (§4.6, §4.8).
- **Optional enrichment (additive-only):** absent enrichment ⇒ `cards_latest.json`
  byte-identical; present ⇒ player side views gain only additive nullable
  `per_book_odds`/`public_money`, existing structure/grain/ranking unchanged (§4.5).
- **Normalized context:** matchup/lineups/injuries/event-insights land in
  `context{}`, not `records[]` (§4.9).
- **Game-board output:** separate file set `{lg}_games_cards_latest.json` + archive +
  `.html` + `reports/games_cards_status_latest.json`, mirroring `export_cards_for_league`
  (`cards.py:768-794`); player `cards_latest.json` is never written by this builder (§4.7).
- **Team identity:** `GAMELINE` → `position` HOME/AWAY = event `home`/`away` `teamId`;
  `TEAM_PROP` (OVER/UNDER) → market/outcome `teamId` if present, else per-event
  `label`↔home/away name match — never global aliases (§4.4).
- **`--all` behavior:** games is opt-in via `--games`; not in `--all` (§4.8).
- **Scope:** preserved as metadata, never added to `*_MARKET_ALIASES` (§4.4).
- **Required tests (pytest, fixtures from captured 2026-06-22 payloads):**
  1. `normalize_games` keeps alt lines + N-way `WINNING_MARGIN` distinct (grain test).
  2. `game_sides()` returns correct positions per proposition incl. 3-way ML.
  3. Scope metadata round-trips; no scoped market collapses into a full-game alias.
  4. Enrichment join attaches by `outcome_id`; **absent enrichment file ⇒ cards
     succeed unchanged** (regression guard for player path).
  5. Game line-movement dedupes market IDs from the outcome-grain games file and
     retains HOME/AWAY/DRAW side identity (no silent drops).
  6. `context{}` carries injuries/matchup; empty arrays handled without error.
  7. `TEAM_PROP` (OVER/UNDER) resolves to the right team via market `teamId` or
     per-event `label`↔home/away match — without global `*_TEAM_ALIASES` nicknames.
  8a. Game cards write `{lg}_games_cards_latest.json` + archive + status; the
     game-board builder never writes/edits player `cards_latest.json`.
  8b. Player `cards_latest.json` is byte-identical when enrichment is **absent**; when
     present, the only diff is the additive nullable side-view fields.

---

## 5. Call budget (corrected) & remaining open questions

**Budget — per event, then by scope** (the earlier ~64 estimate both omitted the
market-detail phase and conflated `schedule`'s 8 multi-day events with the day's slate):
- Per event, event-level: 4 `marketType` + matchup + insights = **6 calls**; injuries
  fetched once per unique `teamId` (2 per game, deduped across the slate).
- Per event, market-detail (**full-game-only v1, decided** §4.6): ~5-8 core markets
  (ML, spread, total, full-game team totals) → **~6 calls** (was ~30 with all scopes).
- **Captured 2026-06-22 Today tab (4 games; default scope = run date, pregame only):**
  event-level ≈ 4×6 + ~8 injuries ≈ **~32 calls**; + full-game detail ≈ 4×6 ≈ **~24**;
  **≈ ~56 total** (vs ~150 if all scopes). Live/final events drop out (no markets), so
  the practical count is usually lower.
- **Full 8-event `schedule` pull, full-game-only:** ≈ ~58 event-level + ~50 detail ≈ **~110**.
- Levers: today/`--date` + pregame-status scoping (§4.3), full-game-only (decided),
  `--limit`/`--workers`, and the existing 403 cooldown.

**Resolved by the §4 revision** (were P1/P2 review gaps):
- Outcome sides beyond OVER/UNDER → §4.2 taxonomy.
- Scope as metadata, not aliases → §4.4.
- PLAYER_PROP enrich-only output contract → §4.5 enrichment map.
- Line-movement ID source + side identity for game markets → §4.6.
- Game-board input/model path → §4.7 separate builder.
- Multi-day `schedule` vs per-day slate → date/status scoping, default today → §4.3.

**Decided (2026-06-22, with Daniel + confirmed against live payload):**
- **`publicMoney`:** shape confirmed `{position, percentage=tickets%, money=handle%}`;
  capture both, signal = bets-vs-handle divergence (§2). No assumption needed.
- **Line-movement scope:** **full-game only** for v1 (ML/spread/total/full-game team
  totals); quarter/half normalized + stored but not detail-fetched (§4.6). Biggest
  budget cut; scoped behind a later flag.
- **`WINNING_MARGIN`:** **store only**, excluded from the ranked Board A/B (high-vig
  N-way exotic); kept as data (§4.7).

**Still open — confirm at build time:**
- **Empty groups:** `GAME_PROP` returns 200-with-0-markets for WNBA today — treat
  as normal, not an error.
- **`TEAM_PROP` team source:** confirm whether team-prop markets/outcomes carry a
  `teamId`; if not, per-event `label`↔home/away matching is the fallback (§4.4).
- **`outcomeMap`/`referenceOutcomeMap`:** element purpose unconfirmed (low priority).

---

## 6. Raw IDs captured (today's WNBA slate, for reference)

- Sample event: `538113f5783215e84c55b97eb25bf11937bd6446` (Chicago Sky @ Connecticut Sun, 7:00 PM)
- Captured 2026-06-22 "Today" tab: **4 games** — CHI@CON, TOR@ATL, PHX@IND, DAL@SEA.
- `schedule` endpoint returned **8 events** total — multi-day (Games UI date tabs:
  Today / Jun 23 / Jun 24 / Jun 30), so schedule length ≠ per-day game count.
