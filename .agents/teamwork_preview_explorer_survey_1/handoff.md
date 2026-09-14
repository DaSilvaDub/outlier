# Handoff Report — Explorer 1 Survey: Standalone `outlier_nfl` Blueprint

## 1. Observation
1. **Repository Architecture & Invariants**:
   - In `outlier_scrapers/normalizer.py` (lines 23-28):
     ```python
     ALLOWED_MLB_PLAYER_PROPS = frozenset({"SO"})
     ALLOWED_MLB_TEAM_PROPS = frozenset({"R", "TOTAL"})
     ```
     MLB player and team props are strictly filtered down to Pitcher Strikeouts and Runs/Total at generation time.
   - In `outlier_scrapers/registry.py` (lines 317-347):
     The `SPORTS` dictionary only defines configurations for `"MLB"`, `"WNBA"`, and an inactive placeholder `"NBA"`. NFL is entirely absent from the existing registry.
   - In `outlier_scrapers/api.py` (lines 20-23, 89-106):
     API endpoints use `API_BASE_URL = "https://api.outlier.bet"`, with `RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}`, `RetryPolicy` utilizing bounded exponential backoff with +/- 50% random jitter, and `strict=False` in `json.loads` calls (line 150) to mitigate unescaped control characters.
   - In `docs/plans/2026-08-31-ncaaf-analytics-pipeline.md` (lines 716-718):
     Prior live API probing confirmed: "`NFL` also resolves (260 events), which confirms 502 means 'unknown league' rather than a transport failure."
   - In `docs/games_section_api_map.md` (lines 28-33):
     Live capture confirmed Outlier `/sportsdata/events/{eventId}/markets?marketType=...` accepts `marketType` tokens `GAMELINE`, `PLAYER_PROP`, and `TEAM_PROP`.
2. **Project Prompt & Requirements**:
   - `C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md` (lines 129-138):
     - R1: Create standalone package `outlier_nfl` within existing `outlier` repository, separate from WNBA/MLB code paths.
     - R2: Reuse Outlier API integration patterns, hitting NFL-specific endpoints.
     - R3: Process NFL player props and team props (game totals, team totals, spreads).
     - Acceptance criteria require a pytest test suite and a standalone verification script `verify_nfl_pipeline.py`.
3. **Artifact Created**:
   - Report delivered to `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md` (24,367 bytes, 397 lines).

## 2. Logic Chain
1. **Observation 1 & 2 -> Need for Complete Decoupling**:
   Because `outlier_scrapers` contains hardcoded domain whitelists for MLB (`ALLOWED_MLB_PLAYER_PROPS = {"SO"}`), baseball inning scope parsers, and deep coupling to the AI Research Desk, importing or modifying `outlier_scrapers` for NFL would either pollute the baseball/basketball invariants or subject NFL ingestion to MLB house rules. Therefore, `outlier_nfl` must be an independent top-level package with zero runtime imports from `outlier_scrapers`.
2. **Observation 1 & 2 -> Pattern Reuse Without Code Coupling**:
   The resilience mechanisms in `outlier_scrapers` (Bearer token discovery from `config/.outlier_session/storage_state.json`, `RetryPolicy` with jitter, progress signature fingerprinting in pagination, and `_replace_with_retry` for Windows `[WinError 32]` cloud locks) can be cleanly implemented in `outlier_nfl/api.py` and `outlier_nfl/utils.py` using standard library packages. This preserves operational reliability while achieving total modular decoupling.
3. **Observation 1 & 2 -> Comprehensive Market Coverage (R3)**:
   By establishing a dedicated `outlier_nfl/config.py` with 32 NFL franchises and football market mappings, `outlier_nfl/games.py` will accurately extract:
   - Full-game Game Totals (`GAMELINE` -> `TOTAL`, sides `OVER`/`UNDER`)
   - Point Spreads (`GAMELINE` -> `SPREAD`, sides `HOME`/`AWAY`, formatted signed lines like `KC -3.5`)
   - Team Totals (`TEAM_PROP` -> `POINTS`/`TOTAL`/`TEAM_TOTAL`, sides `OVER`/`UNDER`, mapped to canonical teams)
   And `outlier_nfl/props.py` will extract all core NFL player prop families (passing yards/TDs/completions/attempts/interceptions, rushing yards/attempts, receiving yards/receptions, anytime touchdowns).
4. **Observation 2 & 3 -> Verification Architecture**:
   The verification script `verify_nfl_pipeline.py` and pytest test suite `tests/test_nfl_*.py` require deterministic offline fixtures (`tests/fixtures/nfl/`) to ensure all tests run without hitting the live network or external AI providers, complying with repository testing constraints.

## 3. Caveats
1. **No Live Network Calls Made During Survey**:
   In strict adherence to the read-only exploration assignment and repo house rules, no live API requests were executed during this survey. League token availability and payload shapes are grounded in committed repo documentation (`docs/plans/2026-08-31-ncaaf-analytics-pipeline.md`, `docs/games_section_api_map.md`).
2. **Auth Prerequisite**:
   Live execution of `verify_nfl_pipeline.py` in `--mode live` requires an active session in `config/.outlier_session/storage_state.json` or `OUTLIER_BEARER_TOKEN`. Replay mode (`--mode replay`) using committed fixtures will allow full verification offline without credentials.

## 4. Conclusion
The architectural survey is complete and fully documented in `report.md`. The proposed `outlier_nfl` standalone package cleanly decouples from `outlier_scrapers`, covers all requested NFL markets (game totals, team totals, spreads, player props), provides complete specifications for all 10 package modules, and defines the testing and verification harnesses required for the implementer agents.

## 5. Verification Method
1. **Inspect Architectural Report**:
   Verify that `report.md` exists and is populated:
   ```powershell
   Get-Item "C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_1\report.md"
   ```
2. **Review Module Blueprint**:
   Examine Section 3 of `report.md` for interface contracts of `outlier_nfl/__init__.py`, `constants.py`, `config.py`, `models.py`, `api.py`, `schema.py`, `normalizer.py`, `props.py`, `games.py`, `pipeline.py`, and `utils.py`.
3. **Review Market Coverage Specification**:
   Examine Section 4 of `report.md` to verify that game totals, team totals, point spreads, and player prop families are fully covered.
4. **Review Verification and Test Strategy**:
   Examine Section 6 of `report.md` for `verify_nfl_pipeline.py` assertions and pytest layout.