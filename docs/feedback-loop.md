# Feedback-loop ledger and calibration tool

The feedback tool turns dated packs into a permanent, gradeable dataset. The
ledger and reports are fully local and deterministic. The daily job also uses
the read-only MLB Stats API and ESPN WNBA results feed to grade completed
events; it never calls a reasoning model for settlement.

## What is captured automatically

`python -m outlier_scrapers.pack` now writes `opportunities.csv` before the
top-N ranking step and then captures the pack into
`calibration/feedback.sqlite3`. The database is local/ignored and contains:

- `market_snapshots`: every pregame opportunity, including non-selected rows,
  the final pack selection state, exact outcome/line identity, probabilities,
  edge, book, data-quality flags, and the four Board B components.
- `decisions`: one decision per market snapshot, seeded as `PLAY` or
  `STAND_DOWN`. After desk orchestration, the daily job projects A/B/C/D and
  the E-or-fallback final verdict from the authoritative `desk_snapshot.json`
  into the exact pack-membership-backed decision. A later price/time snapshot gets its own decision
  instead of rewriting the earlier verdict history. There is no `E_verdict`
  column — pass E (or the no-E fallback) is the desk report, not a fifth
  scalar on this row.
- `pack_snapshot_memberships`: immutable dated-pack membership for each quote,
  keyed by a pack-capture fingerprint plus `snapshot_id`. It preserves the
  pack path, pack timestamp, seeded verdict, units, and selection/actionability
  state when the same source quote is reused in a later pack. The legacy
  `market_snapshots.pack_path` remains the first-seen path and is never reassigned.
- `parlay_legs` (child of `decisions`): the ordered legs of a captured parlay,
  each linked to the leg's own `market_snapshots` row. Primary key
  `(parlay_decision_id, leg_index)`. This is what lets a parlay settle from its
  legs; a recovered ledger that lost it could never settle one again, so
  `recover_corrupted_database` restores it alongside the other tables.
- `verdict_records` (child of `decisions`): one row per structured envelope
  record, primary key `(decision_id, pass, publication_id, record_id)`. This is
  where C's multiple findings per `outcome_id` and forced-rerun publications
  live. Legacy `C_verdict` on `decisions` is a lossy reduction
  (`CONTRADICTS` > `CONFIRMS` > `NEUTRAL`, else empty). Schema and helpers:
  `outlier_scrapers/verdict_store.py`. The coherent set of publications to
  persist is `packs/<date>/verdicts/desk_snapshot.json`, never a pass-level
  `current.json` that may have moved on. Plan:
  [2026-08-12-structured-ai-verdicts.md](plans/2026-08-12-structured-ai-verdicts.md)
  (Verdict persistence, The desk-level snapshot).
- `settlements`: the result, closing line/price, CLV, PnL, and would-have result
  joined to a decision/snapshot.

Repeated capture of the same source timestamp/line/price is idempotent. A new
line, outcome, source timestamp, price, or book creates a new snapshot and a new
seeded decision. Pack files are staged while the SQLite transaction remains
open, then swapped into the dated directory and committed together. Capture or
publication failure rolls back the ledger and restores the prior published pack.
Before a final verdict or settlement, recapture may repair fields on an identical
snapshot (for example, after a schema-semantics migration). Once either exists,
both the prediction snapshot and seeded pipeline decision are frozen so grading
history cannot be rewritten after outcomes are known.

Schema v5 adds pack-capture membership. Existing ledgers are backfilled once
from their first-seen snapshot path; future pack rebuilds retain distinct
vintages using the dated pack manifest timestamp.

Schema v3 includes a one-time, auditable conversion of legacy schema-v2 game
and team-total rows from conditional win probability to unconditional `P(win)`.
It runs before the finalization freeze, recomputes expected-return edge, marks
the migrated rows, and cannot be applied twice.

Use `--no-feedback-ledger` only for an explicit pack-only diagnostic. Use
`--feedback-db <path>` to override the default database.

