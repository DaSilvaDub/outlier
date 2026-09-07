# Original User Request

## 2026-09-06T15:20:45Z

# Teamwork Project Prompt — NCAAF Props & Insights (v3)

> Status: Launched.
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full Team

Extend the **existing** `cfb-analytics` NCAA football pipeline to ingest Outlier **player props, team props, and insights**, alongside the gameline odds it already collects.

**Working directory:** Create a separate `git worktree` from `C:\Users\dasil\Dev\GitHub\cfb-analytics` so you do not interfere with the user's active checkout.
**Integrity mode:** benchmark (strict adherence to existing repo architecture, no external AI/paid paths)
**Branch:** checkout the existing `feat/outlier-props-insights` branch. Never commit to `master` directly.
**Deliverable:** one PR against `cfb-analytics` `master`.

---

## 🚨 CRITICAL: DO NOT TOUCH THE ELO PROJECT CODES 🚨
The user has recently completed a massive internal Elo engine build on `master`. **DO NOT modify, edit, or interfere with any Elo models, the CFBD backfill, the walk-forward backtest harnesses, or any core modelling math.** Keep your props and insights work strictly isolated to its own ingestion and schema layers.

---

## Why this changed from v1 (read before starting)

The NCAAF integration ships as a separate repo, `cfb-analytics`, importing nothing from `outlier`. The Outlier league token is `NCAAFB`, not `NCAAF` (which returns HTTP 502). The `cfb-analytics` repo already exists with SQLite ledgers, migrations, and gamelines. What is genuinely unbuilt is props and insights.

---

## Requirements

### R1. Discovery spike — a hard gate, before any schema or parsing work

Nothing below R1 may be designed until R1 is answered with recorded evidence. Using the existing authenticated session, read-only, against a slate inside the operating window (within ~5 days of kickoff):

- Which `marketType` tokens does `/sportsdata/events/{eventId}/markets?marketType=…` accept for `NCAAFB`? Probe at minimum `PLAYER_PROP`, `TEAM_PROP`, `GAME_PROP`, and confirm `GAMELINE` still behaves as documented.
- For every token that resolves: what `proposition` values appear, at what frequency, and how many distinct books price each? Report median books per game per proposition.
- Is there a player identity field on prop outcomes, and is it a stable `playerId` or a display name?
- Does an insights endpoint exist for `NCAAFB`? If it 404s or 403s, say so and stop — do not synthesise an insights feed.

Commit the probe script and payloads under `tests/fixtures/`, and write findings into `docs/probes/` as a dated markdown file. If a prop family is not actually offered, report that rather than building a mapping.

*(Note: The previous teamwork run already completed this and committed the findings to `feat/outlier-props-insights`. Review those findings before proceeding!)*

### R2. Target Markets, Whitelist, and Side Restrictions

Use the repository's existing `market_type` and `market` fields. Define this explicit whitelist as module-level frozensets before ingestion is wired up:

| Requested market | `market_type` | `market` | `scope` | Allowed sides |
|---|---|---|---|---|
| Full-game spread | `GAMELINE` | `SPREAD` | `full_game` | `HOME`, `AWAY` |
| First-half spread | `GAMELINE` | `SPREAD` | `first_half` | `HOME`, `AWAY` |
| Full-game total points | `GAMELINE` | `TOTAL` | `full_game` | `OVER`, `UNDER` |
| Moneyline | `GAMELINE` | `ML` | `full_game` | `HOME`, `AWAY` |
| Team total points | `TEAM_PROP` | `POINTS` | `full_game` | `OVER`, `UNDER` |
| Team offensive yards | `TEAM_PROP` | `OFFENSIVE_YARDS` | `full_game` | `OVER`, `UNDER` |
| Team receiving yards | `TEAM_PROP` | `RECEIVING_YARDS` | `full_game` | `OVER`, `UNDER` |
| Team rushing yards | `TEAM_PROP` | `RUSHING_YARDS` | `full_game` | `OVER`, `UNDER` |

*Note: These aliases become authoritative only after confirmation in R1. Explicitly out of scope: NCAAF player props, quarters, derivative spreads, executable plays, and bet sizing.*

### R3. Schema & Integrity

- `odds_snapshots` admits props via a **table-rebuild migration** (version `11` or whatever is next available in `cfb_analytics/db.py`). Existing rows must survive with identical `snapshot_id`s.
- Decide explicitly whether to widen `odds_snapshots`/`market_consensus` with a nullable `player_id`, or use separate `prop_odds_snapshots`/`prop_consensus` tables. Do not overload existing tables by accident.
- `snapshot_id` must remain deterministic for idempotency.

### R4. Parsing

Extend `cfb_analytics/sources/outlier.py`. Respect two documented traps (and prove with fixtures):
1. `outcomes[].books` is **not** parallel to `outcomes[].odds`. Book is read from inside each odds entry.
2. One proposition spans several market rows per event. Consensus must union all rows and de-duplicate.

### R5. Ingestion & Reporting

- Thread props and insights through `ingest_slate()` behind explicit flags (`with_props`, `with_insights`), defaulting **off**.
- Degrade gracefully: one event's prop fetch failing must not lose the rest. Add failures to `source_health` and `IngestSummary`.
- Apply a `min_books_for_consensus = 3` floor to any prop consensus.

### R6. Constraints & Methodology

- **No external AI/paid reasoning:** No `run_desk`, no OpenAI/Gemini/Claude calls. 
- **Code constraints:** Nothing imported from `outlier` or `nba-props-pipeline`. Stdlib-only for all modelling math (no pandas/numpy).
- **TDD:** Write failing tests first against committed fixtures (`CFB_HTTP_MODE=replay`), then implement. Tests never reach the network.
- **Date logic:** Prefer earliest date from today through 14 days ahead with non-final NCAAF events. Fallback to past 14 days if needed.

## Acceptance Criteria

### Discovery
- [ ] R1 findings committed (`docs/probes/` and `tests/fixtures/`) stating HTTP status, propositions, and median books for each probed token.

### Correctness & Logic
- [ ] Fixture proves parser attributes prices to the correct book despite `outcomes[].books` ordering.
- [ ] Fixture proves proposition prices are unioned across multiple market rows and de-duplicate.
- [ ] Markets outside the explicit whitelist (R2) are dropped at ingestion.
- [ ] Home and away team props map correctly.
- [ ] Missing/empty prop endpoints degrade cleanly without crashing gameline ingestion.

### Migration & Integrity
- [ ] Migration 11 (or next available) applies cleanly; pre-existing rows keep original `snapshot_id`s.
- [ ] Re-running ingestion is idempotent (no duplicate rows).

### Regression & Quality
- [ ] Existing test suite passes unchanged. No test edited to accommodate props without prose explanation.
- [ ] `ruff check .`, `mypy cfb_analytics`, and `pyright` run cleanly.
- [ ] `pytest --cov=cfb_analytics` achieves **>=80% coverage on every module touched**.
- [ ] `ingest --date <slate> --with-props` produces a summary giving events, games written, prop rows by family, distinct books, and failures.

---
## Notes for the team
- Use the reference slate `2026-09-05` for realistic fixtures (30 games, 12 books, ~630 prices).
- Read conventions in `README.md` and module docstrings before assuming a house style.
- If R1 shows Outlier offers no usable NCAAFB props, **say so and stop.** Truthful negative findings close this project successfully.
