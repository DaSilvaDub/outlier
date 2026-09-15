## 2026-09-06T15:27:10Z

You are spec_miner_survey_3.
Your working directory is: C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3
The authoritative original user request is recorded at: C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md
Target worktree repository path: C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights
Current branch: feat/outlier-props-insights

MANDATORY FIRST STEP: Read C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md before doing any work.

Objective:
Mine and document the exact requirements, constraints, database migration specs, parsing rules, and acceptance criteria.

Scope Boundaries:
- Read-only exploration! DO NOT edit, modify, or write source code or test files.
- DO NOT run any live reasoning or paid AI models.
- Write your findings into C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md and handoff.md.

Investigation Tasks:
1. Analyze R2: Target Markets, Whitelist, and Side Restrictions.
   - Define exact module-level frozensets for market_type, market, scope, allowed sides.
   - Clarify side restrictions (e.g. SPREAD: HOME/AWAY; TOTAL: OVER/UNDER; ML: HOME/AWAY; TEAM_PROP: OVER/UNDER).
   - What markets are explicitly out of scope?
2. Analyze R3: Schema & Integrity.
   - Current migration version vs next available (Migration 11 or other?).
   - Table-rebuild migration strategy for odds_snapshots / market_consensus vs separate tables.
   - Deterministic snapshot_id generation (what fields are hashed/combined, ensuring existing snapshot_ids do not change).
3. Analyze R4: Parsing traps.
   - Trap 1: outcomes[].books is NOT parallel to outcomes[].odds. Book must be read from inside each odds entry.
   - Trap 2: One proposition spans several market rows per event. Consensus must union all rows and deduplicate.
4. Analyze R5: Ingestion & Reporting.
   - ingest_slate flags: with_props, with_insights (defaults).
   - Graceful degradation: failure isolation per event, source_health, IngestSummary fields.
   - Minimum books floor: min_books_for_consensus = 3 for prop consensus.
5. Analyze Acceptance Criteria & Quality Gates:
   - TDD approach with CFB_HTTP_MODE=replay.
   - ruff check ., mypy cfb_analytics, pyright.
   - pytest --cov=cfb_analytics >=80% coverage on touched modules.
   - CLI command ingest --date <slate> --with-props summary output format.

Completion Criteria:
Write a comprehensive specification document to C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md and C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\handoff.md. When finished, send a completion message to parent.
