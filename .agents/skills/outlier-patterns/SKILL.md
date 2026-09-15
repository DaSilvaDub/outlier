---
name: outlier-patterns
description: Use when writing, reviewing, committing, or splitting modules in the outlier repo (pack, daily_job, tests, HANDOFF.md, conventional commits, PRs).
version: 1.0.0
source: local-git-analysis
analyzed_commits: 200
---

# Outlier Patterns

Git-extracted team practices from the last 200 commits (2026-08-13 → 2026-09-06; 633 total). Do not copy house rules from AGENTS.md, MLB whitelist, or STEP 0 here.

**REQUIRED BACKGROUND:** python-patterns, python-testing. Sync/reasoning/desk rules stay in AGENTS.md.

## Commit Conventions

75% of recent commits are conventional (`type(scope): summary`). Dominant types: `fix` (52), `docs` (38), `feat` (33), `chore` (21).

| Type | Use |
|---|---|
| `feat` | New pipeline capability |
| `fix` | Bug or gate correction |
| `docs` | Plans, skill atlas, handoffs |
| `chore` | HANDOFF.md / session record |
| `test` | Test-only |
| `ci` | pytest/mypy/pyright jobs |

Scopes seen often: `handoff`, `verdicts`, `projections`, `pack`, `pipeline`, `storage`, `feed-health`.

- Land code via feature branch + PR; include the GitHub number (`fix(pack): … (#148)`).
- Direct `master` is for coordination files: `AGENTS.md`, `CLAUDE.md`, `.agent-log/HANDOFF.md`.
- `Auto-snapshot YYYY-MM-DD` is generated; do not mimic it for human commits.

## Code Architecture

```
outlier_scrapers/   # CLI package (python -m outlier_scrapers.<mod>)
tests/test_<mod>.py
docs/plans/         # dated design-of-record
.agent-log/HANDOFF.md
prompts/            # A–E and Desk2 Q/R/W/X/S
```

Hot files (change counts in the window): `pack.py` 27, `test_pack.py` 22, `projections.py` 17, `runner_common.py` 15, `daily_job.py` 11, `feedback.py` 9, `verdict_gate.py` 8.

Large modules get split, then the original file stays a thin facade:

- `pack.py` → `pack_context`, `pack_index`, `pack_manifest`, `pack_market`, `pack_projections`, `pack_publish`, `pack_ranking`, `pack_render`, `pack_selection`, `pack_sizing`
- `feedback.py` → `feedback_capture`, `feedback_db`, `feedback_recovery`, `feedback_reporting`, `feedback_retention`, `feedback_settlement`
- `daily_job.py` → `refresh_plan`, `run_state`

Re-export public names from the facade. Reviewers flag missing re-exports.

Empty-slate / hiatus leagues: feed-health `check_freshness` **and** pack-build gates both treat a confirmed no-games slate as non-fatal (`#146`, `#147`). Do not add a third copy of that rule.

## Workflows

### Change a pack/daily/projection behavior
1. Edit `outlier_scrapers/<mod>.py` (or the focused `pack_*` / `feedback_*` sibling).
2. Update `tests/test_<mod>.py` in the same commit (72 of 90 source commits did this).
3. Keep the facade importable: `from outlier_scrapers.pack import …` still works.
4. Open a PR; record the result in `.agent-log/HANDOFF.md`.

### Split a file over the maintainability ceiling
1. Extract focused modules next to the original.
2. Re-export from the original so callers do not change.
3. Add or extend `tests/test_<original>.py` plus a small `tests/test_<new>.py` when the new module has its own contract (`test_pack_index.py`, `test_pack_manifest.py`, `test_feedback_decomposition.py`).

### Multi-ent doc sync
`AGENTS.md` and `docs/ENT-SYNC-GLOBAL-PROMPT.md` change together. Use `scripts/sync_agent_docs.py` / `report-sync.ps1`, not a one-off copy.

### Daily pipeline (data only)
`python -m outlier_scrapers.daily_job --leagues MLB,WNBA --analysis-profile local` then `python scripts/organize_today_run2.py`. Paid A–E: AGENTS.md.

## Testing Patterns

- Location: `tests/test_*.py` (91 files). pytest from repo root; `testpaths = ["tests"]`.
- Mirror name: `outlier_scrapers/projections.py` ↔ `tests/test_projections.py`.
- Markers in `pyproject.toml`: `uses_enforce_mode`, `provider`. Live desk tests: AGENTS.md.
- Coverage target on changed `outlier_scrapers` modules: 80%+.
- Verify with `pytest tests/test_<mod>.py -q` then `python -m ruff check`.

## Common Mistakes

- Editing a `pack_*` helper without updating the `pack.py` facade.
- Fixing empty-slate handling in only one of feed-health vs pack-build.
- Committing pipeline behavior on `master` without a PR.
- Restating AGENTS.md house rules inside this skill.
