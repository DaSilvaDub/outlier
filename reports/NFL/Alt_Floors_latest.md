# NFL Sportsbook Alternate Floor Props — 2026-10-15

**Target Book**: `HARDROCK` (Dynamic floor ladders with retail consensus fallback)  
**Methodology**: Replaces static, arbitrary thresholds (fixed 50 rush / 200 pass) with player-specific floor lines. Evaluates true hit rate convergence (L5/L10/Season), safety cushion below consensus line, and environmental factors.  
**Juice Cap Policy**: Standalone straight wagers require odds >= -250. Odds worse than -250 (e.g. -325 to -700) are flagged `PARLAY ONLY` due to extreme downside injury asymmetry.

---

## Master Confidence Ranking (Top 1–9 Overall)

| Rank | Player | Team | Market | Alt Line | Consensus | Cushion | Odds | Play Type | L5 Hit | L10 Hit | Confidence |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **#1** | **Jaxon Smith-Njigba** | SEA | Receiving Yards | **OVER 49.5** | 99.5 | +50.0 (50.3%) | `HARDROCK -800` | `PARLAY_ONLY` | 100% | 80% | **0.869** |
| **#2** | **J.K. Dobbins** | DEN | Rushing Yards | **OVER 39.5** | 49.5 | +10.0 (20.2%) | `HARDROCK -190` | `STRAIGHT` | 60% | 80% | **0.636** |
| **#3** | **Bo Nix** | DEN | Passing Yards | **OVER 149.5** | 199.5 | +50.0 (25.1%) | `HARDROCK -575` | `PARLAY_ONLY` | 60% | 70% | **0.625** |
| **#4** | **Sam Darnold** | SEA | Passing Yards | **OVER 174.5** | 199.5 | +25.0 (12.5%) | `HARDROCK -425` | `PARLAY_ONLY` | 60% | 60% | **0.558** |
| **#5** | **Emanuel Wilson** | SEA | Rushing Yards | **OVER 39.5** | 79.5 | +40.0 (50.3%) | `HARDROCK -600` | `PARLAY_ONLY` | 60% | 50% | **0.541** |
| **#6** | **Jaylen Waddle** | DEN | Receiving Yards | **OVER 39.5** | 49.5 | +10.0 (20.2%) | `HARDROCK -200` | `STRAIGHT` | 20% | 40% | **0.317** |

---

## Category Breakdowns

### Passing Yards Alternate Floors (Top 3)

- **#1 Bo Nix (DEN vs SEA) — OVER 149.5 Passing Yards**
  - **Sportsbook Quote**: `HARDROCK -575` (Implied: 85.2%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +50.0 yards below consensus line of 199.5 (25.1% discount)
  - **Hit Rates**: L5: 60% (3/5) | L10: 70% (7/10) | Season: 60%
  - **Confidence Score**: `0.625` (Master Rank: #3)

- **#2 Sam Darnold (SEA vs DEN) — OVER 174.5 Passing Yards**
  - **Sportsbook Quote**: `HARDROCK -425` (Implied: 81.0%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +25.0 yards below consensus line of 199.5 (12.5% discount)
  - **Hit Rates**: L5: 60% (3/5) | L10: 60% (6/10) | Season: 50%
  - **Confidence Score**: `0.558` (Master Rank: #4)

### Rushing Yards Alternate Floors (Top 3)

- **#1 J.K. Dobbins (DEN vs SEA) — OVER 39.5 Rushing Yards**
  - **Sportsbook Quote**: `HARDROCK -190` (Implied: 65.5%) | **Play Type**: `STRAIGHT`
  - **Safety Cushion**: +10.0 yards below consensus line of 49.5 (20.2% discount)
  - **Hit Rates**: L5: 60% (3/5) | L10: 80% (8/10) | Season: 60%
  - **Confidence Score**: `0.636` (Master Rank: #2)

- **#2 Emanuel Wilson (SEA vs DEN) — OVER 39.5 Rushing Yards**
  - **Sportsbook Quote**: `HARDROCK -600` (Implied: 85.7%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +40.0 yards below consensus line of 79.5 (50.3% discount)
  - **Hit Rates**: L5: 60% (3/5) | L10: 50% (5/10) | Season: 60%
  - **Confidence Score**: `0.541` (Master Rank: #5)

### Receiving Yards Alternate Floors (Top 3)

- **#1 Jaxon Smith-Njigba (SEA vs DEN) — OVER 49.5 Receiving Yards**
  - **Sportsbook Quote**: `HARDROCK -800` (Implied: 88.9%) | **Play Type**: `PARLAY_ONLY`
  - **Safety Cushion**: +50.0 yards below consensus line of 99.5 (50.3% discount)
  - **Hit Rates**: L5: 100% (5/5) | L10: 80% (8/10) | Season: 100%
  - **Confidence Score**: `0.869` (Master Rank: #1)

- **#2 Jaylen Waddle (DEN vs SEA) — OVER 39.5 Receiving Yards**
  - **Sportsbook Quote**: `HARDROCK -200` (Implied: 66.7%) | **Play Type**: `STRAIGHT`
  - **Safety Cushion**: +10.0 yards below consensus line of 49.5 (20.2% discount)
  - **Hit Rates**: L5: 20% (1/5) | L10: 40% (4/10) | Season: 20%
  - **Confidence Score**: `0.317` (Master Rank: #6)

---

## Non-actionable inventory

Floors listed for reference only: no executable quote, identity or injury evidence. Never ranked or recommended.

- None

---

## Parlay Construction Guidelines

1. **Low-Floor Same-Game Parlay (SGP)**: Anchor correlating positive script legs in dome/neutral matchups (e.g. Starting QB Passing Floor + Workhorse RB Rushing Floor).
2. **Cross-Game High-Confidence Parlay**: Select the top 1 play from each category (Pass #1 + Rush #1 + Rec #1) to capture cross-game diversification with massive statistical floors.
3. **Alt Floor Side Restriction**: Per pipeline invariants, alternate player props must be parlayed across different games when combining alt lines.
4. **Juice Cap Enforcement**: Props with odds worse than -250 must NEVER be wagered as straight bets; allocate them exclusively as SGP or cross-game parlay anchors.
