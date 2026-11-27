# `slate`: today's NBA report from the team model

```powershell
python -m outlier_nba.cli slate [--date YYYY-MM-DD] [--draws 50000] [--no-refresh]
                                [--store PATH] [--cache-dir PATH] [--out-dir PATH]
```

Simulates every game on the slate (default 50,000 draws each), prices the quoted DraftKings
lines, runs twelve gates, sizes survivors by quarter-Kelly under the caps, and writes a
nine-section report. No Outlier call, no reasoning model, no desk.

## What it is, and is not

- **SHADOW / UNPROMOTED.** The team model lost to the closing market out of sample
  ([phase2-market-backtest.md](phase2-market-backtest.md)). Every EV is model-only and is shown
  beside a *market-anchored* EV (model at 17.5% weight, the weight fitted on earlier seasons).
  "Pass" is a normal verdict.
- **Single book, untimestamped.** Lines come from ESPN's public scoreboard (DraftKings). They are
  not a verified consensus; re-check prices before betting. Team totals, player props and alt
  ladders are not available from this source, and player props are not modeled at all.
- **Possession counts, not possessions.** The sim draws possessions and per-100 efficiency with
  noise fitted to real margins and totals; it does not play out each possession.
- **Injuries are a filter, not an input.** A blocking listing (Out/Doubtful/Questionable/Day-To-Day)
  dated within 48 h on either team rejects the game; an empty injury feed rejects everything.

## Gates

F1 pre-tip (10 min buffer) · F2 results fresh (≤7 days, enforced by `compute_team_state`) ·
F3 teams resolved and neutral-site flag known · F4 −250 ≤ price < +150 · F5 probabilities available ·
F6 EV ≥ 2% and quarter-Kelly ≥ 0.5u · F7 EV ≤ 10% (larger is treated as model error) ·
F8 |model − devigged market| ≤ 7 points · F9 EV still positive after a 0.75-point adverse shift ·
F10 no fresh injury news and a usable feed · F11 overround 1.00–1.10 ·
F12 caps 1.0u/bet, 1.5u/game, 3.0u/day, one exposure per side per game, never both sides.

These are this command's gates, not a house standard; thresholds live in `SlateConfig`.

## Files and locations

Outputs are written per run (`NBA_Master_Report_<day>_<HHMM>ET.md`, `nba_master_<day>_<HHMM>ET.json`,
and the raw `espn_*` payloads) so a later run never replaces the only record of what was quoted.
Defaults live under `~/outlier_nba_data/{reports,store,cache}` (override `OUTLIER_NBA_DATA_DIR`);
locations inside the repo or temp are refused. `backfill` and `ratings` default to different
locations, so pass the same `--store` and `--cache-dir` to all three if they should share one.

Exit codes: 0 ok (including "no games today"), 2 refused location, 3 team state unavailable
(stale or missing store: run `backfill`), 4 ESPN feed blocked.
