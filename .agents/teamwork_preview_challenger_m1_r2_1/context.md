# Milestone 1 Iteration 2 Challenger 1 Assignment

## Identity & Role
You are Challenger 1 (`teamwork_preview_challenger`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_challenger_m1_r2_1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Worker 2 Handoff: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1_r2\handoff.md`

## Mission
Adversarially verify that the previous vulnerabilities in `outlier_nfl/api.py` and `outlier_nfl/config.py` are resolved:
1. Gzip EOFError and zlib.error stream error recovery and retry.
2. JSONDecodeError retry on transient HTML/gateway timeouts.
3. 32-team composite code+nickname normalization (e.g. `KC Chiefs`, `SF 49ers`, `TB Bucs`, `NY Giants`, `NY Jets`).
4. Run `pytest tests/test_nfl_stress.py -v`.
5. Deliver `handoff.md` with explicit verdict `APPROVE` or `REQUEST_CHANGES`.
