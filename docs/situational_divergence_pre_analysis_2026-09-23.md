# Pregame Situational Divergence Analysis & Sizing Verdicts — 2026-09-23 Slate

**Target Slate**: 2026-09-23  
**Document**: `docs/situational_divergence_pre_analysis_2026-09-23.md`  
**Reference Datasets**: `packs/2026-09-23/game_totals.csv`, `packs/2026-09-23/alt_team_totals.csv`  
**Compliance**: Outlier Multi-Ent Global Rules, House Rule 3 (MLB Alt Team Totals Over runs only / cross-game parlay invariant), Paid AI Reasoning OFF  

---

## 1. Executive Summary & Calibration Context

On the active 2026-09-23 slate, the Outlier game totals pipeline generated 13 full-game total propositions across MLB and WNBA. Of these, 9 games were tagged with model divergence or directional conflict flags (`totals_model_divergence`, `FAIR_TOTAL_DIVERGENCE`, `FAIR_TOTAL_SIDE_CONFLICT`, `SOURCE_INTEGRITY_FLAG`).

### Root Causes of Algorithmic Divergence
1. **Uncalibrated Empirical Bayes Prior ($\alpha = 20.0$)**:
   The uncalibrated shrinkage formula in `outlier_scrapers/game_totals.py` pooled a 10-game window from both teams ($n = 20$). With $\alpha = 20.0$, the empirical weight on recency noise was $w = 20 / (20 + 20) = 50.0\%$. In high-variance sports like baseball, small sample clusters (e.g. 7 of 10 Over) produced synthetic model win probabilities approaching 60% against sharp market consensus prices near 48%, generating false edge spikes exceeding +20%.
2. **Directional Fair Total Inversion**:
   In multiple matchups, the model computed a consensus `fair_total` based on market ladder movements, yet the selection engine picked a side conflicting with that fair total (e.g. selecting OVER 8.5 when `fair_total = 8.0`, or UNDER 172.5 when `fair_total = 173.5` after sharp upward steam).
3. **Severe Environmental and Personnel Factors**:
   Several games exhibited environmental headwinds (15 mph crosswinds blowing in off Lake Michigan or from right-center at Citizens Bank Park), heavy bullpen strain, or vacated rim protection that directly contradicted the unregressed recency trend.

### Master Slate Sizing Verdicts

