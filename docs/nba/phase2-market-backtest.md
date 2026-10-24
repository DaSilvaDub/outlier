# NBA Phase 2c: the team model against historical closing lines

Stacked on PR #293. Aggregate numbers only; no archive content is stored in git.

## Source, permission and limits

Sportsbook Reviews Online (SBRO) season pages (`/scoresoddsarchives/nba-odds-YYYY-YY`): one HTML table per season
with the final score, open/close total, open/close spread and both moneylines for each game.

**Permission, stated plainly.** robots.txt allows crawling, but the site's Terms say its content may not be used or
reproduced without express written consent. The repository owner decided on 2026-10-21 to use it for private
research. Consent from the site was **not** requested or obtained. Those are different things, and whether this use is
acceptable is the owner's decision; nothing in this repository settles it. Mitigations that exist: one request per
season page, raw pages cached outside git (`%LOCALAPPDATA%/outlier_nba/sbro_cache`), only aggregates published, and a
provider-independent evaluation (below) so the source can be dropped without redoing anything else.

**Provider independence.** `track/market_compare.py` depends only on `outlier_nba.market.closing.MarketLine`.
`ingest/sbro.py` is one way to fill it; `closing.lines_from_csv` (and `backtest --closing-csv FILE`) is another, with
the columns documented in `closing.py`. Replacing the source changes one loader.

Limits of the data:

* The archive ends in 2022-23 (that page holds 662 games), so the comparison covers 2018-19 to 2022-23, **5,604
  games** (of 6,267 scored games in those seasons). 2023-24 onward has no closing lines here.
* The page does not say which book or what time the "Close" is. It is *a* closing line, not a verified-timestamp one,
  and a historical close is not evidence that the same price was available when a bet would have been placed.
* A handful of rows are skipped and counted per season in the manifest (missing spread or total, unknown team).

## How the join stays honest

* Every SBRO game is joined to an ESPN game on Eastern date (+/- 1 day), teams **and both final scores**; 0 score
  mismatches. Ordinary games must also agree on the home team.
* **Neutral-site games** (two `N` rows, no home team stated; includes all 172 bubble games after the 2019-20 restart)
  have their own path: oriented from ESPN's home/away, verified by both scores, and reported separately.
* Dates carry no year on the page; the calendar year rolls over exactly once (December to January), which dates the
  August to October 2020 bubble correctly.
* A season page is cached only if it parses to at least 300 games (a placeholder or changed markup is rejected before
  anything is stored), and a run asked to compare with the market **fails (exit 6)** if any requested season is not
  cached or matched fewer than 400 games, unless `--allow-partial` is given and at least one game matched. Coverage
  problems are written into the report.
* `data/NBA/backtest/sbro_manifest.json` records the parser version and, per season, the SHA-256 of the cached page,
  games parsed / neutral / matched / skipped (with reasons). It contains no page content, so the figures below can be
  rebuilt from the same cached pages and checked against the hashes. The raw pages themselves are not in the repository.

## Result: the market is better, by a wide margin

Home-win log loss on the 5,604 games (lower is better):

| Predictor | Log loss | Brier |
|---|---|---|
| Market, multiplicative de-vig | 0.6118 | 0.2117 |
| Market, Shin de-vig | 0.6128 | 0.2120 |
| Pool (sim + Elo) | 0.6336 | 0.2216 |
| Simulation (the live predictor) | 0.6338 | 0.2217 |
| Elo only | 0.6346 | 0.2220 |

Gain of the simulation over the (Shin) market: -0.0210 log loss, 95% interval [-0.0273, -0.0147] resampling dates and
[-0.0278, -0.0145] resampling games: the model is worse, and the interval excludes zero. By season (market /
simulation): 2018-19 .5963/.6180, 2019-20 .6142/.6280, 2020-21 .6203/.6476, 2021-22 .6076/.6361, 2022-23 .6400/.6458
(n = 1312 / 1142 / 1169 / 1319 / 662). The market wins every season.

By venue: ordinary games (n = 5,431) market .6115 vs simulation .6320; neutral-site games (n = 173, mostly the
bubble) market .6518 vs simulation .6882. The neutral sample is small, so read it as direction only.

Point forecasts, same games: market spread MAE 10.26 vs simulated margin 10.67; market total MAE 14.39 vs simulated
total 14.78. After the 2026-10-22 level correction the simulated total is centred on these games (bias -0.09; it was
-1.8 before, the market is -0.51), but the market's total is still more accurate than the model's, and the
win-probability comparison below is unchanged.

Blending the simulation into the market with a weight fitted on earlier seasons (0.15 to 0.20) gives a log-loss gain
of +0.0006 with a date-clustered interval of [-0.0007, +0.0019] on 4,292 games: indistinguishable from zero.

## What this means

* The earlier finding (pool beats Elo by 0.0017) is real but irrelevant to betting: both are about 0.02 log loss behind
  the closing market. **There is no evidence of an edge on the moneyline**, and the point forecasts are worse than the
  closing spread and total.
* The market-anchored design with model weight 0 is the right default; the promotion gate would not pass this model.
* The sensible uses of the model are consistency checks across lines and shadow evidence, not stakes.
* Caveats that cut both ways: the close of unknown origin may be a sharper book than what Outlier offers; the model
  uses no injury, lineup or rest information, which the market prices in. The gap per season runs from .006 (2022-23,
  half a season) to .029 (2021-22); the cause of the differences was not investigated.

## Reproduce

```
python -m outlier_nba.cli sbro-fetch --seasons 2018-19:2022-23
python -m outlier_nba.cli backtest --no-tune --sbro --seasons 2018-19:2022-23 --out data/NBA/backtest/phase2_market.json
# or, from any provider's CSV:
python -m outlier_nba.cli backtest --no-tune --closing-csv closes.csv --seasons 2018-19:2022-23
```
