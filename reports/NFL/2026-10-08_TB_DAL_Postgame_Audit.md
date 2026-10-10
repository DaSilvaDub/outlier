# TB @ DAL — POSTGAME PREDICTION AUDIT & CALIBRATION ANALYSIS
**Date of Event:** 2026-10-08 | **Audit Completed:** 2026-10-09  
**Matchup:** Tampa Bay Buccaneers @ Dallas Cowboys (Week 5 Thursday Night Football)  
**Venue:** AT&T Stadium (Arlington, TX — Indoor Dome)  
**Authoritative Score:** **Tampa Bay Buccaneers 24, Dallas Cowboys 16**  

---

## 1. Executive Verdict & Summary

- **Game Result:** The Tampa Bay Buccaneers (1–4) upset the Dallas Cowboys (2–3) outright, winning **24–16**.
- **Model Result Prediction:** **FAILED on Spread & Total.** The pre-game model predicted Dallas 30, Tampa Bay 20 (Spread: DAL -7.5, Total: OVER 48.5). Tampa Bay won outright (+385 ML / covered +7.5), and the game finished well UNDER the total (40 combined points vs. 48.5 line).
- **Player Prop Results:** **4 WINS, 1 LOSS (80.0% Win Rate)** on Hard Rock Alternate Floor recommendations. However, due to severe minus-money juice (-325 to -700), the portfolio generated **-0.092 units (-1.84% ROI)**. A single catastrophic loss (CeeDee Lamb quad injury at -600) erased the profits of four winning legs.
- **Tactical Reality:** The model's primary trench mismatch — that Tampa Bay's run game led by Bucky Irving would overpower Dallas's leaky run front — materialized with explosive perfection (Irving: 21 carries, 165 yards, 2 TDs). However, Dallas suffered an offensive collapse driven by Dak Prescott's 2 interceptions, red-zone stalls, and CeeDee Lamb's 1st-half exit.

---

## 2. Verified Postgame Box Score

### Score by Quarter
| Team | Q1 | Q2 | Q3 | Q4 | Final |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Tampa Bay Buccaneers** | 7 | 10 | 0 | 7 | **24** |
| **Dallas Cowboys** | 3 | 7 | 3 | 3 | **16** |

### Verified Player Statistics
- **Passing:**
  - **Dak Prescott (DAL):** 24/42, 316 yards, 1 TD, 2 INTs, 3 sacks taken (71.1 Passer Rating).
  - **Jalon Daniels (TB):** 19/25, 189 yards, 1 TD, 1 INT, 1 sack taken.
- **Rushing:**
  - **Bucky Irving (TB):** 21 carries, 165 yards (7.9 YPC, 72-yard long, 1 TD). Career-high performance.
  - **Javonte Williams (DAL):** 12 carries, 45 yards (3.8 YPC, 1 TD).
  - **Jalon Daniels (TB):** 6 carries, 24 yards.
- **Receiving:**
  - **George Pickens (DAL):** 9 receptions, 130 yards, 1 TD (14 targets). Absorbed primary target share.
  - **Bucky Irving (TB):** 3 receptions, 28 yards, 1 TD.
  - **Chris Godwin Jr. (TB):** 5 receptions, 54 yards.
  - **CeeDee Lamb (DAL):** 1 reception, 4 yards (targeted twice before leaving early due to an acute quad injury).

---

## 3. Game Lines & Scoring Audit

| Market | Pre-Game Line | Model Projection | Verified Actual | Error Margin | Prediction Grade |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Spread** | DAL -7.5 (-110) | DAL -10.0 | **TB +8 (TB 24, DAL 16)** | -15.5 pts | **FAILED (LOSS)** |
| **Total** | 48.5 (-110) | 50.0 (OVER) | **40.0 Points** | -8.5 pts | **FAILED (LOSS)** |
| **DAL Team Total** | 28.5 (-110) | 30.0 (OVER) | **16 Points** | -14.0 pts | **FAILED (LOSS)** |
| **TB Team Total** | 20.0 (-110) | 20.0 (PUSH) | **24 Points** | +4.0 pts | **CORRECT (DIR)** |

### Analytical Errors on Game Lines:
1. **Spread & Script Blind Spot:** The model leaned heavily into Dallas being a home favorite in a dome, anchoring on Dak Prescott's prior week (355 yards vs HOU). It failed to weight the risk of Dallas's defensive collapse against the run (Dallas gave up 165 yards to Bucky Irving alone).
2. **Total Overestimation (+10 points):** The model applied a double DIDF boost (+3.5 to both teams) and a dome pace upgrade. While Tampa Bay scored 24 points, Dallas stalled repeatedly in scoring territory (converting just 1 touchdown on 4 red-zone trips, with 2 Prescott turnovers and 3 field goals).

---

## 4. Player Props Audit (Hard Rock Alternate Floors)

