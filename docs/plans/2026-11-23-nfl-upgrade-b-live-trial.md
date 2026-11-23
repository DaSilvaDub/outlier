# NFL Upgrade B live trial (Weeks 12-15)

Pre-registered 2026-11-23, before any Week 12 result. Upgrade B (PR #382) stays
merged but is treated as unproven until it earns trust on live slates.

## Why

PR #382 reported 64-30 (68.1%, +27.8% ROI) on Weeks 5-11 and 17-11 on a Weeks 9-11
holdout. Neither number is yet evidence:

- **The 68.1% is in-sample.** The cutoffs (L10 and season at least 70%, odds band
  -155/+135, no yardage OVERs) look chosen on the same weeks that score them.
- **The holdout can't be checked.** The backtest harness behind PR #382 was never
  committed (the PR touched only `best_bets.py` and its tests). Nothing in the repo
  shows the cutoffs were fixed using Weeks 5-8 only. Until a walk-forward rerun
  confirms it, treat the Weeks 9-11 result as in-sample too.
- **The holdout is small.** 17-11 over 28 picks is not distinguishable from the
  52.4% breakeven at -110 (one-sided binomial p = 0.25).
- **Round-number cutoffs that remove about 90% of picks are a classic overfitting
  pattern.**

## Trial setup

| Item | Setting | Where |
|---|---|---|
| Weeks | 12-15 (2026-11-26 to 2026-12-21) | `config/nfl_upgrade_b_trial.json` |
| Gates | Upgrade B as merged (all on) | `gates.disabled: []` |
| Stake | **flat 0.5% of bankroll** per VALIDATED pick, instead of quarter-Kelly | `stake.mode: flat`, `flat_fraction: 0.005` |
| Kelly | still computed and stored on each pick as `kelly_stake_fraction` | card JSON |
| Replay input | every run saves `nfl_best_bets_inputs.json.gz` in its bundle (sha256 in the manifest) | `outlier_nfl/trace_capture.py` |

- **The trial does not end on its own.** Stakes stay flat after Week 15 until this
  config is changed and the decision is recorded below. A calendar date must never
  silently put money back on Kelly.
- **Paper trade instead?** Set `"stake": {"mode": "paper"}` to stake nothing. The
  cards and the scorecard work the same way.
- **A malformed config fails the card.** It never falls back to Kelly; the stale
  card is removed, just as for any other trace failure.

## Pre-registered stop criteria

The criteria are applied mechanically to VALIDATED picks by
`python -m outlier_nfl.trial_scorecard`. They are encoded in the config's
`stop_criteria` section and must not be edited once results start arriving.

| Rule | Judged after | Triggers when | Action |
|---|---|---|---|
| `clv` (early signal) | 40 picks with a verified book close | mean CLV < 0 implied pts | **Review the gates** |
| `win_rate` | 100 decided picks | win rate < 53% | **Review the gates** |
| `drawdown` | any time | flat-stake drawdown reaches 15 units | **Stop staking, review** |
| `success` | 200 decided picks | one-sided 95% lower bound of win rate > 52.4% **and** CLV rule ok | Gates have earned trust: consider restoring Kelly |

Overall verdict: `REVIEW_GATES` if any rule triggers; `EARNED_TRUST` once `success`
holds and CLV is ok; otherwise `CONTINUE`.

Why these numbers:

- **Sample size.** At about 6 VALIDATED picks per slate, an observed 58% only clears 52.4%
  (one-sided 95% lower bound) at about 210-250 decided picks. Win/loss alone will take most of
  the trial, possibly longer, to mean anything.
- **CLV is faster.** Closing-line value converges much sooner. If VALIDATED picks
  consistently beat the close, the gates are probably finding real value. If they
  don't, the 68% backtest was likely fitted to noise, whatever the win rate says
  meanwhile.
- **Winning without CLV is suspect.** The scorecard therefore reports
  `REVIEW_GATES` even when the win rate looks good, if CLV is negative.
- **CLV counts only verified book closes** (`close_source=book_close`, captured
  within 60 minutes before kickoff, PR #375) and only when the close line equals the
  bet line. A moved line is reported separately as `close_line_move`.

## Weekly procedure (on the Windows machine; data/ never written by an agent)

1. Run the pipeline as usual. Cards now carry `trial`, `settings.stake_policy` and
   each pick's `upgrade_b` block (`gates_fired`, `blocked_only_by`).
2. Capture closes for CLV shortly before each window's kickoff (requires
   `ODDS_API_KEY`). The timing policy only verifies quotes taken within 60 minutes
   before kickoff:

   ```
   python -m outlier_nfl.fetch_odds_close --out data/NFL/close/close_<date>_<window>.json
   ```

3. After the week's games, score the trial:

   ```
   python -m outlier_nfl.trial_scorecard --cards "data/NFL/normalized/nfl_best_bets_2026-1*.json" --provider nflverse --season 2026 --weeks 12 13 --close-feed data/NFL/close/close_<date>_<window>.json --out-json reports/NFL/upgrade_b_trial.json --out-md reports/NFL/upgrade_b_trial.md --overwrite
   ```

   - The scorecard keeps the **latest pre-kickoff verdict** for each bet. A card
     written after kickoff is ignored.
   - Arms reported: `VALIDATED` (the trial), each `blocked_only_by:<gate>` (picks
     one gate alone held back), `PROVISIONAL` and `REJECTED`.
   - It also prints the stop-criteria verdict and the `base_p` reliability tables.

## Gate tests (do next; tells us which gates to keep)

`python -m outlier_nfl.gate_backtest` replays the captured inputs. That gives the
exact counterfactual card for any gate setting, graded against the same outcomes.

- **Ablation:** `ablation --captures "data/NFL/runs/*/nfl_best_bets_inputs.json.gz" ...`
  - Scores `legacy` (pre-Upgrade-B), `only:<gate>`, `all_but:<gate>` and
    `upgrade_b`, in units and CLV.
  - Labels each gate `keep` (helps alone *and* is missed when removed),
    `drop_candidate` (neither) or `mixed`.
- **Walk-forward:** `walk-forward --split-date <date> --grid small ...`
  - Picks thresholds on slates before the split only.
  - Scores the later slates once, next to the merged defaults and legacy.
  - Prints the train top 5: if they are close, the chosen thresholds are noise.
- **Equivalence (checked when this landed):**
  - `GateConfig()` reproduced the PR #382 code exactly on 400 randomized slates.
  - `GateConfig.legacy()` reproduced the pre-#382 code exactly on the same 400:
    verdicts, base_p, final_p, stakes and pillar statuses.

**Weeks 5-11 can't be replayed yet.** Captures exist only from this change forward.
Re-running the original 918-pick backtest walk-forward needs the inputs PR #382
used; if they still exist on the Windows machine, convert them to `TraceInputs`
captures and run the same commands.

Priors to test, not conclusions:

- **Most credible:** the line-move chase fix, the odds band, and the INT/sack EPA
  sign fix. Each has a clear mechanical reason to work.
- **Most suspect:** the L10 and season at least 70% floors, and the blanket
  yardage-OVER cap.
- **Drop any gate that doesn't hold up on its own.**

## Calibration of the market anchoring (later)

- **What to watch.** The scorecard's reliability tables compare predicted vs actual
  hit rate by `base_p` bucket and by the player's blended empirical hit rate.
- **Kappa too strong.** If the high empirical buckets beat their mean `base_p`
  (positive gap, z above 2), κ=12 is underweighting real signal.
- **Kappa too weak.** If those buckets fall short, the prior isn't strong enough.
- **Where to tune.** Tune `kappa_standard` only through `gate_backtest walk-forward`,
  never on the slates being evaluated.

## `config/nfl_audit_rules.json`: decided not needed (removed)

- **What it was.** PR #382 added a loader for this file and recorded
  `{loaded, audit_date, active_rules_count}` in the card audit.
- **Why it's gone.** Nothing ever applied those rules to a verdict. No document or
  script defines `section_12_betting_rules`. Writing such a file would mean
  inventing rules.
- **What replaced it.** The loader is removed. The audit now records what is
  actually in force: `audit.upgrade_b` (enabled gates, stake mode, how often each
  gate fired, and `blocked_only_by` counts on that card).

## Decision log

| Date | Scorecard verdict | Decision | By |
|---|---|---|---|
| 2026-11-23 | (trial starts) | Flat 0.5% stakes, all Upgrade B gates on | user |
