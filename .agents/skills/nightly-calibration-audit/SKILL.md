---
name: nightly-calibration-audit
description: >-
  Run this skill every night at midnight to check for underlying math bugs, validate game results against the extracted data pipeline, and automatically recalibrate the Outlier sizing models.
---

# Nightly Calibration & Audit Runbook

This skill is designed to run automatically at the end of every night to ensure the Outlier pipeline's sizing math and probabilities are perfectly calibrated against actual historical settlements.

## Steps to Execute

1. **Collect and Grade Completed Game Results**
   - Run `python -m outlier_scrapers.results` to fetch final scores and player box scores from MLB Stats API and ESPN WNBA, automatically settling pending ledger records.
   - Verify that completed games for the target slate are graded into `calibration/feedback.sqlite3`.

2. **Check for Underlying Math Bugs via Feedback Report**
   - Run `python -m outlier_scrapers.feedback report` to generate the latest portfolio metrics.
   - Read `calibration/reports/latest/play_vs_stand_down.csv`.
   - **Verification**: Compare the ROI of `PLAY` decisions versus the `would_have_flat_pnl`. If the dynamic Kelly sizing is severely underperforming a flat betting strategy (e.g., negative ROI while flat betting is positive), this indicates a probability math bug or over-sizing issue on push-capable lines. 

3. **Check Game Results / Extract Data Pipeline for the Day**
   - Verify that the ledger has been successfully settled for the day's games in `calibration/feedback.sqlite3`.
   - If anomalies are found in the report, inspect `calibration/feedback.sqlite3` or run the pipeline `python -m outlier_scrapers.daily_job --date <today>` to ensure data extraction aligns with the game results.

4. **Recalibrate the Models**
   - Run `python -m outlier_scrapers.feedback fit-blend` to re-tune `blend_weights.json`.
   - Run `python -m outlier_scrapers.feedback fit-stake-calibration` to update `stake_calibration.json`.
   - Run `python -m outlier_scrapers.feedback replay-portfolio --from 2026-07-01 --to 2026-12-31 --policy config/portfolio_risk.json` to verify that the newly fitted models correct any negative ROI trends.

4. **Summarize Findings**
   - Create a `walkthrough.md` artifact summarizing the nightly run, highlighting any math bugs found, how they were mitigated by recalibration, and the final state of the portfolio.