| Player | Prop Market | Hard Rock Line | Quoted Odds | Verified Actual | Margin vs Line | Result | Unit P/L ($100 Flat) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Bucky Irving** | RUSH_YDS | OVER 39.5 | `-325` | **165 yds** | **+125.5 yds** | **WIN** | **+$30.77** |
| **Javonte Williams** | RUSH_YDS | OVER 39.5 | `-700` | **45 yds** | **+5.5 yds** | **WIN** | **+$14.29** |
| **CeeDee Lamb** | REC_YDS | OVER 49.5 | `-600` | **4 yds** | **-45.5 yds** | **LOSS** | **-$100.00** |
| **Dak Prescott** | PASS_YDS | OVER 224.5 | `-450` | **316 yds** | **+91.5 yds** | **WIN** | **+$22.22** |
| **George Pickens** | REC_YDS | OVER 39.5 | `-425` | **130 yds** | **+90.5 yds** | **WIN** | **+$23.53** |

### Financial Performance:
- **Total Recommendations Audited:** 5
- **Record:** 4 Wins, 1 Loss (**80.0% Win Rate**)
- **Total Units Risked:** 5.0 units ($500.00)
- **Net Profit / Loss:** **-0.092 units (-$9.19)**
- **ROI:** **-1.84%**

---

## 5. Mathematical Reassessment of the Alt-Floor Strategy

The audit exposes a structural flaw in betting high-juice alternate floors as standalone straight bets:

### The Negative Asymmetry Trap:
1. **Break-Even Barrier:**
   - At `-600`, the required win rate to break even is $\frac{600}{700} = 85.71\%$.
   - At `-700`, the required win rate is $\frac{700}{800} = 87.50\%$.
2. **In-Game Injury & Catastrophic Risk:**
   - In any NFL game, a skill player carries an inherent ~3% to 6% chance of in-game injury, early ejection, or blowout benching.
   - Even when CeeDee Lamb's line was discounted by **50 yards** (49.5 vs 99.5 consensus), his quad injury occurred in the first quarter after 1 catch for 4 yards.
   - That single loss (-1.00 unit) completely wiped out the collective profits from **four** winning wagers (+0.308 + +0.143 + +0.222 + +0.235 = +0.908 units).
3. **Rule Revision for Alt-Floor Lines:**
   - **Never wager straight bets on alternate props with odds worse than `-250`.**
   - Alt-floor ladders (-300 to -600) should **only** be deployed as parlay legs where payout multipliers can offset tail risk, never as isolated negative-EV juice traps.

---

## 6. What the Analysis Got Right vs. What It Got Wrong

### What the Analysis Got Right:
- **Bucky Irving Mismatch:** Correctly identified that Dallas's run defense (150.2 yds/g allowed) was a sieve missing linebackers. Irving was rated the #1 Confidence play on the board and delivered a monster 165-yard clinic.
- **Dak Prescott Passing Volume:** Correctly predicted that Tampa Bay's stout run defense (78.5 yds allowed) would force Prescott to throw 40+ times. Prescott threw 42 passes for 316 yards, easily surpassing both the 224.5 floor and 249.5 consensus lines.
- **George Pickens Target Expansion:** Pickens blew past his 39.5 yard line, posting 130 yards on 9 receptions.

### What the Analysis Got Wrong:
- **Underestimating Dallas Turnover / Red-Zone Collapse:** Dak threw 2 interceptions and took 3 sacks. Dallas scored only 16 points despite 316 passing yards.
- **Assuming Dome Venue Guarantees High Points:** The total finished at 40 points (well under 48.5) because Tampa Bay dominated time of possession on the ground, shortening the game.
- **Ignoring Extreme Minus-Money Downside:** The model permitted -600 and -700 straight bets on players with non-zero injury probability.

---

## 7. Model Scorecard (0–10 Scale)

1. **Result Prediction Accuracy:** `3.0 / 10` (Missed spread and total).
2. **Statistical Reasoning:** `6.5 / 10` (Bucky Irving and Dak yardage projections were spot on).
3. **Tactical / Game Understanding:** `6.0 / 10` (Anticipated Tampa's run game and Dallas's pass volume, but failed to model pace reduction).
4. **Player / Team Assessment:** `7.5 / 10` (Accurate on 4 of 5 skill players; Lamb failed solely due to injury).
5. **Risk & Uncertainty Handling:** `2.0 / 10` (Laying -600 and -700 straight bets was mathematically reckless).
6. **Overall Analysis Quality:** `5.0 / 10` (Sound statistical fundamentals undermined by poor risk pricing and game pace error).

---

## 8. Actionable Pipeline & Model Upgrades

1. **Max-Juice Floor Filter:** Add a hard cap in `outlier_nfl.alt_floors`: straight alternate bets must not exceed `-250` American odds.
2. **Run-Pace Haircut on Game Totals:** When an underdog possesses a massive ground mismatch (opp run D > 140 yds/g), apply a negative pace modifier to the total projection, because ground dominance burns the game clock.
3. **Injury Fragility Haircut on High-Volume WRs:** Reduce unit allocation on heavily targeted receivers facing short turnaround Thursday games.
