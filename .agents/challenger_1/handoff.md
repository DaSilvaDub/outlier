# Handoff Report — challenger_1 (Adversarial Verification)

## 1. Observation

### Implementation Under Review
- Target file: `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights\cfb_analytics\sources\outlier.py`
- Test suites:
  - `tests/test_outlier_parsing.py`
  - `tests/test_outlier_props.py`
  - `tests/test_outlier_adversarial.py` (newly developed comprehensive adversarial test harness)

### Empirical Verification Commands & Results

1. **Adversarial Test Suite Execution**:
   - Command: `pytest tests/test_outlier_adversarial.py -v`
   - Result:
     ```
     ============================= 42 passed in 2.64s ==============================
     ```
   - All 42 adversarial stress tests passed across all 5 verification dimensions.

2. **Full Outlier Test Suite & Coverage**:
   - Command: `pytest tests/test_outlier_parsing.py tests/test_outlier_props.py tests/test_outlier_adversarial.py --cov=cfb_analytics.sources.outlier --cov-report=term-missing`
   - Result:
     ```
     Name                               Stmts   Miss  Cover   Missing
     ----------------------------------------------------------------
     cfb_analytics\sources\outlier.py     220     38    83%   119, 144-151, 160, 163-186, 191-195, 198-204, 207-212, 281, 321, 461
     ----------------------------------------------------------------
     TOTAL                                220     38    83%
     ============================== 94 passed in 7.85s ==============================
     ```
   - Total 94 tests passed, achieving 83% line coverage on `cfb_analytics/sources/outlier.py` (exceeding the >=80% acceptance threshold; the unexecuted lines are network-reaching HTTP client methods mocked or offline in test mode).

3. **Linter & Type Checker Gates**:
   - `ruff check tests/test_outlier_adversarial.py` -> `All checks passed!` (code 0)
   - `mypy cfb_analytics` -> `Success: no issues found in 40 source files` (code 0)
   - `pyright cfb_analytics` -> `0 errors, 0 warnings, 0 informations` (code 0)

4. **Multi-Ent Sync Verification**:
   - Command: `& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"`
   - Result:
     ```
     REPORT STATUS: OK
       bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)
     RUN-NONCE: caa4ab208a4e46c9  utc=2026-09-06T16:06:08Z  head=6b5181c  status=OK
     ```

---

## 2. Logic Chain

1. **Trap 1 Stress Test (Book Attribution & Ghost Leakage)**:
   - *Observation*: In `cfb_analytics/sources/outlier.py` lines 335-345, the parser iterates over `outcome.get("odds") or []` and extracts `entry.get("book")`. `outcome.get("books")` is never accessed or referenced during parsing.
   - *Stress Test*: Tested inverted books lists (`BOOK_09` to `BOOK_00`), injected ghost books (`GHOST_CASINO`, `PHANTOM_BET`, etc.), executed 50 randomized permutations of `outcome.books`, tested empty/None books arrays, and exhaustively checked all parsed rows in `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json`.
   - *Result*: In 100% of cases, `OddsRow.book` strictly matched `odds[].book`. 0% leakage was observed. `test_trap1_*` suite passed completely.

2. **Trap 2 Stress Test (Multi-Card Union & Deduplication)**:
   - *Observation*: In `outlier.py` line 239 & lines 342-345, deduplication key is `(book, market_code, side, line, team_id, scope)`.
   - *Stress Test*: Tested 4 disjoint market cards with overlapping book subsets; tested duplicate cards with conflicting prices (asserting first-seen is preserved); tested multiple lines for the same book (asserting all distinct lines survive); tested home vs away team props with identical lines (asserting both survive due to `team_id` in key); tested `full_game` vs `first_half` scopes.
   - *Result*: Books across disparate market cards are properly unioned; duplicate rows are cleanly deduplicated; valid multiple lines, scopes, and team IDs do not collide. `test_trap2_*` suite passed completely.

