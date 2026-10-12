# NFL alternate-floor probability contract

Alternate-floor exports are **QUOTED_RESEARCH**. The heuristic orders available
floors; it is not a calibrated win probability. This release captures a versioned
training dataset and supplies offline settlement and shadow calibration. It does
not activate a fitted model for betting.

## Report and export fields

- `ranking_score` and `ranking_score_version` describe the ranking formula.
  `confidence_score` remains a deprecated numeric alias for existing consumers.
- `quote_eligible` means the saved quote passed floor inventory checks.
  `actionable` and `bet_actionable` are false, and `stake_fraction` is zero.
- `break_even_probability` is the quoted half-point line's required win rate.
  Integer and malformed lines withhold it and the legacy `implied_probability`
  field because push probability is unmodeled.
- `market_probability` is a proportional de-vig benchmark from an exact
  same-book, same-event/player/team/scope/line pair. Missing, mixed or conflicting
  quotes withhold it. It is not an independent forecast.
- Model/calibrated probability, fair odds and EV are unavailable. The report
  explicitly states this; neither heavy juice nor `PARLAY_ONLY` enables betting.
- Hit rates stay unavailable when absent. The report never invents denominators
  by multiplying a provider's L5/L10 rate by five or ten.

`calibration_records` includes all selected floors, including those outside the
displayed top three and inventory with incomplete evidence. Training admission
is stricter than recording: ineligible inventory is not calibration evidence.

Each export carries `run_id`, `captured_at`, `run_context`, and the formula version
`nfl_alt_floor_rank_v1`. RunWriter hashes the staged export in the immutable run
manifest and records `sealed_utc` after artifact hashing. `created_utc` remains the
run-start timestamp. The pipeline's required `alt_floor_capture` receipt and final
publication gate prevent capture failures from publishing earlier staged output.
Windowed runs preserve full-slate rolling reports.

## Offline research workflow

From the repository root:

```powershell
python scripts/nfl_floor_calibration.py `
  --runs-dir data/NFL/runs `
  --output-dir work/floor-calibration-research `
  --as-of 2026-10-12T01:45:00Z
```

Without outcomes, the command audits the archive and emits **DATA_INSUFFICIENT**.
It writes inventory, settlement diagnostics, a shadow artifact and a Markdown
report only to the supplied output directory. Output inside the immutable input
runs is refused. No network or paid reasoning calls occur.

To settle and fit, additionally pass `--outcomes verified-finals.json`. The file
must contain a `records` array with explicit final outcome attestations:

```json
{
  "records": [
    {
      "event_id": "exact-event-id",
      "event_starts_at": "2026-10-11T20:20:00-04:00",
      "player_id": "exact-player-id",
      "team": "BAL",
      "opponent": "ATL",
      "market": "RUSH_YDS",
      "scope": "full_game",
      "position": "OVER",
      "line": 49.5,
      "actual": 67,
      "status": "verified_final",
      "finalized_at": "2026-10-12T04:20:00Z",
      "source_id": "provider-event-and-player-reference",
      "source_sha256": "64-lowercase-hex-digits-of-the-outcome-source"
    }
  ]
}
```

This is a schema example, not a real outcome; replace every identity/source field.
Actual yardage may be negative. Missing statistics remain missing; kickoff is not
proof of a final. The offline tool trusts the caller's explicit source attestations
and records the input hash; it does not independently fetch or verify the provider.

## Admission, fitting and evaluation

Only hash/size-matching live, non-replay snapshots with known formula version,
complete exact identity, eligible quote and an aware pregame seal can enter
fitting. The run must finish sealing before the relevant kickoff. Legacy exports
without these fields remain inventory and are never assigned a guessed version.

The declared policy selects the latest admitted pregame snapshot per exact wager
target. Conflicting latest scores and inconsistent event identity are refused.
Multiple books and reruns do not create extra training observations. Within each
family, each game receives one total weight even if several players/lines exist.

The trainer fits a regularized sigmoid with a nonnegative ranking-score slope,
separately for passing, rushing and receiving yards. Whole **Eastern kickoff ISO
weeks** are split chronologically; Sunday night stays with Sunday's daytime games.
The last 25% of observed weeks is holdout. Earlier-game outcomes must already be
known before the first holdout prediction. Holdout labels never change the fitted
coefficients, and later measurement does not refit on the holdout.

The default research guard requires 30 distinct training games and 10 later
holdout games per family, and both training outcome classes. These are guards
against fitting tiny datasets, not statistical certification or betting approval.
Reports include event-weighted Brier/log loss, training-prevalence comparisons,
reliability bins and event-unit uncertainty. Exact-pair market comparisons are
optional and use the same eligible subset; an unavailable benchmark stays absent.
The ranking-score diagnostic is explicitly a proxy, not a probability claim.

Every fitted artifact remains **SHADOW_ONLY**, `approved_for_betting=false`.
`predict_shadow()` emits only `shadow_probability`, refuses malformed artifacts
and scores outside the observed training range, and supplies no EV or stakes.
There is deliberately no production activation path in this release. Successful
fitting or lower Brier loss alone does not establish calibration or value.

Before a later activation change, review support across weeks and relevant roles,
high-probability reliability, uncertainty, bookmaker baselines and actual price
economics. Quote eligibility, calibrated probability and positive EV must remain
distinct gates. Same-game parlay probability needs a separate joint model.

## Evidence limits

Hashes establish consistency with the saved manifest; declared timestamps do not
independently establish when external provider information became available.
Historical signal replays and other sports' fitted coefficients cannot substitute
for frozen NFL floor predictions. Synthetic tests establish algorithm and contract
behavior, not empirical NFL betting performance.

The statistical split and calibration distinction follow the official
[calibration guidance](https://scikit-learn.org/stable/modules/calibration.html)
and [time-ordered validation guidance](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).
