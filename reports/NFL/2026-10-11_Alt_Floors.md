# NFL Sportsbook Alternate Floor Props — 2026-10-11

**Target Book**: `HARDROCK` (Dynamic floor ladders with retail consensus fallback)  
**Methodology**: Replaces static, arbitrary thresholds (fixed 50 rush / 200 pass) with player-specific floor lines. Evaluates true hit rate convergence (L5/L10/Season), safety cushion below consensus line, and environmental factors.  
**Juice Cap Policy**: Standalone straight wagers require odds >= -250. Odds worse than -250 (e.g. -325 to -700) are flagged `PARLAY ONLY` due to extreme downside injury asymmetry.

---

## Master Confidence Ranking (Top 1–9 Overall)

| Rank | Player | Team | Market | Alt Line | Consensus | Cushion | Odds | Play Type | L5 Hit | L10 Hit | Confidence |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **#1** | **Trevor Lawrence** | JAX | Passing Yards | **OVER 174.5** | 224.5 | +50.0 (22.3%) | `HARDROCK -550` | `PARLAY_ONLY` | 100% | 100% | **0.907** |
| **#2** | **Joe Burrow** | CIN | Passing Yards | **OVER 199.5** | 249.5 | +50.0 (20.0%) | `HARDROCK -600` | `PARLAY_ONLY` | 100% | 100% | **0.906** |
| **#3** | **Jaylen Warren** | PIT | Rushing Yards | **OVER 39.5** | 69.5 | +30.0 (43.2%) | `HARDROCK -750` | `PARLAY_ONLY` | 100% | 80% | **0.891** |
| **#4** | **Derrick Henry** | BAL | Rushing Yards | **OVER 49.5** | 79.5 | +30.0 (37.7%) | `HARDROCK -550` | `PARLAY_ONLY` | 100% | 100% | **0.882** |
| **#5** | **Drake Maye** | NE | Passing Yards | **OVER 174.5** | 249.5 | +75.0 (30.1%) | `HARDROCK -575` | `PARLAY_ONLY` | 100% | 90% | **0.880** |
| **#6** | **Tee Higgins** | CIN | Receiving Yards | **OVER 39.5** | 60.5 | +21.0 (34.7%) | `HARDROCK -325` | `PARLAY_ONLY` | 100% | 90% | **0.876** |
| **#7** | **Chris Olave** | NO | Receiving Yards | **OVER 49.5** | 79.5 | +30.0 (37.7%) | `HARDROCK -475` | `PARLAY_ONLY` | 100% | 80% | **0.850** |
| **#8** | **Nico Collins** | HOU | Receiving Yards | **OVER 39.5** | 79.5 | +40.0 (50.3%) | `HARDROCK -800` | `PARLAY_ONLY` | 80% | 90% | **0.844** |
| **#9** | **Aaron Jones Sr.** | MIN | Rushing Yards | **OVER 39.5** | 69.5 | +30.0 (43.2%) | `HARDROCK -800` | `PARLAY_ONLY` | 100% | 80% | **0.822** |

---

## Category Breakdowns

### Passing Yards Alternate Floors (Top 3)