3. **Whitelist Stress Test (Prohibited Markets Rejection)**:
   - *Observation*: `outlier.py` defines `ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, and `PROPOSITION_TO_MARKET`.
   - *Stress Test*: Subjected parser to 12 prohibited market types (`PLAYER_PROP`, `GAME_PROP`, `CORRELATED`, `FUTURE`, `SPECIAL`, `LIVE`, `OUTRIGHT`, `TEAM_TOTAL`, etc.), 20 prohibited propositions (`DOUBLE_RESULT`, `MONEYLINE_THREE_WAY`, `WINNING_MARGIN`, player props, etc.), and 14 prohibited scopes (`first_quarter`, `second_half`, `1q`, `2h`, `overtime`, etc.), plus an adversarial payload bundle containing 50 prohibited cards surrounding 2 valid cards.
   - *Result*: Exactly 0 prohibited rows leaked into the output. 100% rejection across all unwhitelisted categories. `test_whitelist_*` suite passed completely.

4. **Team Props Side Restrictions & Fuzzy Attribution**:
   - *Observation*: `outlier.py` enforces `ALLOWED_SIDES_BY_MARKET` and implements `_resolve_team_id` supporting team ID matches and team name/alias/school substring searches.
   - *Stress Test*: Tested 11 invalid side tokens (`HOME`, `AWAY`, `YES`, `NO`, `DRAW`, `TIE`, `NONE`, `PUSH`, `1`, `0`, `None`) on `POINTS`, `OFFENSIVE_YARDS`, `RECEIVING_YARDS`, and `RUSHING_YARDS`; tested gamelines with invalid sides (e.g. SPREAD with OVER); tested label ambiguity where both home and away team names appear (e.g. "Tulsa vs Oklahoma State"); tested alien teams not in the game context; tested missing `game_context`.
   - *Result*: Non-OVER/UNDER sides are 100% rejected for team props; ambiguous labels return `None` rather than misattributing; alien teams and missing contexts safely produce 0 rows. `test_team_props_*` suite passed completely.

5. **Graceful Degradation & Error Resilience**:
   - *Observation*: Handlers use `_as_float`, `_as_int`, type guards, and dictionary lookups.
   - *Stress Test*: Passed empty structures (`[]`, `[{}]`), missing keys, non-list outcomes, corrupted odds lists with non-dicts, missing books, invalid floats ("forty-five", NaN, None), and malformed insights payloads.
   - *Result*: The parser never raised uncaught exceptions or crashed; malformed items were safely skipped while valid sibling entries were preserved. `test_degradation_*` suite passed completely.

---

## 3. Caveats

- Live HTTP network endpoints (`OutlierClient.http.get_json`) were tested using fixtures and replay mocks rather than hitting live third-party network APIs, per benchmark mode and repository isolation invariants (ORIGINAL_REQUEST R6).
- If a future upstream Outlier change introduces an entirely new schema where `odds` is renamed or transformed from a list of dicts to a different structure, `parse_odds_rows` will return empty lists (graceful degradation) rather than crashing.

---

## 4. Conclusion & Explicit Verdict

**Verdict: APPROVE**

The Outlier parser implementation in `cfb_analytics/sources/outlier.py` is empirically robust, impervious to Feed Trap 1 (book misattribution / outcome.books leakage), handles Feed Trap 2 (multi-card proposition spanning and deduplication) correctly, strictly enforces the whitelist and side restrictions, resolves team attribution without ambiguity leakage, and degrades gracefully under corrupted or empty payloads.

---

## 5. Verification Method

To independently reproduce and verify these findings:

1. Navigate to the worktree:
   ```powershell
   cd C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights
   ```
2. Run the adversarial stress test suite:
   ```powershell
   pytest tests/test_outlier_adversarial.py -v
   ```
3. Run the full outlier test suite with coverage:
   ```powershell
   pytest tests/test_outlier_parsing.py tests/test_outlier_props.py tests/test_outlier_adversarial.py --cov=cfb_analytics.sources.outlier --cov-report=term-missing
   ```
4. Run static analysis and type checks:
   ```powershell
   ruff check tests/test_outlier_adversarial.py
   mypy cfb_analytics
   pyright cfb_analytics
   ```
5. Invalidation condition: Any failure in `test_outlier_adversarial.py`, any leakage of `outcome.books`, any acceptance of unwhitelisted markets/sides, or any crash on malformed payloads.
