# 2026-10-23 MLB Postgame Audit — TB at MIL

Audit date: 2026-10-24. Frozen source: `packs/2026-10-23/` (candidate snapshot at 2026-10-23 17:10 ET; pack manifest later marked `PARTIAL`). Game: Tampa Bay Rays at Milwaukee Brewers, World Series Game 1, MLB game 849805.
Scope: all seven saved candidate rows, including flagged and Board B rows. None was `actionable=true`; all corresponding decision records show `STAND_DOWN`, 0 units. This audits forecast direction and line settlement separately from actual betting. No wager execution/stake evidence was present.

## 1. Verified Game Result and Key Facts

Official MLB StatsAPI feed marks game 849805 `Final`, completed after nine innings. Milwaukee beat Tampa Bay 3–2. Team totals: MIL 8 hits, 0 errors; TB 7 hits, 0 errors. The fifth run arrived in Tampa Bay's ninth-inning rally; no extra innings.

Jacob Misiorowski started for Milwaukee: 5.0 IP, 78 pitches, 3 K, 1 BB, 4 H, 1 ER. Nick Martinez started for Tampa Bay: 5.0 IP, 73 pitches, 5 K, 0 BB, 5 H, 2 ER. Drew Rasmussen was listed in the box score roster but had no pitching appearance. The AP recap independently confirms the score, Misiorowski's three strikeouts and five-inning start, and Martinez's five strikeouts over five innings. Sources: [MLB Gameday](https://www.mlb.com/gameday/rays-vs-brewers/2026/10/23/849805/live), [AP recap](https://apnews.com/article/world-series-rays-brewers-game-1-049d8efa7d3d7c8fc8a4e4b88d8dfe77). Exact pitching lines were read from the official [MLB StatsAPI game feed](https://statsapi.mlb.com/api/v1.1/game/849805/feed/live).

Pregame identity issue: the saved candidate names Rasmussen as the Tampa Bay strikeout prop pitcher, but he did not start; Martinez did. The saved `identity_audit.json` flags a pitcher identity mismatch and fail-closed status. This is a pregame data/role error caught by the gate, not a miss to score as a wager.

## 2. Executive Verdict: Prediction Quality vs. Betting Outcomes

- Result directions: 5 settled selections won; 1 lost; 1 ungradable (Rasmussen did not pitch). These are retrospective candidate-direction/line outcomes, not a record of bets placed.
- Actionable bets: 0 of 7. Every row was `actionable=false`; every decision was `STAND_DOWN` with 0 units. Therefore realized bankroll return and ROI are not available and must not be inferred.
- Process: the most important success was blocking all rows. The board was snapshot-aged, low quality for several markets, contained conflicting prices/market definitions, and had incomplete or unsupported probabilities. A 5–1 settled directional record from one game does not establish model skill or validate any signal.
- WNBA: no candidate/game was present in this frozen pack; there is no WNBA result to audit or WNBA playbook change to make.

## 3. Complete Pick-by-Pick Audit Scorecard

| Prediction | Original line/odds | Actual outcome | Grade | Original confidence | Evidence | Explanation |
|---|---|---|---|---|---|---|
| Drew Rasmussen strikeouts OVER 3.5 | +119, Underdog; 2026-10-23 17:10:20 ET | Rasmussen did not pitch; Martinez started for TB | UNGRADABLE | Board B; proxy p=54.87%; LOW quality; actionable=false | MLB game feed and saved identity audit | Listed player did not participate. Proxy market probability and identity mismatch are disqualifying; no wager/units. |
| MIL three-way moneyline HOME | -150; book absent; 17:10:17 ET | MIL won 3–2 | WIN (direction only) | Board B; probability absent; actionable=false | Final score | Direction hit; no independent probability, price-source book, or stake. Three-way market settlement terms not preserved. |
| TB–MIL three-way total UNDER 6.5 | -115; book absent; 17:10:17 ET | 5 total runs | WIN (direction only) | Board B; probability absent; actionable=false | Final score | Under direction settled below 6.5. Exact book/rules missing; no wager evidence. |
| Jacob Misiorowski strikeouts UNDER 6.5 | +125, Novig; 17:10:21 ET | 3 strikeouts | WIN (direction and line) | A_FLAGGED; blended p=47.86%; independent p=63.39%; actionable=false | Official pitching line | Threshold beat by 3.5. It was shadow/audit-only and flagged `missing_predictive_signal`; no stake. Probability-source disagreement needs investigation before crediting the forecast. |
| Milwaukee team total UNDER 3.5 | +106, Novig; 17:10:24 ET | MIL scored 3 | WIN (direction and line) | A_FLAGGED; `candidates.csv` final blend 50.00% (`outlier_devig`); `team_totals.csv` final blend 49.39% (book-median devig blended with recency); LOW quality; actionable=false | Official score | Won by half a run; the saved artifacts disagree on probability/EV. Also flagged stale/reverse movement/thin liquidity, independent model absent, and team-total fair-total separation absent. |
| TB–MIL full-game total OVER 6.5 | -105, TheScore Bet; 17:10:24 ET | 5 total runs | LOSS (direction and line) | A_FLAGGED; p=51.99%; actionable=false | Official score | Two runs short. Saved line movement was 5.5 open to 6.5 current, with reverse movement flag and public-money divergence; it was correctly stood down. |
| MIL moneyline HOME | -170, Novig; 17:10:24 ET | MIL won 3–2 | WIN (direction and line) | A_FLAGGED; p=63.44%; actionable=false | Final score | Direction hit, but saved edge was only 0.76 percentage points versus raw implied probability, with stale/thin-liquidity/reverse-movement flags; correctly stood down. |

`original_recommendations.csv` contained headers but no recommendation rows. Candidate rows and decision records are therefore the source for the scorecard. Prices above are the saved candidate terms, not proof those prices were obtainable or bets were placed.

## 4. Internal Contradictions and Pregame Factual Errors

- **Starter identity mismatch:** Rasmussen SO over 3.5 remained visible despite Nick Martinez starting for TB. The pack had `pitcher_identity_mismatch`, identity audit fail-closed, and the decision was `STAND_DOWN` at zero units. Retaining the flagged row in the candidate artifact is intentional diagnostic visibility, not a defect by itself. A separate diagnostic-only output would be a deliberate contract change requiring code and test updates.
- **Conflicting Milwaukee moneyline expressions:** one row is a bookless three-way HOME -150 quote and another is a Novig standard moneyline HOME -170. They are not interchangeable. No saved linkage explains the different settlement semantics or timestamp difference.
- **Totals duplication/direction conflict:** under 6.5 three-way at -115 and over 6.5 at -105 refer to different quote sources/market definitions, and the over row is flagged for movement against. Treating these as two independent forecasts would double-count one game thesis.
- **Model versus price ambiguity:** Misiorowski UNDER has `outlier_devig` p=45.91%, an independent-model p=63.39%, and final blend p=47.86%. Its observed win does not resolve the disagreement. Team-total UNDER has no independent model and explicit source/fair-total quality failures; additionally, `candidates.csv` records final_blended_prob=50.00% and edge_pct=3.00% while `team_totals.csv` records final_blended_prob=49.39% and edge_pct=1.74%. These are different probability/EV artifacts, not a single reconciled estimate.
- **No explicit projected final score or written pregame rationale** was present in the frozen source. The game result cannot be used to invent or backfill either.
- **Date representation:** candidate event start is 2026-10-24 00:00 UTC, corresponding to the Oct. 23 Milwaukee local game date; this is not a game-date error.

## 5. What the Pipeline Predicted Correctly and Why

The three unambiguous standard two-way market threshold directions that settled were Misiorowski UNDER 6.5 (3 K), MIL team total UNDER 3.5 (3 runs), and MIL moneyline (win); each direction was correct. The bookless three-way MIL home and under-total rows also matched the result direction. However, process evidence is weak: these rows were all non-actionable, multiple had no usable model probability or quality flags, and all outcomes arose from a single correlated game. The correct decisions were the `STAND_DOWN` gates, not an established edge from the hits.

The team under and full-game over are directly dependent scoring exposures in the same game; the Milwaukee side is also correlated with its team scoring. Do not treat the wins as independent observations.

## 6. What the Pipeline Predicted Incorrectly and Why

The TheScore Bet OVER 6.5 lost at five runs. This is a forecast miss; evidence cannot identify whether it was model specification, run-environment estimation, or variance because no reproducible projected score/distribution and full input rationale were preserved. The line rose from 5.5 to 6.5 and the candidate carried a reverse-movement flag, so market/price discipline was the observable preventable risk. The stand-down prevented a recorded portfolio loss.

Rasmussen OVER 3.5 is not a loss; it is ungradable because Rasmussen did not pitch. Its incorrect pitcher identity is a preventable data/role defect. The identity gate surfaced it and the decision was zero units. Do not score it as an under or substitute Martinez's statistics.

## 7. Most Important MLB Performance Drivers

- **Starting pitching/workload:** both actual starters lasted five innings. Misiorowski threw 78 pitches and struck out three; Martinez threw 73 and struck out five. Misiorowski UNDER 6.5 won comfortably; the saved distribution mean was 5.25 K with variance 7.54, median 5, but this sample does not validate that projection model.
- **Identity and role:** the expected TB pitcher was wrong in one candidate. In postseason rotation context, starter identity can change independently of a regular-season rotation expectation. The exact reason this row got emitted is not established by this one audit.
- **Scoring:** MIL scored twice in the first and once in the seventh; TB scored in the fourth and ninth. Five total runs settled the game-total UNDER 6.5 as a win and the game-total OVER 6.5 as a loss; MIL's separate team-total UNDER 3.5 also won. No extra innings.
- **Bullpen:** AP reports MIL relievers Uribe and Ashby combined for five strikeouts across three scoreless innings before Megill. This is descriptive, not enough to infer a reusable bullpen edge.
- No verified weather, lineup-order audit, or pregame bullpen availability record was found in the frozen artifacts. No conclusion is drawn about those factors.

## 8. Betting Market, Props, Price and Expected-Value Audit

| Market | Saved estimate and price | Arithmetic check | Assessment |
|---|---|---|---|
| Rasmussen O3.5 | proxy p=.5487; +119 | Formula EV would be about +0.20/unit if that probability and quote were valid | Not a valid EV claim: wrong pitcher identity, proxy only, flagged, no stake. |
| MIL 3-way HOME | p absent; -150, book absent | Cannot calculate EV | Direction hit; price/settlement definition not sufficiently preserved. |
| Under 6.5 (3-way) | p absent; -115, book absent | Cannot calculate EV | Direction hit; no trusted probability or source. |
| Misiorowski U6.5 | final blend p=.4786; +125 | Break-even 44.44%; EV ≈ +0.077/unit under saved blend | Hypothetical only; A_FLAGGED, missing predictive signal, blended probability conflict, 0 units. |
| MIL TT U3.5 | `candidates.csv`: final blend .5000, `edge_pct` .0300; `team_totals.csv`: final blend .4939, `edge_pct` .0174; +106 | Break-even 48.54%; using `team_totals.csv`, probability gap ≈0.85 percentage points and EV ≈ +0.017/unit | Pack artifacts conflict: `candidates.csv` records Outlier devig at 50.00% and +0.030 EV/unit, while `team_totals.csv` records a 49.39% blended probability and +0.0174 EV/unit. Neither value should be silently substituted for the other. Both are hypothetical; low-quality/source-integrity flags and no independent model; 0 units. |
| Total O6.5 | p=.5199; -105 | Break-even 51.22%; EV ≈ +0.015/unit on saved p | Hypothetical only; small apparent edge, line movement against, flagged, 0 units. Lost. |
| MIL ML | p=.6344; -170 | Break-even 62.96%; probability gap ≈0.48 percentage points; `edge_pct` / EV ≈ +0.0076/unit | Hypothetical only; tiny apparent probability gap, stale/thin-liquidity/reverse-movement flags, 0 units. Won. |

`edge_pct` is expected value per unit under the pipeline sizing formula; it is not the probability gap against break-even. For example, +106 at p=.4939 gives about +1.74% EV per unit, while the probability gap over 48.54% break-even is about 0.85 percentage points. EV calculations use the preserved probability and price as if both were valid and available at the same time; they do not establish realized EV. No reliable closing quote series or comparable closing market was available, so CLV is ungraded. There is no stake/transaction record; no ROI, units won, or portfolio performance is reported. The two totals and two MIL moneyline rows must not be treated as independent bets.

## 9. New and Updated Sport-Specific Betting Rules

| Rule ID | Sport | Signal or failure | Updated rule | Application | Evidence strength | Validation method |
|---|---|---|---|---|---|---|
| MLB-SP-IDENTITY-01 | MLB | Candidate pitcher differs from confirmed starter | Current contract: retain mismatch rows for diagnostics with a disqualifying flag; enforce non-actionable / `STAND_DOWN` / zero units downstream. Any change to hide them from candidate output requires an explicit code and test change. | Rasmussen row remained visible and was correctly blocked; visibility alone is not a recommendation. | PROVISIONAL (one case; existing gate and test support current behavior) | Across at least 200 starts, verify all mismatched/unconfirmed rows remain diagnosable and none becomes actionable or receives positive units. |
| MLB-MKT-QUOTE-01 | MLB | Duplicate/contradictory quotes and missing book | Preserve canonical market settlement type, operator/book, quote timestamp, and source ID; dedupe equivalent outcomes and isolate three-way from two-way markets. | MIL -150/-170 ML and the three-way total rows stay separate until reconciled. | PROVISIONAL | Reconcile market IDs and terms on 200 game snapshots; track duplicate candidates and price mismatch rate. |
| MLB-MKT-MOVE-01 | MLB | Candidate flags reverse movement/stale or thin liquidity | Keep these flags as hard review/stand-down inputs; do not let a small model edge override them without current verified quotes and explicit policy. | Applied to O6.5, MIL TT U3.5, MIL ML. | PROVISIONAL (one game; consistent with existing gate) | Prospective shadow log with timestamped open/current/close prices, liquidity, and CLV; compare flagged vs unflagged by market. |
| MLB-PROB-01 | MLB | Blended and independent probabilities disagree | Show component probabilities and source; block EV claims/actionability when required independent predictive input is missing or disagreement exceeds the existing policy threshold. | Misiorowski U6.5 and team total. | PROVISIONAL | Freeze pregame component probabilities and evaluate Brier/log loss by market with chronological holdout, minimum 200 graded forecasts per segment. |

No rule is promoted to REPEATED or VALIDATED. No WNBA rule change.

## 10. Updated MLB Master Playbook — KEEP / CHANGE / REMOVE

No pre-existing MLB master playbook document was found in the repository, so this audit creates a **provisional starting playbook** at [`docs/playbooks/MLB_MASTER_PLAYBOOK.md`](../../docs/playbooks/MLB_MASTER_PLAYBOOK.md). That file is the authoritative KEEP / CHANGE / REMOVE list and change history; this section records its status: one game, seven candidate rows, zero actionable plays, validation pending. It does not claim prior rules were reinforced or contradicted when no versioned rule source exists.

### KEEP

See the authoritative playbook's KEEP section. This audit is consistent with retaining fail-closed identity checks, actionability gates, and immutable quote provenance.

### CHANGE

See the authoritative playbook's CHANGE section. This audit supports retaining current visible-and-stand-down handling unless a separately reviewed code and test change establishes another contract.

### REMOVE

See the authoritative playbook's REMOVE section; no rule is promoted on this single-game result.

### Change history

- 2026-10-24: Added provisional identity, market-quote, movement, and probability provenance rules from the Oct. 23 TB–MIL audit. Sample: one game, seven candidate rows, zero actionable plays. Validation pending.

## 11. Five Highest-Priority Pipeline Improvements

| Priority | Pipeline improvement | Problem addressed | Implementation | Validation metric |
|---|---|---|---|---|
| 1 | Preserve and clearly surface the identity stand-down contract | A mismatched starter row is diagnostic output and could be mistaken for a recommendation | Keep the flagged row visible under the current contract; ensure every output surface makes it non-actionable / `STAND_DOWN` / zero units. A diagnostic-only export requires a reviewed code and test change. | Across at least 200 starts, target zero mismatched rows promoted to actionable or positive units; confirm diagnostic visibility. |
| 2 | Canonicalize market identity and settlement | Separate -150 3-way and -170 moneyline rows; missing books | Key by event, market type, period, settlement type, selection, line, operator, and quote timestamp. | Duplicate/equivalent market collision rate and quote audit failure rate. |
| 3 | Enforce quote freshness/liquidity policy in published recommendation surfaces | Stale/thin/reverse-movement flags on three candidates | Show current quote age and gate eligibility against existing policy; do not suppress diagnostics. | Stand-down leakage rate; timestamped CLV for eligible and flagged shadow rows. |
| 4 | Surface probability provenance and disagreement | Devig, independent, and blended probabilities differ materially | Publish probability components, model version, source integrity, and deterministic discrepancy gate. | Brier/log loss and calibration by market/source on chronological out-of-sample set. |
| 5 | Cluster correlated same-game exposures and label outcome denominators | Five winning directions are not five independent observations | Group score/side/total/team-total candidates by game thesis; separate candidate-direction, actionable, and bet ledger counts. | Correct denominator audit; correlation-aware portfolio drawdown and calibration reports. |

## 12. Final Lesson and Rules for the Next Game

The most defensible conclusion is **NO BET**, which the pipeline in fact recorded: 0 actionable candidates, 0 units. Milwaukee's 3–2 win made several candidate directions look prescient, but the saved evidence shows stale/thin quotes, absent or disputed probabilities, correlated markets, and one wrong pitcher identity. Preserve the stand-down behavior. Before the next MLB slate, verify announced starters and market participant rules; require fresh quote/book/settlement metadata; expose probability provenance; reconcile model components; cluster correlated outcomes; and retain a no-bet decision when those conditions fail. Revisit these provisional rules only after prospective, timestamped, market-specific out-of-sample validation.
