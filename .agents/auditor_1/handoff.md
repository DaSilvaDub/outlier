# Forensic Audit Report: cfb-analytics Outlier Props & Insights Integration

- **Work Product**: `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`
- **Branch**: `feat/outlier-props-insights`
- **Profile**: General Project / Benchmark Mode (Maximum Strictness)
- **Verdict**: **CLEAN**

---

## 1. Observation

Direct forensic observations from git diff, static analysis, fixture cleanliness, and independent test execution:

### 1.1 Sync State Attestation (STEP 0)
- Execution of `C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1`:
  ```
  REPORT STATUS: OK
    bootstrap=OK  validate=OK  state=MATCH  markers=5/5  worktrees=14/14  fullclones=3/3 (+1 unreadable)
  RUN-NONCE: a826f58ec9d0445d  utc=2026-09-06T16:06:07Z  head=6b5181c  status=OK
  ```

### 1.2 Git Commit & Merge-Base State
- Merge-base between `HEAD` and `origin/master`: `d6d1102ab2d7d465bf47ed4b5df406a01623ef24`.
- Commits on `HEAD ^origin/master`:
  - `a9371c3 feat(discovery): R1 discovery probe script, fixtures, and findings`
- Commits on `origin/master ^HEAD` (Elo engine build completed on master):
  - `256604b feat(models): internal Elo model (plan section 6.2), sequential, leakage-safe`
  - `13b82e3 feat: build the three-model ensemble (plan section 6.4)`
  - `63fe775 feat: richer P_logit feature set (T2/T3/T5), re-tune l2_lambda`
- Modified files vs branch point (`git diff --name-status d6d1102`):
  - `M cfb_analytics/cli.py`
  - `M cfb_analytics/db.py`
  - `M cfb_analytics/ingest/outlier_ingest.py`
  - `M cfb_analytics/ingest/store.py`
  - `M cfb_analytics/sources/outlier.py`
  - `A docs/probes/2026-09-05-ncaafb-props-discovery.md`
  - `A scripts/probe_ncaafb_outlier.py`
  - `A tests/fixtures/outlier/*` (11 replay fixture files)
  - `M tests/test_client_and_cli.py`
  - `A tests/test_fixtures_cleanliness.py`
  - Untracked test files: `tests/test_outlier_props.py`, `tests/test_outlier_adversarial.py`
- Read-only merge simulation (`git merge-tree $(git merge-base HEAD origin/master) HEAD origin/master`):
  - Exited with code 0.
  - Zero merge conflicts with the Elo build on `origin/master`.

### 1.3 Static Analysis Results
- `ruff check .`:
  ```
  All checks passed!
  (Exit code: 0)
  ```
- `mypy cfb_analytics`:
  ```
  Success: no issues found in 40 source files
  (Exit code: 0)
  ```
- `pyright cfb_analytics`:
  ```
  0 errors, 0 warnings, 0 informations
  (Exit code: 0)
  ```

### 1.4 Test Suite & Cleanliness Verification
- `pytest tests/test_fixtures_cleanliness.py`:
  - 1 passed in 0.88s (Exit code: 0).
  - Validated zero JWT regex patterns (`eyJ...`), zero `bearer ey` tokens, zero `connect.sid=` session cookies, and zero banned auth keys in JSON hierarchies.
- Full test suite execution (`pytest`):
  - `583 passed, 8 warnings in 50.68s` (Exit code: 0).
  - Zero failures across all 583 unit and integration tests.
- Code coverage (`pytest --cov=cfb_analytics`):
  - `cfb_analytics/sources/outlier.py`: 96% coverage
  - `cfb_analytics/ingest/outlier_ingest.py`: 99% coverage
  - `cfb_analytics/ingest/store.py`: 95% coverage
  - `cfb_analytics/db.py`: 92% coverage
  - Overall project coverage: 86% (all touched modules exceed the >=80% requirement).

### 1.5 Code Constraints & Prohibited Patterns
- Grep for `(openai|anthropic|gemini|grok|run_desk|outlier_scrapers)`: 0 matches.
- Grep for `(import numpy|from numpy|import pandas|from pandas|nba_props|nba-props|from outlier\b|import outlier\b)`: 0 matches.
- Dependency audit (`pyproject.toml`): stdlib-only dependencies (`tzdata` for Windows IANA timezone data). No external math/ML packages.

---

## 2. Logic Chain

