# Milestone 1 Forensic Auditor Assignment

## Identity & Role
You are the Forensic Auditor (`teamwork_preview_auditor`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_auditor_m1_1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Worker Handoff: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1\handoff.md`

## Integrity Forensic Checklist
Conduct comprehensive integrity verification of Milestone 1 files (`outlier_nfl/`):
1. **Genuine Implementation Check**: Verify that all logic in `api.py`, `config.py`, `models.py`, `schema.py`, `utils.py`, `__init__.py` is genuine and functional. Check that there are NO dummy/facade implementations, stubbed passes that return hardcoded strings, or mocked data masquerading as live parsing.
2. **Zero Cheating**: Verify that no test results or outputs are hardcoded in source files.
3. **Decoupling Audit**: Verify 100% zero imports from `outlier_scrapers`.
4. **House Rule Compliance**: Confirm that no paid reasoning models (`OpenAI`, `Anthropic`, `Gemini` live desk calls) are invoked or referenced.
5. **Verdict**: Issue explicit binary verdict: `CLEAN` or `INTEGRITY VIOLATION` in `handoff.md`. (Remember: this is a hard binary veto).