## Probability and edge semantics

- `market_consensus_prob` is the existing no-vig market-derived probability.
  For a market with known push mass it stores unconditional `P(win)`; two-way
  totals ladder probability is multiplied by `(1 - push_prob)` before storage.
- `independent_model_prob` is intentionally blank until a real independent
  model supplies it.
- `final_blended_prob` currently equals the market-consensus probability. It is
  a separate permanent column so a future blend can be evaluated without
  rewriting history.
- `edge` preserves the pipeline's expected-return edge as a decimal; `0.07`
  means 7% expected return. It is not probability minus implied probability.

`push_prob` is stored with every snapshot. Reports calculate Brier score, log
loss, expected-versus-actual hit rate, and calibration curves separately for
all three probability columns using `P(win | not push) = p_win / (1 - p_push)`.
Rows with unknown push mass and actual pushes are excluded from binary scoring,
but pushes remain in ROI/result tables.

## Daily/post-slate workflow

The pack step captures snapshots and seeds decisions automatically:

```powershell
python -m outlier_scrapers.pack --leagues MLB,WNBA
```

After completing the desk, fill the pack's `decisions.csv` and import it:

```powershell
python -m outlier_scrapers.feedback decisions --input packs/2026-07-13/decisions.csv
```

Generate an empty settlement template when integrating a result/close feed:

```powershell
python -m outlier_scrapers.feedback templates --output calibration/exports/templates
```

Import the post-slate settlement file. `clv_line`, `clv_price`, and `pnl` may be
blank; the tool computes them when the decision snapshot and closing values are
available:

```powershell
python -m outlier_scrapers.feedback settle --input settlements_2026-07-13.csv
```

The daily job first queries completed MLB/WNBA scoreboards and box scores for
unsettled ledger rows from the prior three days. It requires final event status,
matches game markets by date plus both team aliases, and matches player props by
date plus an exact normalized player name. Ambiguous or unsupported rows remain
unsettled. Supported markets are game moneylines/spreads/totals, team totals,
WNBA standard full-game box-score markets and their supported combinations, and
the MLB player-prop whitelist. The latest locally captured pregame line and
price are used as the close when available. Generated audit CSVs are written to
`calibration/settlements/generated/`.

Run the collector independently with:

```powershell
python -m outlier_scrapers.results --leagues MLB,WNBA --lookback-days 3
```

The daily job also imports every `*.csv` placed in
`calibration/settlements/inbox/`. A fully matched file moves to
`calibration/settlements/processed/`; files with unmatched or ambiguous wager
identity remain in the inbox for repair. Malformed settlement input fails the
daily job closed. Use `--skip-settlement-ingest` only for an explicit diagnostic.

Alternate spreads, totals, and player props are captured through the unified
shadow artifact described in [ultimate-alt.md](ultimate-alt.md). Both qualified
and rejected rows enter the ledger, allowing play-versus-stand-down evaluation.

Then generate the complete report suite:

```powershell
python -m outlier_scrapers.feedback report
```

The `--db` option precedes the subcommand when overriding the database:

```powershell
python -m outlier_scrapers.feedback --db D:\ledger.sqlite3 report --output D:\feedback-report
```

## Settlement identity and calculations

The requested settlement fields remain present. The template adds
`settlement_id`, `decision_id`, `snapshot_id`, and `outcome_id` because
`event_id + market_id` is not unique when a market bundles alternate lines or
opposing outcomes. If a settlement matches multiple decisions, import fails
closed until one of those identifiers is supplied.

Positive line CLV means the selected side beat the close:

- OVER: `closing_line - taken_line`
- UNDER: `taken_line - closing_line`
- spread/team side: `taken_line - closing_line`

Price CLV is `taken_decimal / closing_decimal - 1`. PnL is computed from the
recorded units and taken decimal price when the input PnL is blank. Stand-down
PnL is zero; `would_have_result` and flat one-unit counterfactual PnL show
whether the pass saved or cost a bet.

