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
  `STAND_DOWN`. A/B/C/D and final verdicts remain blank until imported from the
  pack-local `decisions.csv`. A later price/time snapshot gets its own decision
  instead of rewriting the earlier verdict history. There is no `E_verdict`
  column — pass E (or the no-E fallback) is the desk report, not a fifth
  scalar on this row.
- `pack_snapshot_memberships`: immutable dated-pack membership for each quote,
  keyed by a pack-capture fingerprint plus `snapshot_id`. It preserves the
  pack path, pack timestamp, seeded verdict, units, and selection/actionability
  state when the same source quote is reused in a later pack. The legacy
  `market_snapshots.pack_path` remains the first-seen path and is never reassigned.
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
- `market_type.csv`, `edge_buckets.csv`, `odds_ranges.csv`, `books.csv`,
  `leagues.csv`, and `signal_flags.csv`
- `play_vs_stand_down.csv`, `decision_coverage.csv`, and `model_performance.csv`.
  ROI and hit-rate tables are settled-only; `decision_coverage.csv` separately
  reports total, settled, unsettled, and missing-event-time PLAY counts.
- `ledgers/market_snapshots.csv`, `ledgers/decisions.csv`, and
  `ledgers/settlements.csv`, plus `ledgers/pack_snapshot_memberships.csv`

The report is useful immediately as a market-derived baseline. Learned Board B
weights and an independent probability model should be fit only after the
settled sample is large enough and should be validated out of sample.

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
than leaving it wrong when neither is available.

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

A snapshot row keeps every research column at full fidelity only if it
reached `PLAY`/`BET` or the desk flagged it (`board = 'A_FLAGGED'`) -- that is
the data a calibration report actually needs in full. Once a row is settled,
never played, unflagged, and older than `--cutoff-days`, its wide exploratory
columns (signal components, projection hashes, portfolio caps, blend
metadata, ...) are cleared to keep only identity/result-relevant fields;
unsettled, recent, played, or flagged rows are never touched. The command
vacuums afterward to actually reclaim the freed pages on disk. Run this
periodically (or wire it into the daily job) instead of letting the ledger
grow unbounded.

## Remaining provider boundary

Completed results are now automatic. Closing-line data comes from a genuinely
distinct local capture or the line-movement export at/after event start, not
a separately licensed official close, and is left unknown rather than guessed
when neither is available. Feed outages, unsupported markets, missing
box-score statistics, and ambiguous identities are reported and left
unsettled for the existing CSV repair path. Use `--skip-result-collection`
only for an explicit diagnostic.
