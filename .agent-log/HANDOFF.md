# HANDOFF — 2026-10-06 (Claude, Offline Pytest core CI regression from PR #211)

**Branch**: `fix/pack-gate-ordering-ci-regression` · **PR**: [#215](https://github.com/DaSilvaDub/outlier/pull/215)

## Accomplished
- Root-caused the `core` job failures on master (since 2e11c4d) to ba317d4:
  - `test_over_line_steam_keeps_stale_gate`: **code bug**. The new `low_quality_tier_disqualified` / `negative_learned_edge` gates blanked units early and starved the stale-line gate. I removed the redundant clears; the final `DISQUALIFYING_DQ_FLAGS` sweep still zeroes the stake.
  - `test_wnba_playoff_role_player_...`: **stale fixture**. The warm row (O7.5 PTS, 85% recency) correctly trips `severe_line_discount_trap`. I lowered it to 62% recency.
- Verified: test_pack + test_slate_quality + test_calibration_upgrades → 210 passed. Note: the sandbox needs `--basetemp` in a scratch dir (WinError 5 on the default pytest tmp root).

## Next steps
- Confirm CI `core` is green on PR #215, then merge.

---

# HANDOFF — 2026-10-06 (Antigravity/Gemini, Refresh DAG Concurrency Fix & MLB/WNBA Pipeline Execution)

**Branch**: `master` · **Last commit**: `4a7e06b` · **PR**: [#213](https://github.com/DaSilvaDub/outlier/pull/213)

## Accomplished
1. **Refresh DAG Concurrency Fix & Atomic JSON Writes (PR #213)**:
   - Root-caused `MLB cards: failed (Expecting ':' delimiter: line 14867 column 27 (char 427717))` during concurrent execution of `refresh_plan.py`. `cards` previously depended only on `("props", "line_movement")`, but reads `games_enrichment` and `insights`. While `games` was actively writing a 7MB `mlb_games_enrichment_latest.json`, `cards` was executing concurrently, reading a half-written file.
   - Updated `REFRESH_TASKS` in `outlier_scrapers/refresh_plan.py` so `cards` explicitly depends on `("props", "line_movement", "insights", "games")`.
   - Added `safe_write_json` to `outlier_scrapers/utils.py` with atomic temporary file writes, fsync, and retry loops for Windows file locks (WinError 32/33).
   - Replaced non-atomic file writing in `games.py`, `cards.py`, `props.py`, and `probable_pitchers.py` with `safe_write_json`.
   - Added retry loop to `load_latest` in `cards.py` for read resilience.
   - Verified tests: `tests/test_refresh_plan.py` (6 passed) and `tests/test_cards.py` (62 passed). PR #213 created and merged to `master`.

2. **Executed Daily Pipeline for 2026-10-06 Slate**:
   - Ran `python -m outlier_scrapers.daily_job --date 2026-10-06 --analysis-profile local`.
   - Pipeline completed successfully with exit code 0 (`PARTIAL` local profile).
   - Pitcher identity audit: `status=ok so=3 mismatch=0 unconfirmed=0`.
   - WNBA scheduled games = 0 (cleanly skipped per slate integrity rules).
   - Renamed stray directory `packs/2099-07-07` to `packs/_test_fixture_2099-07-07-clean` per Codebase Quirk invariant rules.
   - Ran `python scripts/organize_today_run2.py` exporting prompts and reports to `C:\Users\dasil\OneDrive\Desktop\today` and `G:\My Drive\today`.

3. **Slate Audit**:
   - `packs/2026-10-06/candidates.csv`: 14 rows total. 0 actionable Board A plays (all prospective EV plays flagged with `september_pitcher_so_under` side restriction, `thin_liquidity`, or `reverse_line_movement`). All decisions in `decisions.csv` are `STAND_DOWN`.

## Files Touched
- `outlier_scrapers/refresh_plan.py`
- `outlier_scrapers/utils.py`
- `outlier_scrapers/games.py`
- `outlier_scrapers/cards.py`
- `outlier_scrapers/props.py`
- `outlier_scrapers/probable_pitchers.py`
- `packs/2026-10-06/`
- `.agent-log/HANDOFF.md`

## Next Steps
- Monitor tomorrow's slate runs.

---

# HANDOFF — 2026-10-06 (Antigravity/Gemini, Post-Game Accuracy Reconciliations & Severe Line Trap Upgrades)

**Branch**: `feat/accuracy-upgrades-and-injury-trap-guard` · **Last commit**: `ba317d4` · **PR**: [#211](https://github.com/DaSilvaDub/outlier/pull/211)

## Implemented & Baked into Pipeline
- **Severe Line Discount Trap Guard (`severe_line_discount_trap`)**:
  - Implemented in `outlier_scrapers/slate_quality.py` and added to `DISQUALIFYING_DQ_FLAGS` in `outlier_scrapers/pack_selection.py`.
  - Disqualifies player props where line is $\le 60\%$ of projection mean or where basketball star scoring lines are collapsed ($line \le 9.5$ with large historical edge), preventing the pipeline from chasing bookmaker minute-restriction / injury traps (such as Jewell Loyd 7.5 PTS / 0 actual).
  - Routes disqualified rows to `actionable = false`, `recommended_units_pre_news = ""`, and `board = "A_FLAGGED"`.
- **Fail-Closed Board A Quality & Learned Edge Gate**:
  - Automatically disqualifies candidates with `data_quality_tier == "LOW"` or `learned_conservative_probability < implied_probability` from receiving Board A units (`negative_learned_edge`, `low_quality_tier_disqualified`).
- **NFL Depth Chart Hierarchy & Script Scaling (`outlier_nfl/alt_floors.py`)**:
  - Added WR depth chart hierarchy scaling: WR2 options receive a -0.04 confidence adjustment; WR3+ options receive -0.08, preventing backup/slot receivers (such as Parker Washington) from masquerading as low-floor anchors above target hogs.
  - Added TE ADOT yardage variance cushion check (cushion < 35% penalized by -0.04).
  - Added road/deficit rushing game script adjustment (-0.04) for running backs facing trailing risk.
- **Unit Tests**:
  - Added tests in `tests/test_slate_quality.py`, `tests/test_calibration_upgrades.py`, and `tests/test_nfl_alt_floors.py`. All 47 tests pass.

## Files Touched
- `outlier_scrapers/slate_quality.py`
- `outlier_scrapers/pack_selection.py`
- `outlier_nfl/alt_floors.py`
- `tests/test_slate_quality.py`
- `tests/test_calibration_upgrades.py`
- `tests/test_nfl_alt_floors.py`
- `.agent-log/HANDOFF.md`

## Next Steps
- Merge PR #211 to `master`.

---

# HANDOFF — 2026-10-05 (claude, Daily Automated Debug Review)

**Branch**: `claude/inspiring-fermat-h49p2m` · **Last commit**: `00aa0b9` · **PR**: https://github.com/DaSilvaDub/outlier/pull/210

## Three defects found and fixed, each reproduced before the fix

1. **`outlier_scrapers/pack_selection.py` — the new WNBA postseason gates were
   dead code.** `wnba_playoff_role_player_over_risk()` (its flag is in
   `DISQUALIFYING_DQ_FLAGS`) and `low_volume_3pt_shooter()`'s new fallbacks,
   both added in `2a2f171`, read `hit_rate_component` / `historical_edge_pct`
   off the row. Both fields were written ~70 lines *below* those gates inside
   the same `_apply_quality_and_signal_flags`, so every gate read the `""`
   placeholder `_build_base_row` seeds from `CANDIDATES_HEADER`. Hoisted the two
   assignments above the gates — pure code motion, both derive from
   `side_view["signal"]` and `row["decimal_price"]` / `row["push_prob"]`, all
   set earlier by `_apply_price_and_sizing`. Row field values verified
   identical pre/post; the only delta is that the gates now see them.
   **Watch the next WNBA postseason pack**: these gates were dormant, so rows
   the 2026-10-04 run let through may now come back `A_FLAGGED`.

2. **`outlier_nfl/weekly.py` — `--reports-dir` was silently half-applied.**
   `run_week()` honoured it for the merged weekly card but never forwarded it
   to the inner `pipeline.run()`, so the per-slate alt-floors report (written
   unconditionally since `2dc5495`) went to `./reports/NFL` relative to the
   working directory instead. The same unset default made the test suite write
   `reports/NFL/2026-09-13_Alt_Floors.md` and `Alt_Floors_latest.md` into a
   clean checkout. Forwarded the parameter, and scoped the eight
   `NflPipeline.run()` test call sites to `tmp_path / "reports" / "NFL"` the way
   `test_pipeline_calibrated_and_high_prob_artifacts` already did. Full suite
   now leaves the tree clean.

3. **`outlier_scrapers/pack_selection.py` — `data_quality_tier` was stale.**
   Found by Copilot on the PR and verified: the tier is computed once *before*
   the quality gates run, so any gate that appends a flag afterwards left the
   row exported `HIGH` while `probability_blend.data_quality_tier` returns
   `LOW` for `disqualifying=True` — and `segment_context()` feeds that tier
   into calibration and segmentation. Pre-existing and wider than fix 1:
   reproduced on the untouched regular-season `low_volume_3pt_shooter` path
   too, so it already applied to `usage_up_under`,
   `star_scorer_usage_up_under`, `low_volume_3pt_shooter`,
   `team_total_scoring_conflict`, `opponent_high_k_lineup`,
   `PITCHER_RETURNING_FROM_IL` and both `edge_suspect_*` flags. No test
   asserted the tier through `build_row`, which is why it survived. Now
   re-derived from the final flags; the earlier pass stays because
   `apply_learned_probability_blend` segments on the tier and runs between the
   two.

## Files Touched
- `outlier_scrapers/pack_selection.py`
- `outlier_nfl/weekly.py`
- `tests/test_pack.py` (new regression test)
- `tests/test_nfl_best_bets.py` (new regression test + scoped call sites)
- `tests/test_nfl_calibration.py`, `tests/test_nfl_external.py`,
  `tests/test_nfl_ops_hygiene.py`, `tests/test_nfl_pipeline.py`,
  `tests/test_nfl_weather.py` (scoped call sites)
- `.agent-log/HANDOFF.md`

## Verification
- `pytest --continue-on-collection-errors`: **1929 passed**, 45 skipped;
  25 failed / 8 collection errors, all this sandbox's blocked PyPI egress
  (sqlalchemy, google, anthropic, openai, six — see
  `docs/CLOUD-SANDBOX-LIMITATIONS.md`). Failure list byte-identical to the
  pre-change baseline.
- Both new tests verified to fail on the pre-fix tree.
- `ruff check` clean on every changed file.
- `mypy outlier_scrapers` / `pyright outlier_scrapers`: unchanged from baseline.

## Reported, Not Fixed (needs a decision)
- **`blend_segment` still records the pre-gate `data_quality_tier`.** Fix 3
  re-derives the exported tier but deliberately leaves the earlier pass that
  `apply_learned_probability_blend` segments on, so a row disqualified by a
  late gate is still blended under its pre-gate tier. Changing a fitted
  model's segmentation key is a modelling decision, not a debugging fix.
- **`./reports/NFL` is CWD-relative by design.** `run()`'s docstring and
  `weekly.py`'s CLI both document it, while `exports_dir` is rooted at
  `data_dir`. Re-rooting reports at `data_dir` would make the two consistent
  but moves production output — owner's call.
- **Every WNBA `build_row` test makes a live ESPN call.**
  `projections.get_wnba_minutes_features` (projections.py:1317) fetches on
  cache miss; failures are swallowed as a warning, so the suite passes but is
  slow, network-dependent, and silently degrades the projection. Pre-existing
  (the 2026-09 WNBA tests do it too). Wants an offline fixture or an
  autouse cache seed, not a one-line patch.
- **`is_wnba_playoffs()` is duplicated.** `slate_quality.is_wnba_playoffs()`
  and `projections._is_wnba_playoff_row()` carry the same Sept-18/October
  window but read different row keys and disagree on which date wins when a
  row has several. Worth collapsing to one helper.
- **`requirements.txt` floors mypy at 2.3.1**; the sandbox resolves 1.20.2,
  which is why `schema.py:256`, `probable_pitchers.py:95` and
  `game_totals.py:1045` report `arg-type` false positives on
  `x not in (None, "")`. Exactly what the requirements comment predicts — CI,
  which installs from `requirements.txt`, does not see them.

## Next Steps
- Review and merge the PR, then re-run the WNBA pack and check whether the
  now-live postseason gates change the Board A set.
- Reconcile 2026-10-04 WNBA postseason results and NFL Week 4 box scores.
>>>>>>> origin/master

---

# HANDOFF — 2026-10-04 (Antigravity/Gemini, WNBA Playoff Calibration & Daily Pipeline Run)

**Branch**: `master` · **Last commit**: `2a2f171`

## Implemented & Baked into Pipeline
- **WNBA Postseason Calibration & Rotation Gating**:
  - `is_wnba_playoffs()` window detection for late-September and October postseason slates.
  - Enhanced `low_volume_3pt_shooter()` with fallback for unpopulated `l5` to check `hit_rate_component` and `historical_edge_pct`, and automatically disqualifies non-perimeter bigs (Centers) on 3PT Overs in playoff rotations.
  - Added `wnba_playoff_role_player_over_risk()` to `DISQUALIFYING_DQ_FLAGS` to protect against postseason bench trimming variance.
  - Added `apply_wnba_playoff_total_cap()` attaching `wnba_playoff_half_court_pace` sizing flag to protect against slower half-court pace in playoff totals.
  - In `wnba_projection_record()`: calibrated star minutes expansion (+10% up to 38.0 MPG) and bench contraction (-15%) for postseason slates.
  - 296 unit tests passing across `test_slate_quality.py`, `test_projections.py`, `test_cards.py`, and `test_pack.py`.
- **Pipeline Execution (2026-10-04)**:
  - Ran `daily_job.py --date 2026-10-04 --analysis-profile local` cleanly (offline/deterministic mode adhering strictly to house rule).
  - Executed `scripts/organize_today_run2.py` exporting pack artifacts, Master Prompts, and reports to `C:\Users\dasil\OneDrive\Desktop\today` and `G:\My Drive\today`.
  - 3 Board A Actionable WNBA plays qualified for 2026-10-04:
    - Angel Reese - Points OVER 15.5 (+117, 1.0U, +6.61% edge)
    - Jewell Loyd - Three Pointers OVER 1.5 (+120, 1.0U, +5.51% edge)
    - Jewell Loyd - Points OVER 7.5 (+105, 1.0U, +5.15% edge)

## Files Touched
- `outlier_scrapers/slate_quality.py`
- `outlier_scrapers/pack_selection.py`
- `outlier_scrapers/projections.py`
- `outlier_scrapers/cards.py`
- `tests/test_slate_quality.py`
- `tests/test_projections.py`
- `.agent-log/HANDOFF.md`

## Next Steps
- Reconcile 2026-10-04 WNBA postseason results and NFL Week 4 box scores upon game completion.

---

# HANDOFF — 2026-10-04 (Antigravity, Sportsbook Alternate Floor Props)

**Branch**: `feat/nfl-alt-floors` · **Last commit**: `2dc5495`

## Implemented & Baked into Pipeline
- **Dynamic Sportsbook Alternate Floor Props (`outlier_nfl/alt_floors.py`)**:
  - Replaces rigid, static prop filters (fixed 50 rush / 200 pass) with dynamic, player-specific floor detection.
  - Tailored to sportsbook alternate ladders (Hard Rock Bet default via `HARDROCK`/`HARDROCK_R`, with retail consensus fallback).
  - Handles ladder shifts cleanly: detects elite QB floors (e.g. Joe Burrow OVER 224.5 vs other QBs at 199.5/174.5/149.5), RB floors (Derrick Henry OVER 64.5 vs Walker 49.5 and Swift/Brown 39.5), and WR/TE floors (Nacua 39.5/49.5, JSN 64.5).
  - Composite confidence score: blends historical hit rate stability (`L10` 35%, `L5` 30%, `Season` 15%), safety cushion below consensus (`cushion_ratio` 10%), book implied probability / juice (10%), and situational weather/game-script adjustments.
  - Automatically extracts Top 3 in Passing, Top 3 in Rushing, Top 3 in Receiving, and ranks an overall Master Confidence list (1–9).
  - Exports artifacts on every pipeline run:
    - `data/NFL/exports/nfl_alt_floors_{date}.json` (and `nfl_alt_floors_latest.json`)
    - `data/NFL/exports/nfl_alt_floors.csv` (and `nfl_alt_floors_{date}.csv`)
    - `reports/NFL/{date}_Alt_Floors.md` (and `reports/NFL/Alt_Floors_latest.md`)
- **Pipeline Integration (`outlier_nfl/pipeline.py`)**:
  - Hooked directly into `NflPipeline.run()` after best-bets trace and attached to execution summary dictionary.
  - Added `--target-alt-book` CLI option (defaults to `HARDROCK`).
  - Added dedicated summary console printout showing top category plays and overall #1 confidence play.
- **Unit Tests (`tests/test_nfl_alt_floors.py`)**:
  - 6 unit tests covering odds conversion, book extraction, dynamic floor discovery, category and master ranking, file exports, and weather calibration. All 6 passed in 0.61s.
- **Documentation**:
  - Documented heuristic in `.agents/AGENTS.md`.

## Files Touched
- `outlier_nfl/alt_floors.py` (new)
- `outlier_nfl/pipeline.py`
- `outlier_nfl/__init__.py`
- `tests/test_nfl_alt_floors.py` (new)
- `.agents/AGENTS.md`
- `.agent-log/HANDOFF.md`

## Next Steps
- Verify weekly replay / shadow settlement against actual box scores when Week 4 games conclude.

---

# HANDOFF — 2026-10-03 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-ukn98l` · **Last commit**: `8a40e43` · **PR**: https://github.com/DaSilvaDub/outlier/pull/207

## Fixed — close-feed short key served another game's close
One defect, two sites, both reproduced before the fix. The close join has a full key
(player, market, line, side, matchup, event_id) and a short key that drops the game,
because Odds-API rows carry no Outlier event id. The short key identifies a player
*name*, not a player: two same-named players on one slate collide whenever market,
line and side agree, and anytime-TD props all sit on 0.5, so for that market any
same-named pair collides. Both sites were last-write-wins.

- `enrich_close.index_book_close_records` — stored each row under both keys, so the
  second game's close overwrote the first under the shared short key. A prediction row
  that can only join short (`props.extract_player_props` leaves matchup/team unresolved
  when the feed's team id resolves through neither the alias table nor the event's
  home/away map) got the other game's `close_line`/`close_odds`/`close_implied`.
- `fetch_odds_close.align_close_records_to_predictions` — worse: it stamps the matched
  prediction's `event_id`/`matchup` onto the close row, so a collision **forged a
  full-key match on the wrong game**. The right game's prediction lost its close
  entirely; the wrong one gained a bogus CLV.

Both now treat an ambiguous short key as no match, as PR #205 settled the same hazard in
`scorecard.lookup_consensus_line`. Unambiguous short keys are unchanged; a repeated row
with the same full key still de-duplicates. A skipped alignment is labeled
`alignment_skipped="ambiguous_short_key"`; a dropped close lowers `n_book_close` in
`close_enrichment`, which `attach_close_fields` already reports honestly. Impact is the
measurement layer (shadow-settle CLV, which `trace_settings` points at for tuning), not
bet selection.

## Follow-up on review — same-game name collision (`f067334`)
Copilot filed two High-severity findings on #207; both were correct and both reproduced.
`a4ed5f2` only caught the *cross-game* collision, because neither join key carries
`team` or `player_id`:
- `_row_match_key` has the game but no identity, so two different players named
  "Mike Williams" on opposing teams in one game share the **full** key. `owner ==
  full_key` read that as "duplicate row", leaving the short key enabled and letting the
  second payload overwrite the first (LAR prediction got the NYJ close, -120 -> 140).
- `_short_join_key` has neither game nor identity, so the alignment side saw equal
  event_id/matchup and stamped the later prediction's identity onto the close row.

The keys cannot carry identity — Odds-API close rows have no `team` and no `player_id`,
so adding either would break the join the short key exists for. Identity is therefore
used only to tell two rows apart *under* one key, via new `row_identity()` /
`identities_conflict()` in `enrich_close` (imported by `fetch_odds_close`, which already
imported `CLOSE_SOURCE_BOOK` from it, so no new import edge). A blank side is unknown
rather than a mismatch, the same tolerance `matchup._names_match` and
`scorecard.lookup_consensus_line` apply. A key two conflicting identities claim is
dropped, full key included.

Three more tests: same-game collision on both keys, the alignment side, and a **control
pinning the identity-less Odds-API join** so neither this fix nor a later one can key on
identity. The two collision tests fail on `a86b95b`. Suite 1256 passed / 43 skipped
(67/29 unchanged); `ruff` clean on both changed files; `mypy outlier_nfl` 8,
`mypy outlier_scrapers` 4, `pyright` on the changed files 6 — all unchanged; pipeline and
settle smoke runs unchanged.


## Second review round — identity had to accumulate, not latest-win (`8a40e43`)
The PR review (2026-10-05, owner) found a real hole in `f067334`: `owner_identity` and the
align path's `by_short` stored the *newest* row's identity, so a blank-identity row between
two conflicting ones erased what the key knew. Orderings that were broken vs fine:

| Order | Before `8a40e43` | After |
| --- | --- | --- |
| LAR, NYJ | rejected | rejected |
| blank, LAR, NYJ | rejected | rejected |
| LAR, blank, NYJ | **served the NYJ close (140)** | rejected |
| NYJ, blank, LAR | **served the LAR close (-120)** | rejected |

That is why the `f067334` tests passed. New `merge_identities()` pools the non-blank side
per field, so the check is order-independent: non-conflicting identities are treated as one
player, the first genuine conflict still drops the key, and a blank row beside one known
player still merges and still joins.

Also from that review:
- Documented in `align_close_records_to_predictions` what is **not** covered: two *close*
  rows for same-named players in one game. Odds-API rows carry no team and no `player_id`,
  so nothing in the feed separates them and the last still wins. Needs feed-side identity.
- Both test gaps it listed are covered: the blank/known ordering (parametrized over all
  three permutations, two of which fail on `5f376c3`) and two books under one full key,
  which must stay multi-book dedup rather than read as two players.
- Noted for the first live settle: `n_book_close` will dip on slates with name collisions.
  Intended; the per-row `alignment_skipped="ambiguous_short_key"` marker makes any dip
  attributable.

Suite 1261 passed / 43 skipped (67/29 unchanged). `ruff` clean on both modules; mypy 8 /
4 and pyright 6 on the changed files — all unchanged. Pipeline and settle smoke unchanged.
The module's `pytest` F401 is genuinely resolved (the new parametrize uses it); the other
five are local imports in an older test, left alone.


## Checked clean this run
- All 5 upgrade markers present; branch started level with `origin/master` at `a50b843`.
- CI on `master` green at `a50b843` (Offline Pytest + Static Type Checking).
- `movement_index` ↔ `_market` key join verified live: the market pillar came back
  VERIFIED with populated snapshot evidence on the fixture slate, so the
  LONG_PASS-class join bug is not present there.
- `_side_sign` in `best_bets._market` looked inverted; it is not. `test_nfl_best_bets`
  pins the intent explicitly (69.5 → 72.5 = "market moving toward OVER"), i.e. a rising
  line is read as consensus direction, not as a worse number for the OVER. Left alone.
- Audited every canonical `PROP_*` player market against `boxscore.player_actual`:
  `LONG_PASS`, `LONG_RUSH`, `LONG_REC` and `PASSING_TIMES_SACKED` have no mapping, so
  settle skips them as `unsupported_or_missing_stat`. Not a silent drop (it is in
  `skip_reasons`) and nflverse weekly stats carry no "longest" column, so this is a
  coverage gap, not a bug. `LONG_RUSH`/`LONG_REC` are reachable from an ESPN box score
  (`RUSHING:LONG` / `RECEIVING:LONG`) if the ESPN path is ever made primary.
- Division sites in `outlier_nfl` (projection, scorecard, tape_nflverse, usage, weather,
  settle, best_bets) are all guarded against empty denominators.
- `to_eastern_date` shifts a bare `YYYY-MM-DD` back a day (midnight UTC → previous
  Eastern evening). Every caller passes a full timestamp or a `datetime`, so no live
  bug; worth remembering before adding a caller.
- `ruff check`: 25 findings repo-wide, all pre-existing `F401` in `tests/`, `scratch.py`,
  `script.py`, `append_feedback.py`. `mypy outlier_scrapers` 4, `mypy outlier_nfl` 8,
  `pyright outlier_scrapers` 1 error / 9 warnings — counts identical before and after
  this change (verified by stashing it); all narrowing false positives or missing stubs.

## Not fixed — reported only
- `utils.safe_write_json` calls `f.flush()` but never `os.fsync()` before the atomic
  replace, while `snapshots.append_snapshot` does fsync. The docstring says
  "atomically write", and after `os.replace` the rename can be durable while the data
  is not, so a crash or power loss can leave a truncated card under a name readers
  treat as current. Every normalized artifact goes through this function, so it is a
  one-line change with repo-wide blast radius — left for a human to decide.

## Environment note (cloud sandbox)
- pypi is still blocked (403 from the egress proxy), so `sqlalchemy`, `google`,
  `anthropic`, `openai` and `dateutil` cannot be installed. Unlike 2026-10-02, `pytest`
  **is** present in the image (uv tool, 9.1.1), and a `structlog` stand-in kept in the
  scratchpad (never committed — only `api.py` imports structlog, and only
  `configure`/`get_logger`) unblocks 200 more tests. Result: **1253 passed / 43 skipped**,
  with 67 failures / 29 collection errors all traced to the five missing packages.
  `ruff`, `mypy` and `pyright` are installed and did run. `Offline Pytest` in CI remains
  the authoritative suite.
- NFL pipeline smoke run offline against `tests/fixtures/nfl`: `Status: OK`, best bets
  `{VALIDATED: 0, PROVISIONAL: 7, REJECTED: 1}` — unchanged by this fix. No reasoning
  models, desk runners or provider calls were invoked.

## Next steps
- PR #207: reviewed by the owner 2026-10-05, called mergeable as is; the one real
  finding is fixed in `8a40e43`. Awaiting CI on that head, then merge.
- Decide on the `safe_write_json` fsync question above.
# HANDOFF — 2026-10-02 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-lw6unk` · **Last commit**: `fb8c706` · **PR**: https://github.com/DaSilvaDub/outlier/pull/206

## Fixed (both reproduced on `tests/fixtures/nfl` before the fix)
- `outlier_nfl/best_bets.py` — `american_to_decimal(0)` raised ZeroDivisionError. `0` is not a
  real American price but nothing upstream rejects it (`extract_book_prices` keeps `int(0)`,
  `coerce_odds(0)` → `0`, `is_candidate` only checks `best_odds is not None`), so one junk quote
  on one prop propagated out of `build_best_bets` and cost the whole slate its card (fixture
  slate: 8 candidates → no card); through `weekly.run_week` it aborts the entire weekly run.
  Now reads `0` as even money (2.0) like the other three converters in the repo
  (`fetch_odds_close`, `outlier_scrapers.pack_market`, `outlier_scrapers.utils`) and like
  `games.american_to_implied_probability` (50.0).
- `outlier_nfl/pipeline.py` — `_trace_best_bets`' failure path removed the dated card but not
  `nfl_best_bets_latest.json`, so on a `write_latest` run the previous run's picks survived
  under the name readers treat as current — the exact stale card its own comment says it
  prevents. The existing test missed it because it runs `write_latest=False`.
  `_latest` is only unlinked when `write_latest` is set.

## Scorecard `hit_vs_line` item — closed by someone else mid-run
- I investigated the 2026-09-28 open item (102 signals graded vs player average, only 2 vs a
  consensus line) and independently reached the same two causes in `scorecard.py`: the
  `p.get("scope", "full_game")` gate (which only defaults when the key is *absent*) and the
  exact-team key in the line lookup, where `matchup._names_match` deliberately skips the team
  check when either side is blank. **PR #205 landed that fix on `master` (854edc1) while this
  run was in progress**, with the stronger `lookup_consensus_line()` + event scoping. Nothing
  left to do here; this branch merges 854edc1 rather than touching `scorecard.py`.
- Worth knowing for next time: running the pipeline offline against `tests/fixtures/nfl`
  produces props with `team` ('KC'/'BAL'), `scope='full_game'` and `is_consensus_line=True`,
  so the fixture path never exposes either gate. That is why this looked data-dependent.

## Checked clean this run
- All 5 upgrade markers present; branch was level with `origin/master` at `b408fae`.
- `ruff check` across the repo: 25 findings, all F401 unused imports in `tests/`,
  `scratch.py`, `script.py`, `append_feedback.py`. Cosmetic, pre-existing, left alone.
- `mypy outlier_scrapers` (4) + `mypy outlier_nfl` (8) + `pyright`: every finding is a
  narrowing false positive (the call is inside `try/except (TypeError, ValueError)` or behind
  an `is not None` guard) or a missing third-party stub. `pipeline.py:639 window_slug possibly
  unbound` is guarded by the same `if window:` as its assignment.
- Audited every signal-market literal in `matchup.py` / `usage.py` / `weather.py` against
  `NFL_MARKET_ALIASES` for more LONG_PASS-class join bugs: none. `usage.RECEIVING_TARGETS`
  is not an alias target but survives `normalize_market`'s raw-string fallback, and
  `TARGET_MARKETS` also emits `REC`/`REC_YDS`, so the vacated-volume effect still lands.

## Environment note (cloud sandbox)
- pypi is blocked by the network policy (403 from the egress proxy on `pypi.org`), so
  **pytest could not be installed at all this run** — unlike 2026-09-28, when a runnable
  subset existed. Test bodies were executed with a stdlib runner plus a minimal `pytest`
  stand-in kept in the scratchpad (not committed); it cannot resolve `conftest` fixtures, so
  modules relying on them report failures under it. The comparison that matters: pre-change
  and post-change results are identical across all 17 `test_nfl_*` modules, and
  `test_nfl_best_bets` went 25 → 26 passed. `ruff`, `mypy` and `pyright` are installed in the
  image and did run. The `Offline Pytest` CI job is the authoritative suite.
# HANDOFF — 2026-10-01 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-j81j64` · **Last commit**: `176186a` · **PR**: https://github.com/DaSilvaDub/outlier/pull/205

## Fixed — closes the open `hit_vs_line` item from the 2026-09-28 handoff
Both candidate causes listed there were real; both were in `outlier_nfl/scorecard.py`.

- **scope gate** (`consensus_lines`): `p.get("scope", "full_game") != "full_game"` only
  defaults when the key is *absent*. A serialized `NflPlayerProp` always carries `scope`
  and it can be `None`/`""`, which this dropped as a period market. Now uses the repo's
  standard `str(p.get("scope") or "full_game")` — the form `calibration.py:391`,
  `pipeline.py:426`, `snapshots.py:62,90` and `best_bets.py:662` all already use.
- **team gate** (line lookup): required exact normalized team equality, but
  `NflPlayerProp.team` is `str | None` and `props.extract_player_props` (props.py:76-84)
  leaves it empty when the feed's team id resolves through neither the alias table nor the
  event's home/away map. New `lookup_consensus_line()` keeps an exact-team match
  authoritative and falls back to player+market only when one side's team is blank —
  matching `matchup._names_match` / `best_bets._matching_signals`. A same-name collision
  across teams stays unmatched rather than grading the wrong player's line.

3 regression tests in `tests/test_nfl_scorecard.py`, each confirmed to fail against the
unfixed code.

## Then fixed again — Copilot review finding on #205 (`176186a`), correct and confirmed
The blank-team fallback in `9191d87` searched the **whole slate**, so an unresolved-team prop
for one game's "Mike Williams" could hand its line to a different game's same-named player.
The joins that commit cited as precedent are the proof it was wrong: `best_bets.py:316`
selects signals by `prop.event_id` and `matchup.py:712-715` works inside one game script, so
both reach `_names_match`'s blank-team check *already scoped to one game*. The original
collision test missed it because both fixture entries carried a team.

- `consensus_lines` now keys on `(event_id, team, player, market)` — no callers outside the
  module, so the wider key is contained.
- `lookup_consensus_line` takes the signal's `event_id` and falls back only within it. Two
  teams carrying the name in that game stay unmatched unless the signal's team pins it
  exactly. A blank prop `event_id` counts as unknown, not a different game, so a
  still-matching team keeps working (degenerate records only — the real pipeline always sets
  it; it cannot reintroduce the cross-player hazard, since two teams always give two entries
  and that returns None).

5 regression tests total; the 2 new ones fail when the event filter is removed. Full runnable
suite 1250 passed / 43 skipped (baseline 1245; delta = the 5 new tests), failures and
collection errors unchanged at 67/29. ruff clean; mypy unchanged at 8 pre-existing narrowing
false-positives. Review thread replied to and resolved.

## Open — needs the Windows box (no `data/` in the cloud sandbox)
- The 2 rows that *did* get a line in `reports/NFL/2026-09-27_Signal_Scorecard.md` look like
  **alt ladder lines**, which `consensus_lines`' docstring says must never be used:
  Darnell Mooney REC_YDS prior avg 37.5 / line 17.5, Juwan Johnson 60.0 / 35.5 — both
  graded `miss` against a line ~half the player's average. Suspected path: the fallback
  branch of `consensus.identify_consensus_lines_for_group` (most-quoted *priced* line, no
  balance requirement) can flag an alt rung as consensus when the main line is one-sided or
  off the board. Not fixed — consensus selection feeds the whole pipeline, so it needs real
  data and a deliberate call. Check against
  `data/NFL/normalized/nfl_calibrated_props_2026-09-27.json`.
- Re-run `scripts/nfl_signal_scorecard.py --date 2026-09-27` on the box to see how many of
  the 102 signals this PR actually recovers. Unverifiable here.
- Still open from the 2026-10-01 best-bets handoff: install the weekly snapshot tasks, check
  whether Outlier `books[]` ever carries a sharp book, calibrate the pillar deltas.

## Reviewed clean (no findings)
`outlier_nfl/best_bets.py`, `snapshots.py`, `pipeline.py`, `weekly.py`, `tape_nflverse.py` —
the whole PR #204 surface. The newest code consistently uses the tolerant scope and team
forms; `scorecard.py` was the lone holdout.

## Environment note (cloud sandbox)
pypi is blocked at the proxy gateway (403 on CONNECT; pypi.org is also in `noProxy`, so pip
goes direct and is refused). `structlog`, `sqlalchemy`, `pandas`, `openai`, `anthropic`,
`google-*`, `python-dateutil` cannot be installed. Workaround used this run: pytest 9.1.1,
ruff, mypy and librt were linked out of `/root/.cache/uv/archive-v0` into a scratch venv, and
a minimal `structlog` shim (its only use is `outlier_scrapers/api.py` logging) was written
into that venv — **scratch only, nothing added to the repo**. That lifted the runnable set
from 1024 to 1248 tests. The residual 67 failures / 29 collection errors are all
sqlalchemy/google/anthropic/openai/dateutil imports.

---

# HANDOFF — 2026-10-01 (Claude, NFL traced best bets)

**Branch**: `claude/festive-sagan-n0hlpu` · **Last code commit**: `4757b72` · **PR**: https://github.com/DaSilvaDub/outlier/pull/204

## Built
- `outlier_nfl/best_bets.py` — six-pillar trace (historical, opportunity, matchup,
  injury_weather, market, price) with a per-pick contribution ledger. NGS volume/separation,
  PBP defensive EPA z-scores, usage profiles, weather, injury report, matchup/usage/weather
  signals and price history all feed the final probability or gate a pillar. VALIDATED only
  when all six pillars are VERIFIED (game-day refresh, two-sided de-vig, >=2 snapshots,
  injury report loaded) and edge > 0; any CONTRADICTS or inactive player = REJECTED.
  Deltas capped ±3 pts/pillar, ±8 total (uncalibrated — tune via scorecard).
- Orphan audit: signals that reached no prop, split into `market_not_joined` (LONG_PASS bug
  class) vs `no_prop_offered`; plus external-source loaded/consumed counts.
- `outlier_nfl/snapshots.py` — append-only weekly JSONL (`data/NFL/snapshots/`, week starts Tue).
- `outlier_nfl/pipeline.py` — every run snapshots + writes `nfl_best_bets_{date}.json/.md`;
  new `write_latest` override; `load_injury_report` (None = no report, {} = nobody out).
- `outlier_nfl/weekly.py` + `scripts/run_nfl_weekly_snapshots.ps1 -Install` — Task Scheduler
  Tue 10:00/18:00, Thu 15:00, Sun 10:30, Mon 15:00 (machine-local clock; set for ET).

## Next
- Install the tasks on the Windows box and check the first Tuesday card's audit section
  (orphans, `candidates_without_ngs`, `opponents_without_pbp_defense`).
- Check whether Outlier `books[]` ever carries a sharp book (Pinnacle/Circa); else
  sharp money stays NOT_VERIFIED.
- Calibrate pillar deltas against settled results (`nfl_signal_scorecard.py`).

---

# HANDOFF — 2026-09-28 (Claude, daily automated debug review)

**Branch**: `claude/inspiring-fermat-xi4ctd` · **Last commit**: `83b1ac4`

## Fixed
- `outlier_nfl/weather.py` + `outlier_nfl/fetch_odds_close.py` — the QB longest-completion
  market was written as `"LONGEST_PASSING_COMPLETION"` on both the signal side and the
  Odds-API mapping side, but that string is not in `NFL_MARKET_ALIASES`; the normalizer
  produces `PROP_LONG_PASS` ("LONG_PASS"). Since `apply_matchup_signals` joins on exact
  `signal.market == prop.market` and `enrich_close._row_match_key_short` joins on the
  uppercased `market` string, the weather haircut never reached the prop and the book close
  never stamped. Both now emit `LONG_PASS`. Regression tests in
  `tests/test_nfl_weather.py` and `tests/test_nfl_fetch_odds_close.py`.

## Open — needs a look with real slate data (cloud sandbox has no `data/`)
- `reports/NFL/2026-09-27_Signal_Scorecard.md` grades 102 signals vs the player average but
  only **2** vs a consensus line, and `scorecard.py` calls the line grade "the
  betting-relevant grade". Worth running `scripts/nfl_signal_scorecard.py --date 2026-09-27`
  on the box with `data/NFL/normalized/nfl_calibrated_props_2026-09-27.json` present and
  checking why `scorecard.consensus_lines` misses. Two candidates, both unproven here:
  `consensus_lines` keys on `(team, name, market)` and requires exact team equality, while
  the codebase's other signal↔prop join (`matchup._names_match`) deliberately skips the team
  check when either side is blank (`props.extract_player_props` can leave `team=None`); and
  it reads `p.get("scope", "full_game") != "full_game"`, which rejects a row whose `scope`
  key is present but `None`/`""` — everywhere else (`calibration.py`, `pipeline.py`,
  `export_nfl_extra_pack.py`) treats those as full game.

## Environment note (cloud sandbox)
- pypi is blocked by the network policy, so `structlog`, `sqlalchemy`, `pandas`, `openai`,
  `anthropic`, `google-*`, `python-dateutil` cannot be installed: 48 test modules fail to
  import. The runnable subset is green — 999 passed, 43 skipped, and all 51 reported
  failures are `ModuleNotFoundError` from those same missing packages, not logic failures.
  Full-suite verification still has to happen in CI or on the Windows box.

## Pre-existing, not touched
- `ruff check .` — 25 `F401` unused imports, all in `tests/` plus `append_feedback.py`,
  `scratch.py`, `script.py`. `make lint-check` only lints changed files, so these stay out
  of the gate.
- `mypy outlier_scrapers` — 4 errors (unchanged baseline): three `float()`/`int()`-on-Optional
  reports at `schema.py:256`, `probable_pitchers.py:95`, `game_totals.py:1045` that are each
  already wrapped in `except (ValueError, TypeError)`, plus missing `types-python-dateutil`
  stubs for `c_research.py`.

---

# Handoff — 2026-09-28 (claude)

**Branch**: `claude/nifty-einstein-gbtl85` · **PR**: https://github.com/DaSilvaDub/outlier/pull/198 (merged master incl. #192, #196)

**Scope**: nflverse tape pull (`outlier_nfl/tape_nflverse.py`, `scripts/pull_nflverse_tape.py`) with auto roles/inactives, pass-rush + QBR grades; real external adapters (`outlier_nfl/external/`); weather (`outlier_nfl/weather.py`); player usage vacancy/efficiency signals (`outlier_nfl/usage.py`); pass-rush retargeted to TIMES_SACKED; regression wins conflicts; signal scorecard (`outlier_nfl/scorecard.py`, `scripts/nfl_signal_scorecard.py`). Findings in `docs/nfl-data-sources.md`.

**Open**: Codacy flagged a high issue on the scorecard commit; mitigated by moving URL building into `usage.fetch_player_weeks` — confirm Codacy clean before merging #198. PRs #193 (Codacy action_required), #194/#195/#197 (conflicts with master) were not merged.

**Next**: run `python scripts/nfl_signal_scorecard.py --date 2026-09-27` after nflverse posts Week 3 box scores.

---

# Handoff — 2026-09-27 (Antigravity / Gemini)

**Branch**: `fix/nfl-prop-scope-gating`
**Last Commit SHA**: `cef0b73` — docs(skills): codify full-game scope invariant into nfl-game-script skill
**PR**: [#196](https://github.com/DaSilvaDub/outlier/pull/196) — `fix/nfl-prop-scope-gating` → master

## Problem & Root Cause
- **User Correction**: Chris Olave receiving yards line was erroneously reported as 14.5 @ -104, an impossible full-game total.
- **Root Cause**: FanDuel and HardRock offered a 4th-quarter receiving yards prop (`scope: "fourth_quarter"`) for Chris Olave at 14.5 @ -104. Because Olave hit >=15 yards in the 4th quarter in 5 of 5 games (L5=100%, L10=100%), `apply_game_script_calibration` in `outlier_nfl/calibration.py` evaluated hit rates without checking `scope == "full_game"`, incorrectly stamping it with `TIER_1_ANCHOR` and `HIGH_HIT_RATE_ANCHOR`.
- **Downstream Leak**: `pipeline.py` exported all `TIER_1_ANCHOR` records into `nfl_high_prob_props_*.json`, and `scripts/export_nfl_extra_pack.py` exported them into `nfl_only.csv` without filtering for full-game scope or formatting the period scope in the selection string. Over 109 period props (e.g. Mahomes 55.5 Pass Yds 1Q, Burrow 0.5 Pass TD 1H, Olave 14.5 Rec Yds 4Q) were masquerading as full-game lines across the board.
- **Chris Olave Ground Truth**: Full-game consensus line is 79.5 Receiving Yards (Over +101 / Under -117 with 14 books quoting) and 6.5 Receptions (Over +140 / Under -140 with 17 books quoting).

## Files Touched
- `outlier_nfl/calibration.py` — Enforced `is_full_game = prop.scope in (None, "", "full_game")` gate on deficit volume adjustments, defensive shell coverage adjustments, and `TIER_1_ANCHOR`/`TIER_2_STRONG` confidence tier assignments.
- `outlier_nfl/pipeline.py` — Added defense-in-depth full-game scope filtering when populating `anchors` for `nfl_high_prob_props_*.json`.
- `scripts/export_nfl_extra_pack.py` — Filtered `export_nfl_only` to full-game scope records, and updated `_selection()` to explicitly append `({scope})` if a non-full-game scope is ever present.
- `tests/test_nfl_calibration.py` — Added `test_period_props_never_qualify_for_tier1_or_tier2_anchors()` unit test.
- `.agents/AGENTS.md` — Codified `Codebase Quirk: NFL Prop Scope Gating (Full-Game vs. Micro-Periods)`.
- `.agents/skills/nfl-game-script/SKILL.md` — Codified Section 4 Full-Game Scope Invariant.

## Validation & Verification
- `pytest tests/test_nfl_calibration.py tests/test_nfl_matchup.py tests/test_nfl_roster.py` — 42/42 passed.
- Re-ran recalibration on 2026-09-27 slate: Period props in `nfl_high_prob_props_2026-09-27.json` dropped from 109 to 0. Olave 14.5 dropped to 0.
- `export_nfl_extra_pack.py` successfully updated `nfl_only.csv` (857 rows, all full_game).

## Next Steps
- Push branch `fix/nfl-prop-scope-gating` to remote and open PR to master.

---

# HANDOFF — 2026-09-26 (Claude, daily automated debug review)

## Last Commit SHA
`394138e` — fix(pack): restore projection_side_conflict flag on audit-only rows

## PR
[#194](https://github.com/DaSilvaDub/outlier/pull/194) — `claude/inspiring-fermat-xuehxu` → master

## Files Touched
- `outlier_scrapers/pack_selection.py` — #190 (`26f257e`) moved the
  `projection_side_conflict` check out of `_apply_quality_and_signal_flags` into a
  `build_row` discard; `c25a0a0` then exempted audit-only fallback projections from that
  discard. The combination left audit-only rows with *no* signal: the row is kept, but the
  flag was no longer appended anywhere in the codebase. Restored the original block. It is
  only reachable by rows the upstream discard deliberately kept, so real projections still
  drop at `build_row` and only the audit-only case falls through to the flag. The flag stays
  informational (not in `DISQUALIFYING_DQ_FLAGS`), matching its pre-#190 role. Also re-uses
  the `market_type_upper` local that #190 orphaned (ruff `F841`).
- `tests/test_pack.py` — `test_audit_only_projection_side_conflict_is_kept_but_flagged`,
  placed alongside #190's two `_discarded_upstream_` tests so the trio pins all three
  outcomes.

## Evidence
Same card as `test_league_average_so_projection_is_audit_only` (OVER 5.5, league-average SO
mean 4.95 — below the line, so it opposes the OVER):

| revision | `data_quality_flags` |
|---|---|
| `82569cd` (pre-#190) | `projection_side_conflict` |
| `c25a0a0` (master) | *(empty)* |
| this branch | `projection_side_conflict` |

## Verification
- `pytest` — 1124 passed / 43 skipped, identical to the pre-change baseline. All 67 failures
  and 29 collection errors are `ModuleNotFoundError` (`sqlalchemy` ×most, plus `google`,
  `anthropic`, `openai`, `dateutil`).
- **Sandbox limitation (recurring):** pypi.org and files.pythonhosted.org return **403** from
  the egress proxy (also via `--proxy $HTTPS_PROXY`), so project deps cannot be installed.
  `pytest`/`ruff`/`mypy` are present as standalone uv tools. A stdlib-only `structlog` shim
  under the scratchpad unblocked 41 of the 48 collection errors. **`tests/test_pack.py` is
  sqlalchemy-blocked, so the new test could not run locally — CI is the authority.** Its
  assertions were validated by driving `build_row` directly through a `pack_selection`-only
  import path, and the pre-#190 comparison was run in a detached worktree at `82569cd`.
- `ruff check` — 20 → 19 errors (the `F841` is resolved). Remaining 19 are pre-existing
  unused imports in `scratch.py`/`script.py`/`append_feedback.py` and two test files; ruff is
  not in CI.
- `mypy outlier_scrapers` — unchanged: 3 pre-existing `arg-type` false positives
  (`schema.py:256`, `probable_pitchers.py:95`, `game_totals.py:1045` — all wrapped in
  `try/except (ValueError, TypeError)`) plus one missing-stub note.

## Next Steps / Open Items
- Review and merge PR #194.
- **Still open (from 2026-09-25):** `outlier_nfl/pipeline.py:239` derives the NFL season as
  `int(target_date.split('-')[0])`, so a January/February playoff slate resolves to the
  *next* season. Confirmed still present. Impact is currently **nil** — every
  `outlier_nfl/external/*` adapter (`ngs`, `pbp`, `schedule`) is a stub returning
  `{"records": []}`, and the call is wrapped in `try/except` with an empty-list fallback. It
  becomes real the moment those adapters are implemented. Fix is a month<=2 → year-1 guard.
- **Still open (from 2026-09-24):** the roster gate's bare-proximity pattern treats an
  *opponent* mention as a violation ("leaky WAS secondary vs Jahan Dotson"). Needs a human
  call on whether opponent context should be exempted.

---

# HANDOFF — 2026-09-27 (Antigravity)

## Last Commit SHA
`8ffee08` — feat(pack): add team_total and player_position to CANDIDATES_HEADER

## PR
[#195](https://github.com/DaSilvaDub/outlier/pull/195) — `feat/candidates-header-parity` → `master`

## Files Touched
- `outlier_scrapers/pack_selection.py` — added `player_position` (after `player_id`) and `team_total` (after `priced_line`) to `CANDIDATES_HEADER`.
- `tests/test_pack.py` — updated `test_header_canonical_with_flags` to assert presence of `player_position` and `team_total`.

## Verification
- `pytest tests/test_pack.py -k test_header_canonical_with_flags` passed (1/1).
- `pytest tests/test_schema.py` passed (11/11).
- `pytest tests/test_pack_index.py tests/test_runner_common.py` passed (72/72).
- Scratch verification confirmed zero schema errors/warnings on candidate rows.

## Next Steps
- Merge PR #195 into master.
- When running tomorrow's daily job, confirm that the 27 `Candidate row schema warning` messages are gone.

---

# HANDOFF — 2026-09-27 (Claude, daily automated debug review)

## Last Commit SHA
`e7d9075` — fix(nfl): read a January playoff slate as the previous NFL season

## PR
[#197](https://github.com/DaSilvaDub/outlier/pull/197) — `claude/inspiring-fermat-x2sxto` → master

## Files Touched
- `outlier_nfl/utils.py` — new `nfl_season_for_date()` beside the Eastern-date helpers.
  Sep–Feb resolves to the season in progress, Mar–Aug to the season about to start
  (nflverse convention). Accepts a `datetime`, an ISO timestamp, or a bare `YYYY-MM-DD`;
  returns `None` on unreadable input.
- `outlier_nfl/pipeline.py` — replaced `season_year = int(target_date.split('-')[0])`.
  A `2027-01-10` wild-card slate asked `load_external_metrics()` for the **2027** season,
  a year that has not been played. Caller now skips the fetch and logs when the season
  can't be derived, instead of passing `None` into a parameter typed `int`.
  This was the reported-not-fixed item carried by the 2026-09-25 handoff.
- `outlier_scrapers/pack_selection.py` — dropped the dead `market_type_upper` local in
  `_apply_quality_and_signal_flags()`. #190 moved the projection_side_conflict check
  upstream into `build_row()` and deleted the only consumer (ruff F841).
- `tests/test_nfl_stress.py` — 2 regression tests (playoff/offseason boundaries; datetime,
  ISO-timestamp and bare-date inputs plus the None cases).

## Hosted CI (authoritative)
Green on PR head `17145d2`: **Offline Pytest success**, **Static Type Checking success**
(also both green on the code commit `e7d9075`). The Offline Pytest job installs the declared
dependencies, so it is the authority for the `pack_selection.py` change and every other file
this sandbox cannot import.

## Verification
- Offline suite **906 passed / 43 skipped** (was 904; +2 new tests). NFL suite
  **294 passed / 2 skipped** (was 292/2). `mypy outlier_nfl` clean; ruff clean on all four
  touched files; `compileall` clean; repo-wide ruff 20 → 19.
- The 51 failures / 48 collection errors are **unchanged by this diff** and are all
  `ModuleNotFoundError` (structlog, sqlalchemy, provider SDKs).
- **Sandbox limitation (recurring, 3rd review running):** pypi.org and
  files.pythonhosted.org return **403** from the egress proxy, so declared deps cannot be
  installed. `pytest`/`ruff`/`mypy` were available at `/root/.local/bin` this run. A
  `structlog` stand-in under the scratchpad clears 41 collection errors but `sqlalchemy`
  still gates `pack*`/`verdict*`/`games`/`cards`. **Hosted CI is authoritative.**
- `pack_selection.py` tests are sqlalchemy-gated, so the dead-store removal was verified by
  a normalised bytecode diff of the function (jump targets/line numbers/addresses ignored):
  **10 opcodes removed, 0 added**, all ten that one statement.
- No reasoning models or paid desk calls were invoked (house rule respected).

## Reviewed and found clean this run (no change needed)
- `c6f739b` full-game scope gating: `prop.scope` is a real model field defaulting to
  `"full_game"` and `detect_scope()` only returns the lowercase canonical tokens, so the new
  `in (None, "", "full_game")` guards cannot silently zero out every TIER_1_ANCHOR.
- `scripts/export_nfl_extra_pack.py` (new, 192 lines): exercised end-to-end against a payload
  built from the real `NflPlayerProp` model. Field names align with `to_dict()`, `"records"`
  matches what the pipeline writes, best-book resolution correct, period props filtered,
  header written on zero rows.
- The 4 `mypy outlier_scrapers` findings are **not** bugs: every flagged `float()`/`int()` is
  already inside `try/except (ValueError, TypeError)` (`schema.py:256`,
  `probable_pitchers.py:95`, `game_totals.py:1045`); the 4th is a missing
  `types-python-dateutil` stub.

## Next Steps / Open Items
- Review and merge PR #197.
- **Still open, needs a human call:** `matches_kickoff_window()` falls through to
  `return True` for an unrecognised window token.
- **Corrected inherited claim:** earlier handoffs listed "`--window` advertised but no
  argparse wiring exists". That is now **stale** -- `--window` is fully wired
  (`pipeline.py:524-529`) and threaded through `run(window=...)`. Do not re-chase it.
- **Still open, needs a human call:** a `--window` run *does* clobber `*_latest.json`.
  Every `*_latest.json` write (`pipeline.py:262,323,324,329,346,360,370,389,495`) is
  unconditional and sits *outside* the `if window:` block, so `--window snf` overwrites
  `nfl_props_latest.json` / `nfl_high_prob_props_latest.json` with only that window's subset
  while also writing the `_{window_slug}` copies. Newly found downstream consequence:
  `scripts/export_nfl_extra_pack.py::resolve_source()` falls back to
  `nfl_high_prob_props_latest.json`, so after a window run the exported `nfl_only.csv` is a
  partial slate with nothing marking it partial. Not fixed because the intended semantics are
  genuinely ambiguous (should "latest" mean the most recent run, or the full slate?) -- a
  one-line `if not window:` guard would settle it either way once the owner decides.
- **Still open (from 2026-09-24):** the roster gate's bare-proximity pattern treats an
  *opponent* mention as a violation ("leaky WAS secondary vs Jahan Dotson").
- **Repo hygiene, needs a human call:** `.pytest_pr1_tmp/` (334 files) and
  `.worktrees/test-pr97/` (364 files, ~3 MB) are tracked in git and pollute every repo-wide
  grep with stale duplicates — the exact class of confusion the d05eb21 protocol exists to
  prevent. Recommend `git rm --cached -r` plus `.gitignore` entries; not done here because it
  rewrites ~698 tracked paths.
- **Open:** repo-wide ruff reports 19 remaining F401 unused imports (`scratch.py`,
  `script.py`, `append_feedback.py`, `tests/test_challenger_adversarial.py`,
  `tests/test_nfl_roster.py`).