## Generated report files

`calibration/reports/latest/` contains:

- `report.md` and `summary.json`
- `probability_metrics.csv`, `calibration_curves.csv`, and
  `expected_vs_actual.csv`
- `market_type.csv`, `pack_type.csv`, `edge_buckets.csv`, `odds_ranges.csv`,
  `books.csv`, `leagues.csv`, and `signal_flags.csv`. `pack_type.csv` splits the
  same settled rows by the pack lane they came from, using the capture `source`
  on `pack_snapshot_memberships` when present and the settlement selection
  grammar otherwise, so a lane running cold cannot hide inside an aggregate.
- `play_vs_stand_down.csv`, `decision_coverage.csv`, and `model_performance.csv`.
  ROI and hit-rate tables are settled-only; `decision_coverage.csv` separately
  reports total, settled, unsettled, and missing-event-time PLAY counts.
- `missing_edge_diagnostics.csv`. The same diagnostic is embedded in
  `summary.json`; it reports settled rows with a null edge and groups likely
  missing inputs, including `GAMELINE` rows with blank `model_prob_source`.
  It never backfills or estimates an edge.
- `ledgers/market_snapshots.csv`, `ledgers/decisions.csv`, and
  `ledgers/settlements.csv`, plus `ledgers/pack_snapshot_memberships.csv`

The report is useful immediately as a market-derived baseline. Learned Board B
weights and an independent probability model should be fit only after the
settled sample is large enough and should be validated out of sample.

## Per-pack accuracy audit

`market_type.csv` answers "is the desk calibrated" across the whole ledger.
`python -m outlier_scrapers.pack_accuracy` answers the narrower daily question:
how did *this* pack do, lane by lane, and which of its lanes are not measured at
all.

```bash
python -m outlier_scrapers.pack_accuracy --date 2026-08-25 --db calibration/feedback.sqlite3
python -m outlier_scrapers.pack_accuracy --pack packs/2026-08-25 --output calibration/reports/pack_accuracy/2026-08-25
```

It writes `report.md`, `pack_type_accuracy.csv`, `lane_coverage.csv`, and
`summary.json`. Two things it deliberately does *not* do:

- It never grades anything itself. Settlement stays in
  `outlier_scrapers.results`; this audit only reads what that collector wrote,
  so a pack type can never disagree with how its rows were settled.
- It never reports an unmeasured lane as a clean sheet. A lane that a pack
  generated but that has no ledger capture path is reported as `NO_COVERAGE`
  with a warning, because "0 rows captured" and "0-for-0" are indistinguishable
  in every other report. `--fail-on-gap` exits non-zero when any such lane has
  rows, which is the check to wire into a nightly job.

Lane coverage today: `opportunities`/`candidates`, `game_totals`, `team_totals`,
`ultimate_alt`, and every alt board — `alt_player_props`, `alt_team_totals`,
`{mlb,wnba}_alt_spreads`, and `{mlb,wnba}_alt_bankroll_props` — reach the ledger.
The capture `source` is the CSV's own stem, so the MLB and WNBA boards stay
distinguishable in `lane_coverage.csv` while both roll up to one pack type.

The `*_parlays` lanes are captured and settled too — see **Parlay settlement**
below. Every lane a pack generates now has a capture path, so any
`NO_COVERAGE` row in `lane_coverage.csv` is a real regression rather than a
known limitation.

### How alt boards are captured

The alt boards were written for human reading: they carry `player` / `team` /
`market` / `position` columns, not the selection grammar `results._grade_row`
parses. `feedback._alt_selection` builds that grammar per lane:

| lane | selection built |
| --- | --- |
| `alt_player_props` | `{player} - {market} {side} {line}` |
| `alt_team_totals` | `{team} Team Total {side} {line}` |
| `*_alt_spreads` | `{matchup} Spread {HOME\|AWAY} {signed_line}` |
| `*_alt_bankroll_props` | `{matchup} Money Line {HOME\|AWAY}`, `{matchup} Spread ...`, `{matchup} Total O/U {side} {line}`, or `{team} Team Total {side} {line}` |

