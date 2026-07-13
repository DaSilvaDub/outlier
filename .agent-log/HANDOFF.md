## Handoff — `feat/feedback-loop-calibration` (PR #35)

**Last Product Commit SHA:** `02b5655989b7be99544f3abd369d43f2adcce7c9`

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/35

**Files Touched:**
- `.gitignore` — ignores local calibration databases, exports, and reports.
- `outlier_scrapers/feedback.py` — permanent SQLite ledgers, CSV import/export,
  settlement grading, calibration/performance reporting, history freezes, and
  the schema-v3 totals-probability migration.
- `outlier_scrapers/pack.py` — full pre-ranking opportunity capture and atomic
  pack/ledger publication.
- `outlier_scrapers/game_totals.py` — explicit outcome/probability/book/stake
  fields and push-aware unconditional win-probability semantics.
- `docs/feedback-loop.md`, `AI-research-desk-runbook.md` — operator workflow,
  schemas, probability definitions, and current external-data boundary.
- `tests/test_feedback.py`, `tests/test_game_totals.py`, `tests/test_pack.py` —
  offline regression and integration coverage.

**Verification:**
- 118 focused feedback/game-total/pack tests passed.
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
- Preserve the unrelated pre-existing local modification in
  `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`.
