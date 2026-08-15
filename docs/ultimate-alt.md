# Ultimate Alt shadow board

`ultimate_alt.csv` is the single decision surface for alternate spreads,
alternate totals, and alternate player props. The existing market-specific CSVs
remain compatibility and audit artifacts; downstream bet selection should move
to Ultimate Alt only after its release gate passes.

## Filtration contract

Every input keeps its market-specific eligibility checks, then enters a common
price-aware filter:

- pregame, full-game, active offer with complete event/market/outcome identity;
- L10 at least 80%, L5 at least 80% when present, and player season rate at
  least 70%;
- recent rates are shrunk toward 50% and converted to a one-sided 90% Wilson
  lower bound;
- the conservative probability must beat the book's implied probability by at
  least 1.5 percentage points and produce at least 1.5% expected value;
- short samples and integer-line push risk are rejected;
- at most eight qualified legs survive the daily shortlist.

`ultimate_alt.csv` retains rejected rows and exact `rejection_reasons` so the
filter can be audited. `ultimate_alt_parlays.csv` contains at most five two- or
three-leg combinations. Parlays must span events, use at least two market
types, contain no repeated team or player, have decimal odds from 1.4 through
4.0, and clear 3% conservative EV. Same-game combinations are forbidden until
an explicit correlation model exists.

## Shadow safety

Ultimate Alt rows are always emitted with `actionable=false`. Qualified legs
receive a hypothetical 0.5-unit stake and compete with the live streams in a
combined shadow portfolio allocation, but they never consume or change live
allocations—even if the main portfolio policy is in enforce mode.

The feedback report writes `ultimate_alt_shadow.csv` and a release-gate object
inside `summary.json`. Promotion is never automatic. Manual review is permitted
only after all gates pass:

- at least 200 settled legs across at least 30 shadow days;
- at least 40 settled legs in each of spreads, totals, and player props;
- positive flat-stake ROI;
- absolute calibration gap no greater than 5%;
- closing-price data on at least 80% of settled legs; and
- non-negative average closing-price CLV.

## Deterministic shadow report

Generate an auditable Markdown report from the two canonical CSV artifacts with
an explicit pack date and output path:

```powershell
python generate_shadow_report.py `
  --alt-csv path\to\ultimate_alt.csv `
  --parlay-csv path\to\ultimate_alt_parlays.csv `
  --pack-date 2026-08-15 `
  --source-prompt path\to\Ultimate_Alt_Analysis.md `
  --output path\to\ultimate_alt_shadow_report.md
```

The command validates complete CSV schemas, degrades malformed legs individually,
requires canonical wager identity, and accepts only supplied parlays that retain
the cross-event, multi-market, price, EV, and shadow-only gates. Publication is
atomic. A `CONTINUE` verdict authorizes continued shadow evaluation only; it never
authorizes activation or promotion.

## Main totals shadow comparison

The live totals contract remains a 3% minimum edge. New columns in
`game_totals.csv` and `team_totals.csv` record a shadow alternative requiring a
4% edge and hard-rejecting model-divergence flags. These columns do not change
live `actionable` or live units. The threshold can be promoted only after the
same settlement history shows an out-of-sample ROI/calibration improvement.
