# Session Handoff: Daily Debug Review (2026-11-09, agent `claude`)

1. **Work**: Automated daily debug review. Branch `claude/inspiring-fermat-38t78c`
   (started from `8bb0363`, which is `origin/master`). Reviewed everything merged
   since the 2026-11-06 run (`b7ed4f2`): PRs #302/#327/#328/#329/#331/#332. The
   `scripts/organize_today_run2.py` (1650 lines) and
   `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
   (1532 lines) diffs are **pure CRLF->LF normalisation** -- `git diff -w` is
   empty for both, matching the new `.gitattributes` `eol=lf` entries. The real
   new code is three scripts plus three PowerShell wrappers:
   `export_nba_extra_pack.py` (269 lines, **no tests**),
   `filter_nba_ruled_out.py`, `organize_desktop_today.py`,
   `refresh_nba_pack.ps1`, `run_nba_audit.ps1`, `run_nba_pack_refresh.ps1`.
   One reproducible defect fixed:
   - **`export_nba_extra_pack.py` published another slate's pack under today's
     date, silently.** `--allow-latest-fallback` was
     `action="store_true", default=True`, so it could only ever be True: the
     fail-closed branch `elif not allow_latest_fallback: use_rows = []` was
     unreachable from the CLI and `--no-allow-latest-fallback` did not exist.
     `refresh_nba_pack.ps1` calls the exporter with `--date $Date`, so whenever
     `prop_edges_latest.csv` still held the previous slate (the case
     NBA-PROP-010's auto-refresh covers, and which it only *warns* about on
     failure) every row of that stale file became the pack for `$Date` with no
     warning. `--fail-on-empty` cannot catch it -- the pack is not empty, it is
     a different slate's pack wearing today's name -- and the wrapper then
     counts rows and reports success. Reproduced: source holding only
     2026-11-08 rows + `--date 2026-11-09` -> a 2-row pack of 2026-11-08 edges.
     Flag switched to `argparse.BooleanOptionalAction` (default still `True`, so
     no existing caller changes behaviour) and the fallback now prints a
     `[PIPELINE WARNING]` on stderr naming the requested date and the dates
     actually published.
2. **Last Commit SHA**: `27cdb58` (plus this handoff commit).
3. **Files Touched**: `scripts/export_nba_extra_pack.py`,
   `tests/test_export_nba_extra_pack.py` (new), `.agent-log/HANDOFF.md`.
4. **PR Link**: see the PR opened for `claude/inspiring-fermat-38t78c`.
5. **Verification**: **The sandbox can now run the whole suite** -- see item 6.
   Full `pytest`: **3348 passed / 4 skipped** before, **3353 passed / 4 skipped**
   after (+5 new tests), zero failures and zero collection errors either way.
   The two new tests covering the CLI flag and the stale-slate warning were run
   against the *unpatched* script and fail there, so they are not vacuous.
   `ruff check scripts/export_nba_extra_pack.py`: no new findings (the 5
   remaining BLE001/PLW1510 are on pre-existing lines). The `RUF100` on the new
   test file's `# noqa: E402` matches `tests/test_filter_nba_ruled_out.py:15`
   and `tests/test_organize_desktop_today.py:16`, i.e. repo convention.
   `mypy scripts/export_nba_extra_pack.py`: clean. `mypy outlier_scrapers`: 190
   errors in 11 files, all in numpy-typed `outlier_nba` modules pulled in by
   follow-imports, identical to the 2026-11-06 baseline. No reasoning / desk
   models invoked (provider SDKs were installed only so their *imports* resolve
   for collection; no key, no network call).
6. **Sandbox can now run the full suite (changed since every prior run).**
   The blocker in 2026-11-03/05/06 was PyPI egress. A `/usr/local/bin/python3.13`
   interpreter is now present with `numpy`, `sqlalchemy`, `structlog` and
   `dateutil` pre-installed, and `pip install pytest openai google-genai
   anthropic` succeeded. Use `/usr/local/bin/python3.13 -m pytest` (not the
   3.11 default interpreter, which still has nothing). Runtime ~4.5 min.
   The structlog shim and before/after-comparison workaround the previous runs
   needed is obsolete. `docs/CLOUD-SANDBOX-LIMITATIONS.md` still documents the
   old constraints and should be updated.
