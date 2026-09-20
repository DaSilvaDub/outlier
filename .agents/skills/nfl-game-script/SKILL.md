---
name: nfl-game-script
description: >-
  Extract, model, and calibrate NFL game scripts and player propositions using the outlier_nfl
  pipeline and scripts/nfl_game_script.py. Covers consensus line selection, 3-scenario game script
  modeling, and post-game reconciliation against actual box scores.
---

# NFL Game Script & Calibration Runbook

Use this skill when tasked with generating pregame betting game scripts, analyzing high-probability props, or reconciling post-game outcomes for NFL slates.

## 1. Data Ingestion & Slate Extraction
Execute the standalone Outlier NFL pipeline for the target date:
```powershell
python -m outlier_nfl.pipeline --date YYYY-MM-DD
```
- Outputs are persisted to `data/NFL/normalized/nfl_games_YYYY-MM-DD.json` and `nfl_props_YYYY-MM-DD.json`.
- The pipeline runs completely decoupled from `outlier_scrapers`.

## 2. Game Script & Consensus Prop Generation
Run the game script engine:
```powershell
python scripts/nfl_game_script.py --date YYYY-MM-DD --out reports/NFL/YYYY-MM-DD_<AWAY>_<HOME>_Game_Script.md
```
The generator extracts:
1. **Consensus Market Environment:** Devigged Moneylines, Point Spread, Game Total, and Implied Team Totals.
2. **Consensus Player Props:** Standard two-way lines filtered for balanced juice (-220 to +180) across sportsbooks.
3. **Three Canonical Scenarios:**
   - **Scenario 1 (Shootout / High Pace):** High offensive efficiency, both QBs high volume, red-zone TD rushers.
   - **Scenario 2 (Front-Runner / Ground Grind):** Favorite leads early; heavy RB rush volume, opponent forced into trailing pass comeback mode.
   - **Scenario 3 (Underdog Ground Control / Upset):** Underdog controls time of possession; Under total in play.

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