Four rules keep the capture honest:

- **A row that cannot be expressed in that grammar is skipped, not stored.** No
  side, no line, no matchup means no snapshot — a captured row that can never
  settle is worse than an absent one, because it reads as pending forever.
- **A team-total selection is only built for a proposition that *is* the team's
  score** (`team_totals.is_team_total_proposition`). A team-hits or team-walks
  prop would otherwise be graded against runs and silently marked wrong.
- **An alt ladder keeps only its `is_best_line` rung selected.** The other rungs
  are captured unselected, exactly as non-selected opportunities are, so a lane's
  hit rate is not inflated by counting every rung of one wager.
- **An alt total that duplicates a `game_totals`/`team_totals` row at the same
  event/market/line/side is dropped.** That is one wager, not two.

The boards also spell the American price three ways (`price`, `best_price`,
`best_odds`); all three are read, because a snapshot with no price cannot
compute PnL when it wins.

One related fix went in with the lanes: `results._grade_row` used to require a
numeric line before grading anything, which meant **no moneyline ever settled** —
a moneyline has no line. The line is now required only by the branches that
actually use one (player props, totals, team totals, spreads).

### Parlay settlement

A parlay is never graded against a game. Every parlay leg is drawn from a
singles board that capture already stores, so a leg resolves to a snapshot that
`outlier_scrapers.results` grades through the ordinary single-row path; the
parlay itself settles by combining what its legs did.

Each parlay writer emits a `legs_json` column carrying the
event/market/outcome ids of its legs, alongside the existing human-readable
`legs` / `leg_N_*` columns (those are display text and are left untouched).
Capture runs in two phases — singles first, then parlays — so each leg resolves
to the snapshot the same capture just created, recorded in the `parlay_legs`
table. Settlement is `feedback.settle_parlays`, wired into `nightly_audit.ps1`:

```bash
python -m outlier_scrapers.feedback --db calibration/feedback.sqlite3 settle-parlays
```

The rules that keep it honest:

- **A parlay with any unsettled leg stays pending.** It is never graded on a
  subset of its legs.
- **A parlay whose legs cannot all be resolved is never captured.** One missing
  leg makes it ungradeable forever, and a stored decision that can only read as
  pending is worse than an absent one.
- **A pushed leg drops out and the payout is recomputed from the survivors** —
  not the combined price the parlay carried before the push was known. A parlay
  whose legs all push is itself a push.
- **A won parlay with no price is left unsettled** rather than recorded with a
  fabricated return.
- **`results._pending_rows` excludes `market_type = 'PARLAY'` outright**, so the
  single-row grader can never pick one up and grade it off one box score.

Most parlay boards carry no `recommended_units`, so they seed `STAND_DOWN` and
measure through `would_have_result` (the flat-stake counterfactual) rather than
staked PnL. That is the correct reading of a shadow surface, not a gap.

Parlay pack types are `PARLAY_PLAYER_PROP`, `PARLAY_TEAM_TOTAL`,
`PARLAY_BANKROLL`, and `PARLAY_ULTIMATE_ALT`.

Pack-type status values are `ON_TRACK` (actual hit rate within `--tolerance` of
the model's expected hit rate), `NEEDS_ADJUSTMENT` (model overconfident by more
than the tolerance, or negative ROI with no model probability to compare),
`AHEAD_OF_MODEL`, `INSUFFICIENT_DATA` (below `--min-samples`, default 20), and
`NO_SETTLEMENTS`. A single slate will almost always read `INSUFFICIENT_DATA` per
lane; run the audit across a date range of packs before treating any lane
verdict as a signal.

## Closing-line/CLV capture

