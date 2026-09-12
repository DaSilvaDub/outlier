# Milestone 1 Reviewer 1 Assignment

## Identity & Role
You are Reviewer 1 (`teamwork_preview_reviewer`).
Working directory: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_reviewer_m1_1`
Parent: `teamwork_preview_orchestrator_2` (Conversation ID: `d2a2c301-d51b-4f3f-9bab-93bbc7ce5295`)

## Mandatory Required Reading
1. `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (MUST read first)
2. `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_orchestrator_2\PROJECT.md`
3. Worker Handoff: `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_worker_m1\handoff.md`

## Scope of Review
Review the Milestone 1 implementation of `outlier_nfl` (F1–F7):
- `outlier_nfl/__init__.py`
- `outlier_nfl/constants.py`
- `outlier_nfl/config.py`
- `outlier_nfl/models.py`
- `outlier_nfl/schema.py`
- `outlier_nfl/api.py`
- `outlier_nfl/utils.py`
- `tests/test_nfl_api.py`

## Review Criteria
1. Correctness: Verify adherence to NFL requirements, API endpoints (`/sportsdata/leagues/NFL/...`), and 32-team canonical mapping.
2. Decoupling: Verify ZERO imports from `outlier_scrapers`.
3. Robustness: Verify retry policy, exponential backoff, JSON `strict=False`, memory efficiency (`json.dump`), and Windows file replace retries.
4. Testing: Run `pytest tests/test_nfl_api.py`, `python -m mypy outlier_nfl`, and `ruff check outlier_nfl`.
5. Verdict: State explicit verdict `APPROVE` or `REQUEST_CHANGES` in `handoff.md`.
