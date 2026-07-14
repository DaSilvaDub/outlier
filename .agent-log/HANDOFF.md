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
