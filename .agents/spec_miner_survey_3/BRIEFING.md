# BRIEFING — 2026-09-06T15:37:35Z

## Mission
Mine and document exact requirements, constraints, database migration specs, parsing rules, and acceptance criteria for outlier props and insights integration in cfb-analytics (feat/outlier-props-insights).

## 🔒 My Identity
- Archetype: Specification Miner
- Roles: Specification Miner, Teamwork specialist
- Working directory: C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3
- Original parent: 541db224-5e2a-46d8-a757-71c82995ea68
- Milestone: Survey & Specification (Survey 3)

## 🔒 Key Constraints
- Read-only exploration! DO NOT edit, modify, or write source code or test files in target repository.
- DO NOT run any live reasoning or paid AI models.
- Write findings into C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md and handoff.md.
- Send completion message to parent via send_message.

## Current Parent
- Conversation ID: 541db224-5e2a-46d8-a757-71c82995ea68
- Updated: 2026-09-06T15:37:35Z

## Task Summary
- **What to build**: Specification document covering R2, R3, R4, R5, Acceptance Criteria & Quality Gates for Outlier Props & Insights in CFB Analytics.
- **Success criteria**: Comprehensive analysis.md and handoff.md with verified code-level facts from cfb-analytics worktree and ORIGINAL_REQUEST.md.
- **Interface contracts**: C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md
- **Code layout**: Target worktree: C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights (feat/outlier-props-insights).

## Key Decisions Made
- Multi-ent sync completed (status OK, nonce 681be614a2d74aa2).
- Migration 9 is the latest migration on both `origin/master` and `HEAD`; next available migration is **Migration 10** (`MIGRATION_010`).
- Migration 10 must execute a 12-step table rebuild for `odds_snapshots` to widen the CHECK constraint to include admitted props.
- Dedicated `prop_consensus` table created rather than overloading `market_consensus`, avoiding primary key collisions on `(game_id, market, line, side, as_of_utc)` between Home and Away team props.
- Deterministic `snapshot_id` generation preserves the 8 positional arguments for existing gamelines, while appending `team_id` for team props.
- Whitelist defined as module-level frozensets (`ALLOWED_MARKET_TYPES`, `ALLOWED_MARKETS`, `ALLOWED_SCOPES`, `ALLOWED_SIDES_BY_MARKET`).
- Ingestion defaults `with_props=False` and `with_insights=False`, isolates event failures, logs to `source_health`, and requires `min_books_for_consensus = 3`.
- Baseline verification: 491 unit tests pass in 34s; `ruff check .` and `mypy cfb_analytics` pass cleanly with 0 errors.

## Artifact Index
- C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\DISPATCH.md — Dispatch instructions
- C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\BRIEFING.md — Persistent memory
- C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\progress.md — Liveness heartbeat (COMPLETE)
- C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\analysis.md — Authoritative specification document
- C:\Users\dasil\Dev\GitHub\outlier\.agents\spec_miner_survey_3\handoff.md — 5-component handoff report
