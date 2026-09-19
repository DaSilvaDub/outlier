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

## 3. High-Probability Prop Auditing
When identifying top-tier props:
- **Tier-1 Priority:** Props with **L5 Hit Rate = 100%** and **L10 Hit Rate >= 80%** across multi-book consensus (e.g. Sam LaPorta 2.5+ Receptions, Khalil Shakir 3.5+ Receptions).
- **TD Scorers:** Target undisputed goal-line rushers with implied probability >= 65% (e.g. Jahmyr Gibbs Anytime TD).
- **Floor Anchors:** Alternate lines offering 88%–94% implied probability for conservative parlay foundations.

## 4. Post-Game Reconciliation & Calibration
When a game concludes:
1. Retrieve official box scores (via Outlier API or verified boxscore feeds).
2. Reconcile game lines (Spread, Total, Team Totals) and individual player props.
3. Apply the 3 Game Script Calibration Rules (Underdog RB Deficit Discount, Two-High Shell Target Divergence, and Empirical Hit Rate Weighting) to update future projections.
