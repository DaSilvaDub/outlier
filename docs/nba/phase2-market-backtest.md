# NBA Phase 2c: the team model against historical closing lines

Stacked on PR #293. Private research only; aggregate numbers only (no archive content is stored in git).

## Source and its limits

Sportsbook Reviews Online season pages (`/scoresoddsarchives/nba-odds-YYYY-YY`): one HTML table per season with
the final score, open/close total, open/close spread and both moneylines for each game. robots.txt allows crawling,
but the site's Terms say its content may not be used or reproduced without express written consent. The repository
owner chose to use it for **private research** on 2026-10-21 (not asked for consent from the site). So: one request
per season page, raw pages cached outside git (`%LOCALAPPDATA%/outlier_nba/sbro_cache`), only aggregates published.

* The archive ends in 2022-23, so the comparison covers 2018-19 to 2022-23, **5,431 games** (of 6,267 scored games
  in those seasons). Missing: 2019-20 bubble games (neutral-site rows the table marks `N`, 343 rows), the second half
  of 2022-23 (the page holds 662 games), and a handful of unparseable rows. 2023-24 onward has no closing lines here.
* The page does not say which book or what time the "Close" is. It is *a* closing line, not a verified-timestamp one.
* Every SBRO game was joined to an ESPN game on date, teams **and both final scores** (0 score mismatches), so no
  pairing is a guess.
* The win probability for the market is the de-vigged moneyline (Shin; multiplicative shown as a check).

## Result: the market is better, by a wide margin

Home-win log loss on the 5,431 games (lower is better):

| Predictor | Log loss | Brier |
|---|---|---|
| Market, multiplicative de-vig | 0.6107 | 0.2113 |
| Market, Shin de-vig | 0.6115 | 0.2115 |
| Pool (sim + Elo) | 0.6319 | 0.2208 |
| Simulation (the live predictor) | 0.6320 | 0.2209 |
| Ridge only | 0.6320 | 0.2209 |
| Elo only | 0.6329 | 0.2212 |

Gain over the market (market loss minus model loss; negative means the model is worse), 95% interval:
simulation -0.0205 [-0.0265, -0.0143] by date clusters and [-0.0262, -0.0136] by game; pool -0.0204
[-0.0262, -0.0144]; Elo -0.0214. The market wins in **every** season: log loss market vs simulation
2018-19 .5965/.6181, 2019-20 .6071/.6171, 2020-21 .6203/.6476, 2021-22 .6076/.6361, 2022-23 .6400/.6458
(n = 1311 / 970 / 1169 / 1319 / 662).

Point forecasts, same games: market spread MAE 10.28 vs simulated margin 10.67; market total MAE 14.33 vs simulated
total 14.79. The simulated total runs 1.8 points low on these games (market 0.5 low).

Blending the simulation into the market with a weight fitted on earlier seasons (0.15 to 0.20) gives a log-loss gain
of +0.0005 with an interval of [-0.0009, +0.0018] by date: indistinguishable from zero, on 4,120 games.

## What this means

* The earlier finding (pool beats Elo by 0.0017) is real but irrelevant to betting: both are about 0.02 log loss behind
  the closing market. **There is no evidence of an edge on the moneyline**, and the point forecasts are worse than the
  closing spread and total.
* The market-anchored design with model weight 0 is the right default; the promotion gate would not pass this model.
* The sensible uses of the model are consistency checks across lines, and shadow evidence, not stakes.
* Caveats that cut both ways: the close of unknown origin may be a sharper book than what Outlier offers; the model
  uses no injury, lineup or rest information, which the market prices in. The gap per season runs from .022 (2018-19) to
  .029 (2021-22); 2020-21, when home court collapsed, is .027. The cause of the differences was not investigated.

## Reproduce

```
python -m outlier_nba.cli sbro-fetch --seasons 2018-19:2022-23
python -m outlier_nba.cli backtest --no-tune --sbro --out data/NBA/backtest/phase2_market.json
```
