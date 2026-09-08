# Handoff — Claude — 2026-09-08

## Last commit

`0cf21e7` — `feat(ncaa): add standalone NCAA football analytics pipeline`
Branch: `claude/ncaa-football-analytics-pipeline-nsiiy4` (pushed to origin).

## PR status — ACTION REQUIRED

**The pull request could not be opened from this session.** `gh` is not
installed in the Claude Code web container, the GitHub MCP server disconnected
mid-session, and a direct REST call returns:

```
403 GitHub access is not enabled for this session.
An org admin must connect the Claude GitHub App for this organization.
```

The branch is pushed. Open the PR manually:
https://github.com/DaSilvaDub/outlier/pull/new/claude/ncaa-football-analytics-pipeline-nsiiy4

## Files touched

- `outlier_scrapers/ncaa/` — new package, 16 modules (~3,900 lines):
  `stats`, `odds`, `config`, `features`, `mismatch`, `safety`, `value`,
  `tiers`, `parlay`, `totals`, `calibrate`, `backtest`, `report`, `pipeline`,
  `sources`, `__init__`
- `tests/test_ncaa_pipeline.py` — 59 tests
- `docs/plans/2026-09-08-ncaa-football-analytics-pipeline.md` — design spec
- `config/ncaa_pipeline.json` — thresholds (new)
- `.gitignore` — added `!config/ncaa_pipeline.json` (the `config/*` rule would
  otherwise have kept it out of a fresh clone)
- `pyproject.toml` — registered `outlier_scrapers.ncaa` in `[tool.setuptools]`

## Verification

ruff, mypy, and pyright all clean on the new package. 59/59 tests pass.

**pytest is not installable in this container** (PyPI returns 403 through the
proxy), so the suite was executed with a stdlib runner that imports the module
and calls each `test_*` function. The tests are ordinary pytest-compatible
functions using plain asserts — **please run `pytest tests/test_ncaa_pipeline.py`
on a normal machine to confirm.** No existing test was run for the same reason;
the change is purely additive, but that is unverified.

## STEP 0 was not run

`report-sync.ps1` requires PowerShell. This is a Linux container and neither
`pwsh` nor `powershell` is installed, so the mandatory bootstrap/sync report
could not be produced. State was read with plain git instead. A Windows session
should run the canonical report-sync before building on this work.

## Scope guarantees

- Shadow-only. Writes no `model_prob`, `edge_pct`, or sizing anywhere; not
  wired into `daily_job`, `refresh`, `pack`, or the desk runners.
- No reasoning-model or provider call; nothing in the package imports
  `openai`, `anthropic`, `google-genai`, or any network module.
- MLB/WNBA prop whitelists, side restrictions, and the verdict/PackIndex
  contract are untouched.

## Next steps

1. Open the PR (link above).
2. Run `pytest` locally to confirm the suite and check for regressions.
3. Phase 2 of the plan: write adapters against the `sources.py` protocols.
   Live web access was disabled here, so no NCAA data source was integrated
   and `api.collegefootballdata.com` / ESPN are both blocked by the proxy.
4. **Every numeric constant is a prior, not a fitted value.** `margin_sigma`,
   `market_spread_weight`, `opponent_adjustment_damping`, `total_sigma`, all
   penalty magnitudes, all tier thresholds, and the correlation tag weights
   need backtesting before any output is trusted. `backtest.py` is the harness.
5. Keep `parlay.max_probability_decay_per_leg` aligned with
   `tiers.core_min_win_prob` (`decay ~= 1 - core_min_win_prob`). Mismatched
   values silently make CORE-admitted legs unplaceable; there is a test for it.