7. **Next Steps / open items**:
   - **NEW, needs a human (pipeline-blocking, fails closed):**
     `filter_nba_ruled_out.py` defaults its registry to
     `<repo>/resources/nba_roster_registry.json`. That file **and the
     `resources/` directory do not exist in the repo**, nothing in the repo
     writes them, and they are not gitignored. `refresh_nba_pack.ps1:208-224`
     calls the filter **without `--registry`**, so from a clean checkout the
     filter exits 2 (`roster registry not found`, reproduced here) and
     `Invoke-CheckedPython` aborts the whole NBA pack refresh after the export
     step. The only in-repo producer of injury data is
     `refresh_lineups_injuries.ps1`, which lives in the *external*
     `nba-props-pipeline` repo and is not visible from here. Not fixed: the
     safe-looking fixes are both wrong without that repo -- making the filter
     fall open re-introduces the unfiltered-pack bug its own docstring records,
     and guessing a path is speculation. Recommended: have
     `refresh_nba_pack.ps1` pass an explicit `--registry` pointing at wherever
     `refresh_lineups_injuries.ps1` actually writes the roster, or commit a
     generator for `resources/nba_roster_registry.json`.
   - **NEW, low:** same dead-flag class as the fix above --
     `outlier_scrapers/feedback.py:427` declares `--as-of-strict` as
     `action="store_true", default=True`, so the non-strict branch at
     `feedback_reporting.py:1035` is unreachable from the CLI. Left alone
     deliberately: here the unreachable side is the *lenient* one, so there is
     no correctness loss, and `replay-portfolio` is calibration-adjacent.
   - **NEW, low:** `export_nba_extra_pack.py:60` lists `DEFAULT_OUT`
     (`data/NBA/exports/nba_only.csv`) in `CANDIDATE_SOURCES`, so with no
     upstream source the exporter reads its own previous output and republishes
     it. Harmless today (the read completes before the write, and the schema
     round-trips) but it means a stale pack can be re-published as fresh.
   - **NEW, low:** `filter_nba_ruled_out.py:74` and `:76` call `.strip()` on
     `r.get("player", "")`; `csv.DictReader` fills a short row's missing cells
     with `None`, so a truncated CSV row raises `AttributeError` rather than a
     clean exit 2. Also, any non-empty `injuries` list marks a player ruled out
     regardless of `status`, which over-filters day-to-day listings.
   - **Still open from 2026-11-06 (needs a ruling, behaviour change):** a
     matchup-prefixed team total is skipped but not graded --
     `_team_total_event_match` does not recognise `<matchup> <tri> Team Total`.
     Related: `"ATL @ WAS Washington Mystics - Points OVER 87.5"` fails to match
     when the event's teams are tricodes (full club name vs `WSH`).
   - **Still open from 2026-11-03 (needs a human):** three-way rows already
     settled as `PUSH` in `feedback.sqlite3` stay wrong until re-graded. The
     ledger is not in this clone. Do not do this from an agent session
     (calibration lock).
   - **Still open, minor:** `track/model_ledger.record_model_probs` reports
     `len(recs)` as `model_rows` although `INSERT OR IGNORE` may insert fewer.
   - **CLOSED since 2026-11-06:** `.worktrees/test-pr97/` (364 tracked files,
     ~4 MB) has been removed and `.worktrees/` is now in `.gitignore`. The
     duplicate-copy lint noise flagged in the last three runs is gone; the 11
     remaining `B023` findings are the live `portfolio.py:373-378` and
     `tape_nflverse.py:559-566` ones. The `portfolio.py` set was re-checked
     this run and is a **false positive**: `build_clusters` is defined and
     called inside the same loop iteration.
   - **Confirmed benign:** all 15 `PLR0124` "name compared with itself"
     findings are the deliberate `x == x` NaN idiom.
