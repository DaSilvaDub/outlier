# PR Review: #35 — feat: close feedback loop with calibration ledgers

**Reviewed**: 2026-07-13
**Author**: DaSilvaDub
**Branch**: feat/feedback-loop-calibration → master
**Decision**: REQUEST CHANGES (one blocking runtime regression)

## Summary
Strong, well-architected feature that adds a permanent SQLite feedback ledger,
settlement grading, and a full calibration report suite. The design and test
coverage are excellent. However, the current HEAD (8d08329) is broken by a
`NameError` (`_coalesce` undefined) introduced *after* the PR's recorded
verification, which fails all capture and, by default, the whole pack build.

## Findings

### CRITICAL
- **`NameError: name '_coalesce' is not defined` at `outlier_scrapers/feedback.py:618`.**
  The last edit changed `row.get("edge") or row.get("edge_pct")` to
  `_coalesce(row.get("edge"), row.get("edge_pct"))`, but `_coalesce` is only
  defined in `pack.py:270` and is never imported into `feedback.py`.
  - `_snapshot_from_pack_row` builds this key in every returned dict, so
    `capture_pack` raises on **every** row.
  - Because `pack.main` now captures by default, `python -m outlier_scrapers.pack`
    (no `--no-feedback-ledger`) hits the exception → transaction rolls back →
    **the default pack build fails entirely.**
  - Confirmed: 13/13 feedback tests that reach capture fail; `ruff --select F821`
    flags exactly this line. The PR body's "118 tests passed" predates commits
    2fa8323 / 8d08329.
  - Fix: define a local `_coalesce` in `feedback.py` (preferred — the module is
    otherwise self-contained, importing only `paths`) or import it from `pack.py`.
    Note the intent was correct: `a or b` mishandles a legitimate `0.0`/`""` edge,
    so keep the coalesce semantics.

### HIGH
- None (once the NameError is fixed, the remaining logic and its tests are sound).

### MEDIUM
- **Verification claim is stale.** The "Ruff, MyPy, Pyright, 118 tests passed"
  section must be re-run at HEAD and the body updated; the breaking commits landed
  after it. Recommend `ruff check` include F821 (undefined names) in CI so this
  class of post-verification edit can't merge green.

### LOW
- `compute_clv_line` uses `taken - closing` for the spread branch regardless of
  sign; correct for the documented convention but worth a test on a laid favorite
  (e.g. -2.5 taken vs -3 close) to lock the sign.
- `initialize_database` opens a second short-lived connection per call via the
  `with sqlite3.connect(...)` block; harmless under CPython refcounting but a minor
  redundancy given `_connect` immediately opens another.

## Validation Results

| Check | Result |
|---|---|
| Tests (feedback/pack/game_totals) | **Fail** — 13 failed, 105 passed (all failures = the `_coalesce` NameError) |
| Lint (ruff F821) | **Fail** — 1 undefined name |
| Type check (mypy/pyright) | Skipped |
| Build | N/A (Python) |

## What the PR does well
- Clean transactional publish: pack staged, DB written in one `BEGIN IMMEDIATE`
  transaction, filesystem swapped, then committed; failure rolls back the ledger
  and restores the prior published pack. Covered by dedicated tests.
- Honest probability semantics: stores unconditional `P(win)`, recovers
  `P(win | not push)` for binary scoring, excludes unknown-push rows rather than
  assuming no push, and keeps `independent_model_prob` intentionally blank.
- Deterministic, auditable schema-v3 migration (conditional → unconditional),
  guarded to run once and before the finalized/settled freeze.
- Freeze semantics prevent rewriting decision/prediction history after outcomes
  are known; strong input validation and clear `FeedbackError` messages throughout.
- Thorough docs (`docs/feedback-loop.md`) and 566 lines of focused tests.

## Files Reviewed
- outlier_scrapers/feedback.py — Added (1592 lines)
- outlier_scrapers/game_totals.py — Modified (push-aware sizing, best-book, prob columns)
- outlier_scrapers/pack.py — Modified (opportunities.csv, transactional capture)
- docs/feedback-loop.md — Added
- tests/test_feedback.py, tests/test_game_totals.py, tests/test_pack.py — Added/Modified
- .agent-log/HANDOFF.md, .gitignore, AI-research-desk-runbook.md — docs/config
