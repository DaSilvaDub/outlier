# Packers at Rams — matchup research, November 25, 2026

**Status: RESEARCH ONLY / NO VALIDATED BETS.** The authenticated NFL pipeline was attempted at 7:51 p.m. ET and stopped at the schedule endpoint after five HTTP 403 attempts. The saved token expired at 10:07 a.m. ET. The standalone nflverse team-tape refresh and player-usage fetch succeeded; the existing matchup-ranking engine was executed separately. This is not a completed live betting pipeline. Paid reasoning: OFF. Monte Carlo: NOT EXECUTED. No verified sportsbook quotes, EV, calibrated probabilities, or stakes.

## 1. Where Green Bay can attack Los Angeles

**The evidence supports a difficult defensive matchup, with opportunity changes driven principally by personnel.** Los Angeles enters allowing 187.7 net passing yards and 96.8 rushing yards per game, compared with Green Bay's 268.1 net passing yards and 67.0 rushing yards on offense. A large Bengals-style passing surplus is not established here. [NFL Week 12 capsules](https://media.nfl.com/content/dam/communications/football-communications/2026/capsules/Week%2012%20Capsules.pdf)

- **Quick distribution and selective shots:** Watson and Golden are the principal established receiving-volume candidates in the refreshed usage data. Shorter throws to Kraft and the backs are a plausible protection response, with selective vertical opportunities for Watson. These are tactical inferences; the available inputs do not establish which corner will shadow a receiver or which coverage shell Los Angeles will use.
- **Backfield replacement volume:** Jacobs is officially inactive. Johnson and Lloyd are the relevant replacement backs to examine. His absence creates opportunity, but the strong Rams run-defense baseline limits any claim of a favorable rushing matchup. The split of carries and pass protection remains uncertain. [Official game-day inactives](https://www.therams.com/news/coleman-shelton-steve-avila-and-larrell-murchison-among-rams-inactives-for-week-12-vs-packers)
- **Avoid importing the sample's scheme claims:** Aggregate yards and pressure rates do not demonstrate leaky perimeter coverage, soft seams, clean pockets, or a particular route-level mismatch. Those claims require current coverage/alignment and protection evidence.

## 2. Which Packers receive the opportunity change?

These are **observed per-game baselines from the refreshed nflverse usage input through Week 11**, not tonight's forecasts. Player averages use games played, so they should not be added together as a team-game projection.

| Player | Observed baseline | Matchup interpretation |
|---|---|---|
| Kaleb Johnson | 26.8 rushing yards; 6.50 carries; 8 games | Main candidate for replacement rushing opportunity with Jacobs out; season average mixes roles. |
| MarShawn Lloyd | 18.0 rushing yards; 6.78 carries; 11.7 receiving yards; 9 games | Also benefits from the vacancy; receiving work provides another path, but the split is unverified. |
| Christian Watson | 91.6 receiving yards; 7.90 targets; 10 games | Established yardage volume; strong opposing pass defense prevents claiming an automatic boost. |
| Matthew Golden | 85.3 receiving yards; 10.10 targets; 10 games | Highest target baseline among these named Packers; efficiency and protection are key uncertainties. |
| Tucker Kraft | 37.0 receiving yards; 5.60 targets; 10 games | Plausible outlet/intermediate option; no verified route-specific advantage or reception forecast. |
| Savion Williams | 27.2 receiving yards; 3.00 targets; 5 games | Secondary usage candidate; limited sample and uncertain target allocation. |
| Josh Jacobs | Inactive | Excluded from tonight's player candidates. |

**Jordan Love:** the refreshed tape contains 284.1 **team gross passing yards** per game. The official 268.1 figure is **team net passing yards**. Neither is automatically Love's individual season average. The ranking engine uses the gross team figure as his baseline, so its QB projection is withheld from the main forecast table.

## 3. Where Los Angeles can attack Green Bay

**A balanced offense with a depleted receiving room is the defensible starting point.** The Rams average 133.0 rushing yards per game, while Green Bay allows 111.0. That comparison supports testing the ground game; it does not independently establish a major rushing edge. Green Bay also allows 210.8 net passing yards per game. [NFL Week 12 capsules](https://media.nfl.com/content/dam/communications/football-communications/2026/capsules/Week%2012%20Capsules.pdf)

- **Williams and Corum:** their refreshed rushing baselines are 61.6 and 54.7 yards, with 13.9 and 10.3 carries per game, respectively. A committee remains the supported baseline; the data do not justify allocating all rushing surplus to one back.
- **Mumpfield, Atwell, Higbee, and Parkinson:** these are relevant receiving options with Nacua, Adams, and Whittington on injured reserve. Mumpfield's observed receiving baseline is 27.0 yards on 3.0 targets; Atwell's is 21.7 on 2.5; Higbee's is 33.8 on 4.78; Parkinson's is 15.9 on 3.12. Their baselines already include some games affected by absences, so a second full vacancy uplift risks double counting. [Rams roster update](https://www.therams.com/news/mcvay-rams-placed-puka-nacua-on-ir-because-they-needed-the-roster-spot-week-10-2026-cardinals)
- **Protection constraint:** Shelton and Avila are inactive, and Parsons is active for his season debut. This creates a plausible protection concern, but Parsons' workload and effectiveness cannot be inferred from his active designation. Zaire Franklin is inactive for Green Bay. [Official inactives](https://www.therams.com/news/coleman-shelton-steve-avila-and-larrell-murchison-among-rams-inactives-for-week-12-vs-packers), [AP availability report](https://www.nhregister.com/sports/article/micah-parsons-is-active-josh-jacobs-inactive-for-22550721.php)

## 4. What the existing ranking stage produced

The separate research execution generated positional QB/RB/WR/TE reports, an overall ranking, and a CSV. The first diagnostic used the tape's broad inactive lists, which contained 57 Rams and 59 Packers entries after supplementation. Those outputs are quarantined. A second diagnostic used named current absences and a limited player list, with a neutral spread input and no sportsbook prices.

The second execution ranked Johnson and Lloyd highest, primarily because of Jacobs' absence. Its raw rushing outputs were 38.2 and 25.6 yards, respectively, versus 26.8 and 18.0 observed baselines. **These are uncalibrated heuristic diagnostics, not approved projections or betting recommendations.**

Independent review found material limitations in `outlier_nfl/matchup_rankings.py`:

1. Every opponent inactive can count as a defensive absence, including offensive players and reserves.
2. QB baselines use team gross passing yards; completions and attempts use fixed yardage divisors.
3. WR/TE receptions are estimated from targets, and target projections receive a fixed 25% increase.
4. RB vacancy boosts can accumulate across several absent backs without a total cap or a current-role check.
5. Probabilities are assumed normal tails; confidence tiers, active-status labels, and verification banners overstate the available validation.
6. Some tactical descriptions are templates rather than measured scheme findings.

These issues explain why this report does not reproduce the sample's precise probability/confidence claims. No product code or publication gates were changed.

## 5. Execution evidence and next step

- Repository sync passed at commit `87608a22be77cefb420c08549df7aaa7dbf508ed`; nonce `791ba5df534d4022`.
- Isolated worktree: `codex/nfl-gb-lar-20261125`; canonical data and calibration stores were not used as output destinations.
- Team tape refreshed at `2026-11-26T00:53:10Z`, covering completed games before November 25; usage cutoff: before Week 12.
- Local evidence folder: `C:\Users\dasil\Documents\NFL Runs\2026-11-25_GB_at_LAR` (`execution.log`, refreshed tape, `usage.json`, diagnostic reports, and summaries).
- Roster-text validation and artifact integrity checks are recorded in `verification.json` in that folder.
- To finish the authenticated live run, refresh the saved Outlier session and rerun the isolated pipeline. A run started after kickoff must retain the pipeline's retrospective/publication restrictions; do not backdate it or publish pregame picks.
