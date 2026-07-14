## Handoff — `claude/sgp-parlay-alt-totals-vcelz5` (PR #39)

**Last Product Commit SHA:** `2af86ec` (`feat: alt team-total L10 board with SGP/parlay suggestions (WNBA + MLB)`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/39

**Files Touched:**
- `outlier_scrapers/alt_team_totals.py` (new) — board of team-total OVER alt
  lines with L10 hit rate in the 90-100% band (from outcome `stats`
  `l10Results`/`l10`), best-line stamping, 2-leg parlay suggestions with SGP
  flagging, standalone CLI writing to `data/<LG>/reports/`.
- `outlier_scrapers/pack.py` — `write_pack` now emits `alt_team_totals.csv`,
  `alt_team_total_parlays.csv`, `sections/alt_team_totals.md`.
- `tests/test_alt_team_totals.py` (new, 14 tests), `tests/test_pack.py`
  (asserts the new pack files exist).

**Verification:** 303 offline tests pass (pytest not installable in the remote
sandbox — egress blocked; used a local shim runner, run real pytest locally).
Drove both league CLIs and `pack --no-feedback-ledger` end-to-end against a
staged games feed; probed missing/malformed feed, bad league, started events.

**Known issue found (pre-existing, NOT from this PR):** `pack.main` without
`--no-feedback-ledger` fails in `feedback._probability` — `game_totals.py`
writes `implied_prob` as 0-100 while the feedback ledger validates 0-1
(`implied_prob must be between 0 and 1, got 85.401`). Needs a follow-up fix.

**Next steps:** confirm MLB team-total proposition token (POINTS vs RUNS) on a
live slate; optional follow-ups: cross-league parlays, 3-leg combos.

---

## Handoff — `feat/feedback-loop-calibration` (PR #35)

**Last Product Commit SHA:** `89db688` (`fix: preserve decision export deduplication`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/35

**Files Touched:**
- `.gitignore` — ignores local calibration databases, exports, and reports.
- `outlier_scrapers/feedback.py` — permanent SQLite ledgers, CSV import/export,
  settlement grading, calibration/performance reporting, history freezes, and
  the schema-v3 totals-probability migration. PR review follow-up added atomic
  structural upgrades, deterministic duplicate-decision resolution, fail-closed
  identity validation, zero-safe numeric fallback, correct fade grading, and
  explicit trusted SQL statements that satisfy the Codacy security boundary.
- `outlier_scrapers/pack.py` — full pre-ranking opportunity capture and atomic
  pack/ledger publication, with one normalized opportunity-identity helper.
- `outlier_scrapers/game_totals.py` — explicit outcome/probability/book/stake
  fields and push-aware unconditional win-probability semantics.
- `docs/feedback-loop.md`, `AI-research-desk-runbook.md` — operator workflow,
  schemas, probability definitions, and current external-data boundary.
- `tests/test_feedback.py`, `tests/test_game_totals.py`, `tests/test_pack.py` —
  offline regression and integration coverage, including duplicate opportunity
  rows producing only one persisted/exported decision.
- `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`,
  `.claude/PRPs/reviews/pr-35-review.md` — user-authored desk2 prompt export and
  review notes committed concurrently in `e144df6`.

**Verification:**
- 129 focused feedback/game-total/pack tests passed.
- 57 downstream offline daily-job/runner/desk2/sizing tests passed.
- Ruff, MyPy, Pyright, and `git diff --check` passed.
- Independent reviewer reported no remaining correctness blockers.
- No live or paid reasoning providers were invoked.

**Next Steps:**
- Review and merge PR #35.
- Connect an authoritative closing-line/results feed to the documented
  settlement CSV boundary when provider selection is approved.
- Supply a real independent probability model and learn Board B weights from
  the accumulated outcomes; those are intentionally not fabricated here.
