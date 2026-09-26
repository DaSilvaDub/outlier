# NFL shadow settle

## Summary

- predictions: **328**
- settled: **237** (W 171 / L 65 / P 1)
- skipped: **91**
- hit rate (W/(W+L)): **0.7245762711864406**
- Brier (market_implied): **0.20942634476398303** (n=236)
- logloss (market_implied): **0.6109200586924246** (n=236)
- Brier (model_p): **0.23783898305084747** (n=236)
- logloss (model_p): **3.787168484897851** (n=236)
- CLV: **ok** — n=237 mean_implied_pts=0.0 sources=['pregame_snapshot_best_odds']

## Blockers

- `live_espn`: Live ESPN NFL scoreboard is optional and may return 403 from sandboxed egress; prefer --provider nflverse or --boxscores fixtures.
- `event_id_join`: Outlier event_id does not match ESPN/nflverse provider ids; join is team-pair + Eastern slate date + player name.

## Skip reasons

- `player_not_in_boxscore`: 57
- `unsupported_or_missing_stat`: 34

## By tier

- `TIER_1_ANCHOR`: settled=237 W/L/P=171/65/1 hit_rate=0.7245762711864406 skipped=91

