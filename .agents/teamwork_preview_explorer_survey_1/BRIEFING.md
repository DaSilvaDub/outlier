# BRIEFING — 2026-09-12T10:51:00Z

## Mission
Investigate outlier_scrapers architecture and design standalone outlier_nfl module blueprint for NFL player and team props.

## 🔒 My Identity
- Archetype: teamwork_preview_explorer
- Roles: explorer, survey
- Working directory: C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1
- Original parent: d2a2c301-d51b-4f3f-9bab-93bbc7ce5295
- Milestone: survey & architecture design for outlier_nfl

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Standalone outlier_nfl package with zero runtime coupling to MLB/WNBA
- Reuse Outlier API integration patterns (session, auth, rate limiting, retry)
- Target NFL player props and team props (game totals, team totals, spreads)
- Deliver report to C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md

## Current Parent
- Conversation ID: d2a2c301-d51b-4f3f-9bab-93bbc7ce5295
- Updated: 2026-09-12T10:51:00Z

## Investigation State
- **Explored paths**:
  - `outlier_scrapers/api.py`, `auth.py`, `paths.py`, `registry.py`, `schema.py`, `normalizer.py`, `games.py`, `props.py`, `game_totals.py`, `team_totals.py`, `alt_spreads.py`, `daily_job.py`, `refresh_plan.py`, `feed_health.py`, `utils.py`
  - `docs/games_section_api_map.md`, `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md`
  - `ORIGINAL_REQUEST.md`, `context.md`, peer assignments in `teamwork_preview_*`
- **Key findings**:
  - Outlier API accepts league token `NFL` on `/sportsdata/leagues/NFL/schedule`, `playerProps`, and `/sportsdata/events/{eventId}/markets?marketType=...`
  - `outlier_scrapers` contains hardcoded MLB/WNBA whitelists and complex AI desk logic; a decoupled `outlier_nfl` package avoids coupling while adapting proven resilience patterns
  - Full module blueprint specified for `outlier_nfl` (`api.py`, `config.py`, `models.py`, `schema.py`, `normalizer.py`, `games.py`, `props.py`, `pipeline.py`, `utils.py`)
  - Market coverage taxonomy specified for game totals (O/U), team totals (O/U), point spreads (H/A), and player props (passing, rushing, receiving, TD)
- **Unexplored areas**: Live network verification of current in-season NFL slate (left to implementer/offline fixtures per benchmark mode)

## Key Decisions Made
- `outlier_nfl` designed as a root-level standalone package with 0 imports from `outlier_scrapers`.
- Session discovery adapts file-based access to `config/.outlier_session/storage_state.json` without depending on `outlier_scrapers.auth`.
- Comprehensive market normalization design covers all R3 requirements and acceptance criteria.

## Artifact Index
- `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md` — Full Architectural Blueprint Report (24 KB)
- `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\handoff.md` — 5-Component Handoff Document