- **#1 Trevor Lawrence (JAX vs PHI) — OVER 174.5 Passing Yards**
  - **Sportsbook Quote**: `HARDROCK -550` (Implied: 84.6%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +50.0 yards below consensus line of 224.5 (22.3% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 100% (10/10) | Season: 100%
  - **Confidence Score**: `0.907` (Master Rank: #1)

- **#2 Joe Burrow (CIN vs MIA) — OVER 199.5 Passing Yards**
  - **Sportsbook Quote**: `HARDROCK -600` (Implied: 85.7%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +50.0 yards below consensus line of 249.5 (20.0% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 100% (10/10) | Season: 100%
  - **Confidence Score**: `0.906` (Master Rank: #2)

- **#3 Drake Maye (NE vs LV) — OVER 174.5 Passing Yards**
  - **Sportsbook Quote**: `HARDROCK -575` (Implied: 85.2%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +75.0 yards below consensus line of 249.5 (30.1% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 90% (9/10) | Season: 100%
  - **Confidence Score**: `0.880` (Master Rank: #5)

### Rushing Yards Alternate Floors (Top 3)

- **#1 Jaylen Warren (PIT vs IND) — OVER 39.5 Rushing Yards**
  - **Sportsbook Quote**: `HARDROCK -750` (Implied: 88.2%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +30.0 yards below consensus line of 69.5 (43.2% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 80% (8/10) | Season: 100%
  - **Confidence Score**: `0.891` (Master Rank: #3)

- **#2 Derrick Henry (BAL vs ATL) — OVER 49.5 Rushing Yards**
  - **Sportsbook Quote**: `HARDROCK -550` (Implied: 84.6%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +30.0 yards below consensus line of 79.5 (37.7% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 100% (10/10) | Season: 100%
  - **Confidence Score**: `0.882` (Master Rank: #4)

- **#3 Aaron Jones Sr. (MIN vs NO) — OVER 39.5 Rushing Yards**
  - **Sportsbook Quote**: `HARDROCK -800` (Implied: 88.9%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +30.0 yards below consensus line of 69.5 (43.2% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 80% (8/10) | Season: 100%
  - **Confidence Score**: `0.822` (Master Rank: #9)

### Receiving Yards Alternate Floors (Top 3)

- **#1 Tee Higgins (CIN vs MIA) — OVER 39.5 Receiving Yards**
  - **Sportsbook Quote**: `HARDROCK -325` (Implied: 76.5%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +21.0 yards below consensus line of 60.5 (34.7% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 90% (9/10) | Season: 100%
  - **Confidence Score**: `0.876` (Master Rank: #6)

- **#2 Chris Olave (NO vs MIN) — OVER 49.5 Receiving Yards**
  - **Sportsbook Quote**: `HARDROCK -475` (Implied: 82.6%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +30.0 yards below consensus line of 79.5 (37.7% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 80% (8/10) | Season: 100%
  - **Confidence Score**: `0.850` (Master Rank: #7)

- **#3 Nico Collins (HOU vs TEN) — OVER 39.5 Receiving Yards**
  - **Sportsbook Quote**: `HARDROCK -800` (Implied: 88.9%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +40.0 yards below consensus line of 79.5 (50.3% discount)
  - **Hit Rates**: L5: 80% (4/5) | L10: 90% (9/10) | Season: 100%
  - **Confidence Score**: `0.844` (Master Rank: #8)

---

## Non-actionable inventory

Floors listed for reference only: no executable quote, identity or injury evidence. Never ranked or recommended.

- Denzel Boston (CLE) — OVER 34.5 Receiving Yards [0bb4d01f15170aa3a47f03ff068aa807e677d488]: `no_sportsbook_quote`
- Harold Fannin Jr. (CLE) — OVER 34.5 Receiving Yards [0bb4d01f15170aa3a47f03ff068aa807e677d488]: `no_sportsbook_quote`
- Roman Wilson (PIT) — OVER 34.5 Receiving Yards [2e2cfae3dbcf1b99807f1c9b6745b88a7f767443]: `no_sportsbook_quote`
- Tyler Warren (IND) — OVER 34.5 Receiving Yards [2e2cfae3dbcf1b99807f1c9b6745b88a7f767443]: `no_sportsbook_quote`
- Antonio Williams (WAS) — OVER 34.5 Receiving Yards [370c68e3ab4737f5e7058ffef0575e9c194c974f]: `no_sportsbook_quote`
- Isaiah Likely (NYG) — OVER 34.5 Receiving Yards [370c68e3ab4737f5e7058ffef0575e9c194c974f]: `no_sportsbook_quote`
- Rome Odunze (CHI) — OVER 37.5 Receiving Yards [4e9a68831703fa6b98c614ce07ccb9854441a44d]: `no_sportsbook_quote`
- Wan'Dale Robinson (TEN) — OVER 35.5 Receiving Yards [6e4f7ce3edbca88f1eb4ac8510f6d091c10c0ffc]: `no_sportsbook_quote`
- Brenton Strange (JAX) — OVER 35.5 Receiving Yards [b6667856e14d440acc7a0501a6117421bc45a1f9]: `no_sportsbook_quote`
- Dontayvion Wicks (PHI) — OVER 34.5 Receiving Yards [b6667856e14d440acc7a0501a6117421bc45a1f9]: `no_sportsbook_quote`
- Jakobi Meyers (JAX) — OVER 38.5 Receiving Yards [b6667856e14d440acc7a0501a6117421bc45a1f9]: `no_sportsbook_quote`
- Tre Tucker (LV) — OVER 38.5 Receiving Yards [c1a97f73d0827f208f95fa84c6551eb4568672c3]: `no_sportsbook_quote`
- Tre' Harris (LAC) — OVER 39.5 Receiving Yards [f2d32122f27ca79a59a50ec2e369e8500e332cb6]: `no_sportsbook_quote`
- Devaughn Vele (NO) — OVER 34.5 Receiving Yards [f5cbe9191df7f36035ad76287a24db6829b8b27d]: `no_sportsbook_quote`
- Jeremiyah Love (ARI) — OVER 34.5 Rushing Yards [4d8349cfa9a34764ec5ec2cdae65fdc1f4285f67]: `no_sportsbook_quote`
- Will Shipley (PHI) — OVER 37.5 Rushing Yards [b6667856e14d440acc7a0501a6117421bc45a1f9]: `no_sportsbook_quote`
- Ashton Jeanty (LV) — OVER 39.5 Rushing Yards [c1a97f73d0827f208f95fa84c6551eb4568672c3]: `no_sportsbook_quote`
- Mike Washington Jr. (LV) — OVER 39.5 Rushing Yards [c1a97f73d0827f208f95fa84c6551eb4568672c3]: `no_sportsbook_quote`
- Keaton Mitchell (LAC) — OVER 34.5 Rushing Yards [f2d32122f27ca79a59a50ec2e369e8500e332cb6]: `no_sportsbook_quote`
- Omarion Hampton (LAC) — OVER 36.5 Rushing Yards [f2d32122f27ca79a59a50ec2e369e8500e332cb6]: `no_sportsbook_quote`
- Ollie Gordon II (MIA) — OVER 34.5 Rushing Yards [fe57943f0fcc16a274dc3b1f340769a74a722d5f]: `no_sportsbook_quote`

---

## Parlay Construction Guidelines

1. **Low-Floor Same-Game Parlay (SGP)**: Anchor correlating positive script legs in dome/neutral matchups (e.g. Starting QB Passing Floor + Workhorse RB Rushing Floor).
2. **Cross-Game High-Confidence Parlay**: Select the top 1 play from each category (Pass #1 + Rush #1 + Rec #1) to capture cross-game diversification with massive statistical floors.
3. **Alt Floor Side Restriction**: Per pipeline invariants, alternate player props must be parlayed across different games when combining alt lines.
4. **Juice Cap Enforcement**: Props with odds worse than -250 must NEVER be wagered as straight bets; allocate them exclusively as SGP or cross-game parlay anchors.
