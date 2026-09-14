# BRIEFING — 2026-09-12T10:39:21Z

## Mission
Sentinel oversight for Standalone NFL betting data pipeline (outlier_nfl) in outlier repo: dispatching orchestrator, monitoring progress via crons, and enforcing independent victory audit.

## 🔒 My Identity
- Archetype: sentinel
- Working directory: C:\Users\dasil\Dev\GitHub\outlier\.agents\sentinel
- Orchestrator: 541db224-5e2a-46d8-a757-71c82995ea68
- Victory Auditor: adcc728c-0f38-4dd4-a2f2-60aaabfbd484
- Active Orchestrator: d2a2c301-d51b-4f3f-9bab-93bbc7ce5295
- Active Victory Auditor: [to be spawned on victory claim]

## 🔒 Key Constraints
- No technical decisions — relay only
- Victory Audit is MANDATORY before reporting completion
- Must not write code, analyze problems, or make any technical decisions
- Strict execution path routing via Routing Decision Table (General -> teamwork_preview_orchestrator)
- DO NOT TOUCH THE ELO PROJECT CODES in cfb-analytics
- No external AI/paid reasoning (benchmark integrity mode)
- Use separate git worktree from C:\Users\dasil\Dev\GitHub\cfb-analytics and branch feat/outlier-props-insights
- Outlier multi-ent sync is mandatory (STEP 0 report-sync)
- House rule: never run reasoning models unless explicitly asked
- Standalone NFL pipeline must leave WNBA/MLB pipeline intact

## User Context
- **Last user request**: Standalone NFL betting data pipeline (`outlier_nfl`) in outlier repo covering NFL player props & team props (game totals, team totals, spreads), unit tests via pytest, standalone verification script `verify_nfl_pipeline.py`.
- **Pending clarifications**: [none]
- **Delivered results**: [none for current request]

## Project Status
- **Phase**: in progress
- **Route**: General (`teamwork_preview_orchestrator`)
- **Cron 1 (Progress Reporting)**: task-20 (active, */8 * * * *)
- **Cron 2 (Liveness Check)**: task-22 (active, */10 * * * *)
- **Subagents**: orchestrator d2a2c301-d51b-4f3f-9bab-93bbc7ce5295 (running)

## Victory Audit Status
- **Triggered**: no
- **Verdict**: pending
- **Retry count**: 0

## Artifact Index
- C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md — Verbatim user request record
- C:\Users\dasil\Dev\GitHub\outlier\.agents\sentinel\BRIEFING.md — Sentinel persistent memory