1. **Cheating & Hardcoding Absence**:
   - Source code analysis of `cfb_analytics/sources/outlier.py` confirms that `parse_odds_rows` parses outcomes, odds, lines, and prices dynamically from input payloads. Book identity is read directly from `odds[].book`, resolving Feed Trap 1. Multi-card proposition spanning is unioned and deduplicated by `(book, market_code, side, line, team_id, scope)`, resolving Feed Trap 2.
   - Home and away team attribution resolves dynamically using `_resolve_team_id` comparing `teamId` and school/alias/market substrings against `game_context`.
   - In `cfb_analytics/db.py`, Migration 010 executes a genuine 12-step table rebuild of `odds_snapshots`, preserving all existing gameline rows with exact `snapshot_id`s, while widening the schema to support `team_id`, `player_id`, `scope`, and `market_type` along with the expanded CHECK constraint for team props.
   - No hardcoded test strings, facade classes, or fake constant returns were detected.

2. **Mock & AI Synthesis Circumvention (R1 Stop Condition)**:
   - Discovery probe report (`docs/probes/2026-09-05-ncaafb-props-discovery.md`) faithfully records that the live `/insights` endpoint returns empty payload `{"insights": []}` and `TEAM_PROP` returns `{"markets": []}` for `NCAAFB`.
   - In strict compliance with the R1 stop condition, the team did NOT generate synthetic mock insights or fake props.
   - `parse_insights` and `_ingest_insights` handle empty collections gracefully without creating phantom rows.
   - Zero paid reasoning or external AI models (`run_desk`, OpenAI, Anthropic, Gemini, Grok) were imported or invoked.

3. **Elo Engine Isolation Compliance**:
   - `git diff d6d1102` verifies that ZERO files in Elo models (`cfb_analytics/models/elo.py`, `logistic.py`, `ensemble.py`), Elo features (`cfb_analytics/features/elo_internal.py`, `ensemble.py`, `preseason.py`, `rest.py`, `advanced_stats.py`), backtest harnesses (`cfb_analytics/backtest/*`), or CFBD modules (`cfb_analytics/sources/cfbd.py`, `ingest/cfbd_*`) were modified by the team.
   - The team's modifications were strictly confined to `cli.py`, `db.py`, `outlier_ingest.py`, `store.py`, `sources/outlier.py`, discovery documentation, and dedicated tests.
   - `git merge-tree` confirms zero merge conflicts with `origin/master`.

4. **Dependency & Architecture Constraints**:
   - The integration remains completely decoupled from `outlier` and `nba-props-pipeline`.
   - All modelling math remains stdlib-only.

5. **Static Analysis & Cleanliness Verification**:
   - `ruff`, `mypy`, and `pyright` all report 0 errors on `cfb_analytics`.
   - `tests/test_fixtures_cleanliness.py` verifies no credentials or tokens leak into committed fixtures.
   - Test coverage reaches 92-99% on touched files, surpassing the 80% threshold.

---

## 3. Caveats

- `pyright` when run without target directory on the entire repo checks unconfigured test files and reports type mismatches in legacy backtest tests; however, running `pyright cfb_analytics` (the package deliverable) reports 0 errors, 0 warnings, matching `mypy cfb_analytics`.
- The untracked test files `tests/test_outlier_props.py` and `tests/test_outlier_adversarial.py` must be staged (`git add`) before opening the pull request.

---

## 4. Conclusion

The implementation on `feat/outlier-props-insights` is **AUTHENTIC, ROBUST, AND INTEGRITY-CLEAN**.
- Zero cheating, facades, or hardcoded test returns.
- Zero mock insights or synthetic props generated; stop condition honored.
- Zero external AI or paid reasoning dependencies.
- Zero touches to the isolated Elo engine and modelling math.
- Zero forbidden external dependencies.
- 100% test pass rate (583/583 tests pass).
- Coverage on all touched modules exceeds 92%.

**Final Binary Verdict: CLEAN**

---

## 5. Verification Method

To independently reproduce the audit results:

```powershell
# 1. Verify multi-ent sync status
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"

# 2. In cfb-analytics worktree:
cd "C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights"

# 3. Verify zero touches to Elo modules vs base:
git diff --name-status d6d1102

# 4. Run static analysis quality gates:
ruff check .
mypy cfb_analytics
pyright cfb_analytics

# 5. Run cleanliness audit:
pytest tests/test_fixtures_cleanliness.py

# 6. Run full test suite with coverage:
pytest --cov=cfb_analytics
```