A close must be a genuinely distinct observation from the taken snapshot. An
outcome that was only ever captured once has no real close: reporting the
take as its own close manufactures a false, precise `clv_line`/`clv_price` of
`0.0` instead of leaving it unknown. `_latest_local_close` in `results.py`
therefore excludes the taken row's own `snapshot_id` and only accepts a
second, later local capture (same book+outcome, then any book on the
event/outcome, then market+selection). When no distinct local snapshot
exists it falls back to the current `*_line_movement_latest.json` export --
usable only when that export was generated at or after the event's start, so
it reflects a real close and not just another pregame quote.

If a settled sample was written before this fix, recompute it:

```powershell
python -m outlier_scrapers.feedback recompute-clv [--dry-run]
```

This only touches settlements whose stored `closing_line`/`closing_price`
exactly match the matched snapshot's own `line`/`price` -- the fingerprint
the bug leaves behind -- and re-resolves each one against a genuinely
distinct snapshot or the line-movement export, clearing it to unknown rather
than leaving it wrong when neither is available. Local close resolution is a
set-based indexed operation, and line-movement exports are parsed once per
sport, so consolidated ledgers do not execute three close queries and two JSON
loads per settlement.

## Recovering a corrupted ledger

```powershell
python -m outlier_scrapers.feedback recover --corrupted calibration\feedback.sqlite3.corrupted --output calibration\feedback.sqlite3
```

Tries the `sqlite3` CLI's `.recover` first (it survives page-level corruption
a plain query does not); either way every row still readable is copied into a
fresh, schema-current database. Rows that fail NOT NULL/identity constraints
on the way in are logged and skipped rather than aborting the whole recovery.
If the corrupted file was copied from a live WAL-mode database, copy its
`-wal` (and `-shm`) companions alongside it under the same stem -- recovery
opens the file normally first specifically to apply any not-yet-checkpointed
commits sitting in the WAL, and without those companions that data is
invisible here, not merely slow to reach. Re-run `report` against the
recovered database afterward.

## Retention and vacuum

```powershell
python -m outlier_scrapers.feedback retention --cutoff-days 90 [--dry-run]
```

A snapshot row keeps every field consumed by reports, blend fitting, and stake
calibration. Once a row is settled, never played, unflagged, and older than
`--cutoff-days`, only unused pack-provenance and portfolio-at-capture columns
are cleared (`projection_feature_hash`, projection-quality metadata,
`pack_path`, policy/portfolio metadata, and cap fields). Unsettled, recent,
played, or flagged rows are never touched. `VACUUM` runs only when at least one
row was newly slimmed, so an idempotent rerun does not repeatedly rewrite and
vacuum the database.

Normal `daily_job` runs perform CLV repair and 90-day retention after result
collection/settlement ingestion and before blend refitting. Both operations are
nonfatal and their detailed status is written under `feedback_maintenance` in
the dated pack manifest. Controls:

```powershell
python -m outlier_scrapers.daily_job --feedback-retention-days 90
python -m outlier_scrapers.daily_job --skip-feedback-maintenance
python -m outlier_scrapers.daily_job --skip-clv-recompute --skip-feedback-retention
```

After desk orchestration the same run persists only the publications pinned by
`verdicts/desk_snapshot.json`; pass-level `current.json` pointers are never
read. Pack date, live fingerprint, pack membership, and `(market_id,
outcome_id)` must resolve to exactly one decision or the write fails closed and
is reported nonfatally under `desk_feedback` in the manifest. Repeating the
same snapshot is idempotent. C reduces with `CONTRADICTS > CONFIRMS > NEUTRAL`,
and the legacy final verdict follows pass E when pinned or the conservative
unanimous A+D+B fallback when E is absent.

## Remaining provider boundary

Completed results are now automatic. Closing-line data comes from a genuinely
distinct local capture or the line-movement export at/after event start, not
a separately licensed official close, and is left unknown rather than guessed
when neither is available. Feed outages, unsupported markets, missing
box-score statistics, and ambiguous identities are reported and left
unsettled for the existing CSV repair path. Use `--skip-result-collection`
only for an explicit diagnostic.
