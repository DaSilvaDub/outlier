# Handoff Summary - 2026-08-25

## 1. Last Commit SHA
- `738a721` on branch `fix/pipeline-audit-remediation-v2`
- Pull Request: https://github.com/DaSilvaDub/outlier/pull/118

## 2. Files Touched
- `outlier_scrapers/api.py`: Broadened network & decode exception handling in `fetch_json` retry loop; wrapped `_safe_http_error_message` in try-except with `strict=False`.
- `outlier_scrapers/games.py`: Multi-key date parsing in `_event_local_date` (`scheduledTime`, `startTime`, `startDate`, `date`, `scheduled`).
- `outlier_scrapers/cards.py`: `_line_values_equal` float tolerance in EV line matching; safe `.get()` dictionary accesses on `main_row`.
- `outlier_scrapers/probable_pitchers.py`: Retries and `strict=False` in `_fetch_json`; doubleheader starter overwrite protection.
- `outlier_scrapers/sizing.py`: `isfinite()` guards and probability partition bounds checking `0 <= p <= 1` in `compute_sizing()` and `_round_to_half()`.
- `outlier_scrapers/alt_bankroll_props.py` & `outlier_scrapers/alt_team_totals.py`: Converted `implied_probability()` percentage to decimal `[0, 1]`.
- `outlier_scrapers/probability_blend.py`: Dynamic `data_quality_tier` inference fallback and default missing `push_prob` to `0.0`.
- `outlier_scrapers/stake_calibration.py`: Clamped `wins` in `wilson_lower_bound` and default missing `push_prob` to `0.0`.
- `outlier_scrapers/projections.py`: Fixed 0-start pitcher BF calculation formula.
- `outlier_scrapers/portfolio.py`: Added `1e-9` epsilon tolerance in `quantize_down`.
- `outlier_scrapers/desk_snapshot.py`: Standardized PID checks to `os.kill(pid, 0)` on Windows and POSIX.
- `scripts/organize_today_run2.py`: Added `safe_copytree()` with retry loops for cloud sync file locks ([WinError 32]).
- `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`: Switched Desk2 prompt writes to `safe_write_text`.
- `outlier_scrapers/daily_job.py`: Streaming `json.dump` in `_atomic_write_manifest`; passed `target_date` when supplied.
- `outlier_scrapers/pack.py`: Streaming JSON writes; defense-in-depth MLB prop whitelist in `build_row()`; matchup team/opponent auto-inference.
- `outlier_scrapers/alt_player_props.py`: Expanded allowed books to 5 books; unicode minus normalization; fixed decimal odds sorting key.
- `outlier_scrapers/results.py`: Allowed 2-letter surnames; matched multi-word team totals; added missing market token aliases (`3PT`, `3PM`, pitcher SO variants).
- `outlier_scrapers/line_movement.py`: Populated `devig_odds` with American odds integer converted from `devig_decimal`.

## 3. Verification
- 1,203 unit tests passed offline (60 live reasoning tests deselected per house rule).
- `pyright outlier_scrapers`: 0 errors, 0 warnings.
- `ruff check outlier_scrapers`: All checks passed.
- No live reasoning model or desk runner invoked.

## 4. Next Steps
- Review and merge PR #118: https://github.com/DaSilvaDub/outlier/pull/118.
