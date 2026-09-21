---
name: nfl-game-script
description: >-
  Extract, model, and calibrate NFL game scripts and player propositions using the outlier_nfl
  pipeline and scripts/nfl_game_script.py. Covers consensus line selection, 3-scenario game script
  modeling, and post-game reconciliation against actual box scores.
---

# NFL Game Script & Calibration Runbook

Use this skill when tasked with generating pregame betting game scripts, analyzing high-probability props, or reconciling post-game outcomes for NFL slates.

## 0. Mandatory Pre-Flight Active Roster & Offseason Verification Gate
Before executing any game script generation, prop recommendation, or qualitative matchup analysis:

1. **Load Authoritative Depth Charts:**
   - Call `outlier_nfl.roster.get_team_depth_chart(away_team)` and `outlier_nfl.roster.get_team_depth_chart(home_team)`.
   - Verify starting quarterbacks, primary ball-carriers, slot/perimeter receivers, and tight ends against `data/NFL/normalized/nfl_rosters_latest.json`.
2. **Cross-Check Offseason Player Movements:**
   - Consult `OFFSEASON_MOVES_2026` in `outlier_nfl/roster.py` for any player who changed teams.
   - Enforce that no player is referred to by their former franchise or depth chart role (e.g. Kenneth Walker III is on KC, Daniel Jones is starting on IND, A.J. Brown is on NE, Hollywood Brown is on PHI, Keenan Allen is on IND, Romeo Doubs is on NE, Rico Dowdle & Michael Pittman Jr. are on PIT).
3. **Automated Markdown Text Gate:**
   - Prior to writing or rendering the final game script report markdown:
     ```python
     from outlier_nfl.roster import validate_analysis_text_for_roster_errors
     errors = validate_analysis_text_for_roster_errors(report_md)
     if errors:
         raise ValueError(f"Game Script Roster Violation: {errors}")
     ```
   - If any violation is found, fail closed and correct the player attribution before presenting the script to the user.

## 1. Data Ingestion & Slate Extraction
Execute the standalone Outlier NFL pipeline for the target date:
```powershell
python -m outlier_nfl.pipeline --date YYYY-MM-DD
python -m outlier_nfl.pipeline --date YYYY-MM-DD --window snf --generate-game-script
```
- Outputs are persisted to `data/NFL/normalized/nfl_games_YYYY-MM-DD.json` and `nfl_props_YYYY-MM-DD.json`.
- The pipeline runs completely decoupled from `outlier_scrapers`.
- **Every slate matchup is analyzed before props are calibrated.** Drop prior-week unit tape at `data/NFL/tape/prior_week.json` (or `latest.json`) using `{ "week": N, "season": 2026, "teams": { "KC": { "rush_yards", "opp_rush_yards_allowed", "rush_offense", "rush_defense", "pass_offense", "pass_defense", "pass_rush", "qb_grade", "sacks", "qb", "rb1", "te", "wr_slot", "wr_deep" } } }`.
- Matchup scripts land in `data/NFL/normalized/nfl_matchup_scripts_YYYY-MM-DD.json`. Mismatch tags (`MATCHUP_RUSH_MISMATCH`, `MATCHUP_PASS_SUPPRESS`, `MATCHUP_COVERAGE_LEAK`, `MATCHUP_FADE`) stack onto consensus player props with volume adjustments alongside deficit-risk / shell / hit-rate calibration.
- `--generate-game-script` writes **one markdown file per matchup** under `reports/NFL/YYYY-MM-DD_<AWAY>_<HOME>_Game_Script.md`.

## 2. Game Script & Consensus Prop Generation
Run the game script engine:
```powershell
python scripts/nfl_game_script.py --date YYYY-MM-DD --out reports/NFL/YYYY-MM-DD_<AWAY>_<HOME>_Game_Script.md
```
Each matchup file includes:
1. **Highest-probability script** from prior-week tape mismatches (rush vs leaky run D, pass rush vs weak QB, slot/TE vs leaky secondary) stacked on consensus lines.
2. **Matchup-tagged player props** with volume adjustments (`MATCHUP_RUSH_MISMATCH`, `MATCHUP_PASS_SUPPRESS`, `MATCHUP_COVERAGE_LEAK`, `MATCHUP_FADE`). Deficit-risk and deep-threat haircuts are not reversed.
3. **Three canonical scenarios** labeled BASE/ALT from the selected script type:
   - Front-runner grind (favorite RB, under total)
   - Competitive one-score
   - Shootout / upset (the Over-total path)

## 3. Multi-Factor Prop Prediction & Re-Basing Protocol
Never select or recommend props purely off an isolated L5 hit rate. All player proposition predictions must satisfy five analytical pillars:

1. **Multi-Window Hit Rate Convergence:**
   - Require **L5 >= 80%** *and* **L10 >= 70%–80%** simultaneously, backed by consistent season-long snap and target shares.
   - Guard against alternate ladder distortion where books offer inflated odds on synthetic lines.
2. **Line Movement & Market Breadth:**
   - Filter for balanced two-way liquidity across minimum 3–5 regulated sportsbooks (DraftKings, FanDuel, BetMGM, Caesars, Fanatics).
   - Verify line stability and juice within standard margins (`-145` to `+115`).
   - Track steam movement and opening vs. current line/odds divergence to confirm sharp market agreement.
3. **Compiled Outlier & Matchup Stats:**
   - Ingest Outlier's `teamComparison` metrics (offensive passing/rushing efficiency, 3rd down conversion %, red zone efficiency %).
   - Match defensive coverage schemes against receiver route trees (e.g. intermediate slot receivers against two-high Cover-2/Cover-4 zones).
4. **Injury & Personnel Vacancy Analysis:**
   - Cross-reference official injury reports and IR designations (e.g., vacated touches when RB1 or WR1 is out).
   - Reallocate target and rush share to direct beneficiaries rather than relying on stale multi-game averages.
5. **Weather & Venue Environmental Calibration:**
   - Open-air precipitation and sustained winds $\ge 12\text{--}15\text{ mph}$ demand downward volume adjustments on deep vertical passing in favor of tight end checkdowns and power rushing.
   - Domed/controlled venues (NRG, Mercedes-Benz, AT&T) preserve maximum offensive pace and perimeter separation.

## 4. High-Probability Prop Tiering
When categorizing filtered props:
- **Tier-1 Alpha Anchors:** Props with L5 >= 80%, L10 >= 80%, strong multi-book consensus (8+ books), and favorable tactical matchups (e.g. Cole Kmet Over 1.5 Rec vs. blitz-heavy defense, Michael Wilson Over 3.5 Rec in dome).
- **Goal-Line TD Scorers:** Target undisputed goal-line ball-carriers with implied probability >= 60% in high-total environments (Team Total >= 24.0).
- **Floor Ladder Foundations:** Alternate lines offering 85%–92% implied probability used strictly as foundational anchors in correlated parlay builds.

## 5. Post-Game Reconciliation & Calibration
When a game concludes:
1. Retrieve official box scores (via Outlier API or verified boxscore feeds).
2. Reconcile game lines (Spread, Total, Team Totals) and individual player props.
3. Apply the 3 Game Script Calibration Rules (Underdog RB Deficit Discount, Two-High Shell Target Divergence, and Multi-Window Re-Basing) to update future projections.
