# NFL Sportsbook Alternate Floor Props — 2026-09-13

**Target Book**: `HARDROCK` (Dynamic floor ladders with retail consensus fallback)  
**Methodology**: Replaces static, arbitrary thresholds (fixed 50 rush / 200 pass) with player-specific floor lines. Evaluates true hit rate convergence (L5/L10/Season), safety cushion below consensus line, and environmental factors.  
**Juice Cap Policy**: Standalone straight wagers require odds >= -250. Odds worse than -250 (e.g. -325 to -700) are flagged `PARLAY ONLY` due to extreme downside injury asymmetry.

---

## Master Confidence Ranking (Top 1–9 Overall)

| Rank | Player | Team | Market | Alt Line | Consensus | Cushion | Odds | Play Type | L5 Hit | L10 Hit | Confidence |
|:---:|:---|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|

---

## Category Breakdowns

### Passing Yards Alternate Floors (Top 3)

### Rushing Yards Alternate Floors (Top 3)

### Receiving Yards Alternate Floors (Top 3)

---

## Parlay Construction Guidelines

1. **Low-Floor Same-Game Parlay (SGP)**: Anchor correlating positive script legs in dome/neutral matchups (e.g. Starting QB Passing Floor + Workhorse RB Rushing Floor).
2. **Cross-Game High-Confidence Parlay**: Select the top 1 play from each category (Pass #1 + Rush #1 + Rec #1) to capture cross-game diversification with massive statistical floors.
3. **Alt Floor Side Restriction**: Per pipeline invariants, alternate player props must be parlayed across different games when combining alt lines.
4. **Juice Cap Enforcement**: Props with odds worse than -250 must NEVER be wagered as straight bets; allocate them exclusively as SGP or cross-game parlay anchors.
