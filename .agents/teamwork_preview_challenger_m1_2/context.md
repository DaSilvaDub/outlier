# Milestone 1 Challenger 2 Assignment

## Identity & Role
You are Challenger 2 (`teamwork_preview_challenger`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_challenger_m1_2`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Worker Handoff: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1\handoff.md`

## Mission & Scope
Empirically stress-test schema validation gates, atomic file operations, and domain models:
- Stress-test `outlier_nfl.schema` validation gates against truncated, missing-field, or type-violated payloads.
- Test `safe_write_json` and `_replace_with_retry` concurrently or against locked files to confirm Windows `[WinError 32]` resilience.
- Verify `NflGameLine` and `NflPlayerProp` dataclasses enforce immutability (`frozen=True`) and handle null/empty odds safely.
- Deliver findings and an explicit verdict (`APPROVE` or `REQUEST_CHANGES`) in `handoff.md`.