| Matchup | Sport | Headline Line | Market Prob | Blended Prob | Raw Edge | Quality Flags | Pregame Verdict | Sizing | Primary Qualifying Alternate Team Total Fallback |
|---|---|---|---|---|---|---|---|---|---|
| **MIL @ PHI** | MLB | 7.5 OVER (+108) | 0.4852 | 0.5926 | +23.26% | `totals_model_divergence` | **PASS** | **0.0u** | **PHI Alt TT Over 2.5** (-180 Hard Rock, L5: 100%, L10: 90%) |
| **SD @ LAD** | MLB | 8.5 OVER (+113) | 0.4773 | 0.5637 | +20.06% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, totals_model_divergence` | **PASS** | **0.0u** | **SD Alt TT Over 2.5** (-170 Hard Rock, L5: 80%, L10: 90%) |
| **CIN @ ATL** | MLB | 7.5 UNDER (-111) | 0.5245 | 0.4373 | -16.88% | `totals_model_divergence` | **PASS** | **0.0u** | **ATL Alt TT Over 2.5** (-400 Hard Rock, L5: 80%, L10: 90%) |
| **MIA @ CHC** | MLB | 6.5 UNDER (+125) | 0.4499 | 0.2499 | -43.76% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, totals_model_divergence` | **PASS** | **0.0u** | **CHC Alt TT Over 2.5** (-250 Midnite, L5: 80%, L10: 90%) |
| **DAL @ SEA** | WNBA | 172.5 UNDER (+117) | 0.4797 | 0.4898 | +6.30% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT` | **PASS** | **0.0u** | **DAL Alt TT Over 85.5** (-300 Midnite, L5: 80%, L10: 90%) |
| **CWS @ KC** | MLB | 9.5 OVER (+150) | 0.4036 | 0.4018 | +0.45% | `LONGSHOT_PRICE, FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT` | **PASS** | **0.0u** | **CWS Alt TT Over 2.5** (-325 Hard Rock, L5: 100%, L10: 90%) |
| **CLE @ BOS** | MLB | 7.5 OVER (+115) | 0.4669 | 0.4085 | -12.18% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT` | **PASS** | **0.0u** | **CLE Alt TT Over 2.5** (-195 Hard Rock, L5: 80%, L10: 90%) |
| **ARI @ COL** | MLB | 11.5 OVER (+114) | 0.4665 | 0.4082 | -12.63% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT` | **PASS** | **0.0u** | *None qualifying* (Fail closed) |
| **WSH @ DET** | MLB | 7.5 UNDER (+111) | 0.4711 | 0.4105 | -13.38% | `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT` | **PASS** | **0.0u** | **WSH Alt TT Over 2.5** (-185 Hard Rock, L5: 80%, L10: 80%) |

---

## 2. Granular Game-by-Game Situational Pre-Analysis

### 2.1 Milwaukee Brewers @ Philadelphia Phillies
- **Market Line**: Total 7.5 (Over +108 Novig / Under -108 to -128)
- **Model Output**: `fair_total = 7.5`, `market_consensus_prob = 0.4852`, `recency_hit_prob = 0.7000`, `final_blended_prob = 0.5926`, `edge_pct = +23.26%`
- **Quality Flags**: `totals_model_divergence`, `MODEL_DIVERGENCE_SHADOW_GATE`
- **Market Dynamics & Movement**: Opened at 8.0 (-120 Over / +100 Under), steamed down across the key 8.0 threshold to 7.5 (+108 Over / -108 Under). Institutional syndicates bought Under 8.0 aggressively.
- **Starting Pitching Form & Splits**:
  - *Logan Henderson (MIL RHP)*: 10-3, 2.51 ERA, 0.81 WHIP, 10.32 K/9 in 93.1 IP. Exceptional command; allowed 0.5 or fewer first-inning runs in 13 of his last 14 starts. Completed >= 14.5 outs in 16 consecutive appearances.
  - *Aaron Nola (PHI RHP)*: 7-10, 4.49 ERA, 1.34 WHIP, 9.04 K/9 in 168.1 IP. Dominant late-season form: 8 consecutive starts allowing 2.5 or fewer earned runs (averaging 1.2 ER/game).
- **Bullpen Utilization & Strain**:
  - *Milwaukee*: Fully rested leverage core (Romero, Hall, Senzatela on 2+ days rest).
  - *Philadelphia*: Top leverage arms worked yesterday (Jhoan Duran 24 PCL3, Jose Alvarado 19 PCL3), but setup depth remains fresh.
- **Weather & Environmental Constraints**:
  - Citizens Bank Park: Temperature 64.7°F, Humidity 57.0%.
  - Wind: **15.0 mph ENE** (relative angle 193.0°). Severe inward crosswind blowing from right-center directly back toward home plate, depressing ball carry and home run trajectory.
- **Synthesis & Verdict**:
  - The +23.26% edge is an artifact of Phillies home games hitting Overs against back-of-rotation arms (15 of last 19). Against Henderson's 0.81 WHIP and Nola's 1.2 ER streak, facing a 15 mph inward wind and downward line steam, betting Over 7.5 is completely contraindicated.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **PHI Alt Team Total OVER 2.5** (-180 Hard Rock | L5: 100%, L10: 90%)
  - **MIL Alt Team Total OVER 1.5** (-650 Hard Rock | L5: 80%, L10: 90%)

---

### 2.2 San Diego Padres @ Los Angeles Dodgers
- **Market Line**: Total 8.5 (Over +113 Novig / Under -115 to -130)
- **Model Output**: `fair_total = 8.0`, `market_consensus_prob = 0.4773`, `recency_hit_prob = 0.6500`, `final_blended_prob = 0.5637`, `edge_pct = +20.06%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG, totals_model_divergence`
- **Market Dynamics & Movement**: Opened at 8.5 (+100 Over / -122 Under), moved to 8.5 (+113 Over / -115 Under). Public ticket split is 33% Over / 67% Under, but **100% of tracked money handle is on Under 8.5**.
- **Starting Pitching Form & Splits**:
  - *Robbie Ray (SD LHP)*: 13-8, 3.60 ERA, 1.33 WHIP, 7.65 K/9. Struggles against LAD lineup (0-1, 5.79 ERA, 1.93 WHIP in 4.2 IP).
  - *Yoshinobu Yamamoto (LAD RHP)*: 14-8, 2.51 ERA, 0.86 WHIP, 8.95 K/9. Historically stifles San Diego: 2-1, 1.35 ERA, 0.85 WHIP, 22 Ks in 20.0 IP.
- **Bullpen Utilization & Strain**:
  - *San Diego*: Mason Miller rested (1.21 ERA, 48.2% K rate, 2 days rest).
  - *Los Angeles*: Heavily taxed bullpen with 6 relievers pitching yesterday on 0 days rest (Alex Vesia, Kyle Hurt, Blake Treinen, Brock Stewart, Edwin Díaz with 9.72 ERA and 61 PCL5, Jack Dreyer).
- **Weather & Venue**:
  - Dodger Stadium: 75.6°F, Humidity 64.0%. Wind 6.1 mph SW (rel 359.0° — dead inward from center field).
- **Synthesis & Verdict**:
  - The model exhibited a severe directional conflict: `fair_total = 8.0` is lower than the 8.5 headline line, yet the algorithm selected OVER 8.5 solely due to San Diego's 65% L10 Over hit rate. Backing Over 8.5 fades Yamamoto's elite splits and opposes 100% of sharp money.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **SD Alt Team Total OVER 2.5** (-170 Hard Rock | L5: 80%, L10: 90%) — targets the exhausted Dodgers bullpen.
  - **LAD Alt Team Total OVER 2.5** (-450 Hard Rock | L5: 100%, L10: 90%) — targets Robbie Ray's vulnerability.

---

### 2.3 Cincinnati Reds @ Atlanta Braves
- **Market Line**: Total 7.5 (Under -111 Novig / Over +107)
- **Model Output**: `fair_total = 7.5`, `market_consensus_prob = 0.5245`, `recency_hit_prob = 0.3500`, `final_blended_prob = 0.4373`, `edge_pct = -16.88%`
- **Quality Flags**: `totals_model_divergence`, `EDGE_BELOW_4PCT; MODEL_DIVERGENCE_SHADOW_GATE`
- **Market Dynamics & Movement**: Opened at 7.5 (-105 Over / -115 Under). Public ticket split is 86% on Under, but money handle is 54% on Over (sharp reverse line movement favoring Over).
- **Starting Pitching Form & Splits**:
  - *Andrew Abbott (CIN LHP)*: 6-11, 4.70 ERA, 1.47 WHIP, 7.08 K/9 in 159 IP. Elevated fly-ball rate vulnerable to right-handed power.
  - *Chris Sale (ATL LHP)*: 14-9, 2.18 ERA, 0.99 WHIP, 10.89 K/9 in 157 IP. Cy Young frontrunner.
- **Bullpen Utilization & Strain**:
  - *Cincinnati*: Fatigued relief corps (Burke on 0 days rest, Mey and Spiers high pitch load).
  - *Atlanta*: 100% rested bullpen; zero arms on back-to-back appearances.
- **Weather Dynamics**:
  - Truist Park: 73.3°F, Humidity 76.8%, Rain chance 19.0%. Warm, humid air enhances ball flight carry.
- **Synthesis & Verdict**:
  - Raw recency Under rate was 35%, generating a strongly negative edge (-16.88%). Sharp syndicates are betting the Over (54% handle on 14% tickets) to exploit Abbott and Cincinnati's gassed pen in warm air. Under 7.5 is completely non-actionable.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **ATL Alt Team Total OVER 2.5** (-400 Hard Rock | L5: 80%, L10: 90%)
  - **ATL Alt Team Total OVER 3.5** (-185 Hard Rock | L5: 80%, L10: 80%)

---

### 2.4 Miami Marlins @ Chicago Cubs
- **Market Line**: Total 6.5 (Under +125 Novig / Over -130 to -145)
- **Model Output**: `fair_total = 7.0`, `market_consensus_prob = 0.4499`, `recency_hit_prob = 0.0500`, `final_blended_prob = 0.2499`, `edge_pct = -43.76%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG, totals_model_divergence`
- **Market Dynamics & Movement**: Total opened at a suppressed 6.5 (-142 Over / +116 Under) due to weather reports, settling at 6.5 (-130 Over / +125 Under).
- **Starting Pitching Form & Splits**:
  - *Ryan Gusto (MIA RHP)*: 1-5, 4.14 ERA, 1.44 WHIP; vs Cubs: 0-1, 9.64 ERA, 2.57 WHIP in 4.2 IP.
  - *Kevin Gausman (CHC RHP)*: 9-12, 4.55 ERA, 1.27 WHIP, 9.26 K/9; vs Marlins: 1.80 ERA in 5.0 IP.
- **Bullpen Utilization & Strain**:
  - *Miami*: Entire bullpen is in deep exhaustion: 7 relievers on 0 days rest (Ralston, Vodnik 5.08 ERA, Gibson, Ekness, Petersen, Zuber 5.67 ERA, Faucher).
  - *Chicago*: High usage on Peterson (83 pitches in 5 days), but closer depth available.
- **Weather & Venue**:
  - Wrigley Field: 63.1°F, Humidity 79.6%. Wind **12.8 mph ENE** (rel 192.0° — blowing hard directly inward off Lake Michigan).
- **Synthesis & Verdict**:
  - Massive model inversion: `fair_total = 7.0` is above the 6.5 line, yet the algorithm selected Under 6.5. Marlins games went Over in 12 of 15, causing an extreme recency Under hit rate of 5% and a -43.76% edge anomaly. Betting Under 6.5 with 7 Miami relievers pitching on zero rest is catastrophic.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **CHC Alt Team Total OVER 2.5** (-250 Midnite | L5: 80%, L10: 90%)
  - **MIA Alt Team Total OVER 1.5** (-333 Midnite | L5: 100%, L10: 100%)

---

### 2.5 Dallas Wings @ Seattle Storm (WNBA)
- **Market Line**: Total 172.5 (Under +117 Novig / Over -104 to -135)
- **Model Output**: `fair_total = 173.5`, `market_consensus_prob = 0.4797`, `recency_hit_prob = 0.5000`, `final_blended_prob = 0.4898`, `edge_pct = +6.30%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG`
- **Market Dynamics & Movement**: Total opened at 170.5 and experienced violent steam upward by +3.0 points to 173.5 across 10 distinct book updates.
- **Personnel & Roster Constraints**:
  - *Seattle*: Missing primary defensive anchor and rim protector Ezi Magbegor (Knee Surgery, Out for Season), Katie Lou Samuelson, Zia Cooke.
  - *Dallas*: Missing starting frontcourt defenders Alysha Clark (Face) and Alanna Smith (Leg). Paige Bueckers in dominant scoring form (24.8 PPG road, 12 straight games over 24.5 P+R).
- **Synthesis & Verdict**:
  - Model fair total adjusted up to 173.5, but the system picked Under 172.5 on a stale price, fighting a 3-point sharp steam wave triggered by vacated rim defense on both rosters.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **DAL Alt Team Total OVER 85.5** (-300 Midnite | L5: 80%, L10: 90%)

---

### 2.6 Chicago White Sox @ Kansas City Royals
- **Market Line**: Total 9.5 (Over +150 Prophetx / Under -180)
- **Model Output**: `fair_total = 8.5`, `market_consensus_prob = 0.4036`, `recency_hit_prob = 0.4000`, `final_blended_prob = 0.4018`, `edge_pct = +0.45%`
- **Quality Flags**: `LONGSHOT_PRICE, FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG`
- **Synthesis & Verdict**:
  - Market consensus total is 8.5. The row captured an alternate ladder line (9.5 Over @ +150). `fair_total = 8.5` is a full run below the 9.5 line.
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **CWS Alt Team Total OVER 2.5** (-325 Hard Rock | L5: 100%, L10: 90%)
  - **KC Alt Team Total OVER 1.5** (-700 Hard Rock | L5: 100%, L10: 80%)

---

### 2.7 Cleveland Guardians @ Boston Red Sox
- **Market Line**: Total 7.5 (Over +115 Novig / Under -118)
- **Model Output**: `fair_total = 7.0`, `market_consensus_prob = 0.4669`, `recency_hit_prob = 0.3500`, `final_blended_prob = 0.4085`, `edge_pct = -12.18%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG`
- **Synthesis & Verdict**:
  - `fair_total = 7.0` conflicts with selecting Over 7.5. Weather at Fenway Park: 56.6°F, 13.4 mph wind blowing in from right field. Edge is negative (-12.18%).
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **CLE Alt Team Total OVER 2.5** (-195 Hard Rock | L5: 80%, L10: 90%)

---

### 2.8 Arizona Diamondbacks @ Colorado Rockies
- **Market Line**: Total 11.5 (Over +114 Prophetx / Under -130)
- **Model Output**: `fair_total = 11.0`, `market_consensus_prob = 0.4665`, `recency_hit_prob = 0.3500`, `final_blended_prob = 0.4082`, `edge_pct = -12.63%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG`
- **Synthesis & Verdict**:
  - Coors Field total 11.5 carries a fair total of 11.0. Selection was Over 11.5 with negative edge (-12.63%).
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - No qualifying alternate team total met the strict `L5 >= 80%` and `L10 >= 75%` OVER runs threshold (fail closed).

---

### 2.9 Washington Nationals @ Detroit Tigers
- **Market Line**: Total 7.5 (Under +111 Prophetx / Over -125)
- **Model Output**: `fair_total = 8.0`, `market_consensus_prob = 0.4711`, `recency_hit_prob = 0.3500`, `final_blended_prob = 0.4105`, `edge_pct = -13.38%`
- **Quality Flags**: `FAIR_TOTAL_DIVERGENCE, FAIR_TOTAL_SIDE_CONFLICT, SOURCE_INTEGRITY_FLAG`
- **Synthesis & Verdict**:
  - Line fell from 8.0 to 7.5, fair total is 8.0 (> 7.5 line), conflicting with Under 7.5 selection. Negative edge (-13.38%).
  - **Verdict: PASS (0.0 units)**.
- **Automated Fallback**:
  - **WSH Alt Team Total OVER 2.5** (-185 Hard Rock | L5: 80%, L10: 80%)

---

## 3. Strict Pre-requisites Required to Authorize a 0.5-Unit Play

Under calibrated pipeline operations, no game total carrying an unresolved directional flag may ever receive positive unit sizing. To authorize an executable 0.5-unit bet on a divergent total on future slates, all 5 gating conditions must be satisfied:

1. **Directional Concurrence**:
   The market `fair_total` must strictly support the selection side (`fair_total >= headline_line + 0.5` for OVER, or `fair_total <= headline_line - 0.5` for UNDER). Zero `FAIR_TOTAL_SIDE_CONFLICT` flags permitted.
2. **Calibrated Positive Edge ($\ge +4.0\%$)**:
   The post-shrinkage probability ($\alpha = 100.0$ and 3.5% adjustment cap) must produce an edge $\ge +4.0\%$ against the devigged market consensus.
3. **Money Flow Alignment**:
   Sharp money handle percentage must not heavily oppose the pick (no $> 75\%$ handle against the selection).
4. **Environmental Verification**:
   Venue weather must not actively oppose the bet (e.g. no sustained inward wind $\ge 12.0\text{ mph}$ for Overs; no hot, humid air with outward wind for Unders).
5. **Starter & Bullpen Health Integrity**:
   No unmodeled bullpen exhaustion ($\ge 5$ relievers on 0 days rest) or undisclosed starter injury distress.

---

## 4. Alternate Team Totals Invariants & Cross-Game Parlay Governance

Per Outlier Multi-Ent House Rule 3 (`docs/ENT-SYNC-GLOBAL-PROMPT.md`):
- **Whitelisted Markets**: MLB Alternate Team Props are strictly restricted to team run totals (`RUNS`, `R`, `TOTAL_RUNS`, `TOTAL`, `TEAM_TOTAL`).
- **Side Restriction**: MLB Alternate Team Totals are strictly **OVER runs only**. UNDER team totals are discarded at generation.
- **Cross-Game Parlay Rule**: High-probability alternate team totals (e.g. Over 1.5 or Over 2.5 at odds of -170 to -650) **must be parlayed across different games**. Single-game parlays (SGPs) combining an alternate team total with another market from the same matchup are strictly prohibited.
