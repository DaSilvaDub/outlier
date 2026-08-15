---
name: calibrate-outlier
description: >-
  Automated runbook for evaluating the historical ledger against actual outcomes, checking
  ROI/hit rates on highly recommended bets (PLAYS), and running recalibration
  scripts (fit-blend, fit-stake-calibration) to correct sizing logic if needed.
---

# Outlier Calibration Runbook

When invoked to calibrate or check historical performance of bets (either manually or via a scheduled background task), follow this workflow:

1. **Evaluate Current Performance**
   Run the report generator to parse the latest ledger data:
   ```powershell
   python -m outlier_scrapers.feedback report
   ```
   Then read `calibration/reports/latest/play_vs_stand_down.csv` and `expected_vs_actual.csv`. 
   - Check the `ROI` and `profit` columns for the `PLAY` decision class. 
   - If the ROI is sharply negative, or if the `hit_rate` for `PLAYS` is lower than for `STAND_DOWNS`, the system sizing or blend logic is degraded.

2. **Run Recalibration Scripts**
   If performance is poor or the system is missing heavily on recommendations, run the model fitting scripts to correct the blends and stake limits:
   ```powershell
   python -m outlier_scrapers.feedback fit-blend
   python -m outlier_scrapers.feedback fit-stake-calibration
   ```

3. **Replay the Portfolio**
   To verify the recalibration successfully filters out the bad bets on historical data, run a chronological replay:
   ```powershell
   python -m outlier_scrapers.feedback replay-portfolio --from 2026-07-01 --to 2026-12-31 --policy config/portfolio_risk.json
   ```

4. **Regenerate Reports**
   Re-run the final report:
   ```powershell
   python -m outlier_scrapers.feedback report
   ```
   Analyze the outputs and explicitly inform the user if any code fixes are required to fix the underlying discrepancy between conditional and absolute win probabilities.
