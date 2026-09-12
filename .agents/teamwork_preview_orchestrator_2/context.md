# Orchestrator Task Assignment

## Mission
You are the Project Orchestrator (`teamwork_preview_orchestrator`). Your working directory is `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2`.
Your objective is to lead and execute the user's project request as recorded in `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md`:

Build a standalone NFL betting data pipeline (`outlier_nfl`), derived from the existing Outlier pipeline. It will cover NFL player props and team props (game totals, team totals, spreads) while leaving the existing WNBA/MLB pipeline intact.

## Constraints & Requirements
- Read `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (latest section `## 2026-09-12T10:39:21Z`).
- Working directory: `C:\Users\dasil\Dev\GitHub\outlier`
- Integrity mode: development
- R1. Standalone NFL Pipeline Module: Create `outlier_nfl` within existing `outlier` repo. Separate from WNBA and MLB code paths.
- R2. NFL Data Sourcing: Reuse Outlier API integration patterns, hitting NFL-specific endpoints.
- R3. Market Coverage: NFL player props and team props (game totals, team totals, spreads).
- Acceptance Criteria:
  - Unit tests via `pytest` passing without errors.
  - Verification script `verify_nfl_pipeline.py` running extraction end-to-end and asserting expected markets.
- House Rules:
  - NEVER run reasoning models unless explicitly asked this turn.
  - Multi-agent sync protocol: Always use git branch workflow for feature work.
- Maintain `progress.md` and `BRIEFING.md` in your working directory.
- When done, report victory to the Sentinel. Victory will be independently verified by an auditor before completion can be reported to the user.
