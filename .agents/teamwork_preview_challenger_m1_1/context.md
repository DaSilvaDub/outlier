# Milestone 1 Challenger 1 Assignment

## Identity & Role
You are Challenger 1 (`teamwork_preview_challenger`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_challenger_m1_1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Worker Handoff: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1\handoff.md`

## Mission & Scope
Empirically challenge the Milestone 1 codebase (`outlier_nfl`):
- Stress-test `outlier_nfl.api.OutlierNflApiClient` with adversarial/mocked network conditions (rapid 429 bursts, 502/503 errors, corrupted gzip bytes, malformed JSON with unescaped control characters).
- Stress-test `normalize_team` across all 32 NFL franchises with extreme casing, punctuation, and nicknames.
- Verify that pagination halts gracefully on repeated page signatures, empty items, or token cycles without infinite looping.
- Deliver findings and an explicit verdict (`APPROVE` or `REQUEST_CHANGES`) in `handoff.md`.
