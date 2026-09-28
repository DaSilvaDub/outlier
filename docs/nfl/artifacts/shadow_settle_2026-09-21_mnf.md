# NFL shadow settle

## Summary

- predictions: **34**
- settled: **29** (W 21 / L 8 / P 0)
- skipped: **5**
- hit rate (W/(W+L)): **0.7241379310344828**
- Brier (market_implied): **0.13612003067586206** (n=29)
- logloss (market_implied): **0.4141579797759036** (n=29)
- Brier (model_p): **0.21358207459541378** (n=29)
- logloss (model_p): **0.6382236416546502** (n=29)
- CLV: **ok** — n=29 mean_implied_pts=0.0 sources=['pregame_snapshot_best_odds']

## Blockers

- `live_espn`: Live ESPN NFL scoreboard is optional and may return 403 from sandboxed egress; prefer --provider nflverse or --boxscores fixtures.
- `event_id_join`: Outlier event_id does not match ESPN/nflverse provider ids; join is team-pair + Eastern slate date + player name.

## Skip reasons

- `player_not_in_boxscore`: 4
- `unsupported_or_missing_stat`: 1

## By tier

- `TIER_1_ANCHOR`: settled=29 W/L/P=21/8/0 hit_rate=0.7241379310344828 skipped=5


_Midweek MNF pack; not a Sunday OOS slate._
