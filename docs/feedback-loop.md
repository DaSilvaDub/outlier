# Feedback-loop ledger and calibration tool

The feedback tool turns dated packs into a permanent, gradeable dataset. It is
fully local and deterministic: it does not call a reasoning model or a results
provider.

## What is captured automatically

`python -m outlier_scrapers.pack` now writes `opportunities.csv` before the
top-N ranking step and then captures the pack into
`calibration/feedback.sqlite3`. The database is local/ignored and contains:

- `market_snapshots`: every pregame opportunity, including non-selected rows,
  the final pack selection state, exact outcome/line identity, probabilities,
  edge, book, data-quality flags, and the four Board B components.
- `decisions`: one decision per immutable market snapshot, seeded as `PLAY` or
  `STAND_DOWN`. A/B/C/D and final verdicts remain blank until imported from the
  pack-local `decisions.csv`. A later price/time snapshot gets its own decision
  instead of rewriting the earlier verdict history.
- `settlements`: the result, closing line/price, CLV, PnL, and would-have result
  joined to a decision/snapshot.

Repeated capture of the same source timestamp/line/price is idempotent. A new
line, outcome, source timestamp, price, or book creates a new snapshot and a new
seeded decision. Pack files are staged, captured to the durable ledger, and only
then promoted to the dated pack directory; a capture failure leaves the prior
published pack intact.

Use `--no-feedback-ledger` only for an explicit pack-only diagnostic. Use
`--feedback-db <path>` to override the default database.

## Probability and edge semantics

- `market_consensus_prob` is the existing no-vig market-derived probability.
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
- `play_vs_stand_down.csv` and `model_performance.csv`
- `ledgers/market_snapshots.csv`, `ledgers/decisions.csv`, and
  `ledgers/settlements.csv`

The report is useful immediately as a market-derived baseline. Learned Board B
weights and an independent probability model should be fit only after the
settled sample is large enough and should be validated out of sample.

## External boundary

This repository still has no authoritative closing-line/result provider.
Settlement ingestion is therefore an explicit CSV boundary. Automating that
feed is separate from the ledger/grading/reporting path and must preserve the
decision/snapshot identifiers above.
