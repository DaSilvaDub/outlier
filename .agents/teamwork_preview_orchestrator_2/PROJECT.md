# Project: outlier_nfl

## Architecture
`outlier_nfl` is a standalone NFL betting data pipeline within the `outlier` repository, completely decoupled from MLB and WNBA code paths.

### Package Structure
```
outlier/
├── outlier_nfl/
│   ├── __init__.py          # Export surface and package version
│   ├── constants.py         # API base URL, NFL league token, retry/timeout settings
│   ├── config.py            # 32 NFL team aliases, display names, and market taxonomy
│   ├── models.py            # Strongly-typed immutable dataclasses (BookPrice, NflGameLine, NflPlayerProp)
│   ├── api.py               # OutlierNflApiClient (HTTP, session discovery, backoff, pagination)
│   ├── schema.py            # Raw payload & normalized record validation gates
│   ├── utils.py             # Atomic file write/replace with retry on WinError 32, date parsing
│   ├── normalizer.py        # Odds to implied prob %, signed lines, football scopes, team context
│   ├── games.py             # Game totals (O/U), point spreads (H/A), team totals (O/U)
│   ├── props.py             # Player props extractor (passing, rushing, receiving, TDs)
│   └── pipeline.py          # Unified NflPipeline orchestrator
├── data/
│   └── NFL/
│       ├── raw/             # Raw schedule, market, and prop JSON payloads
│       └── normalized/      # nfl_games_latest.json, nfl_props_latest.json
├── tests/
│   ├── fixtures/
│   │   └── nfl/             # schedule.json, event_markets.json, player_props.json
│   ├── test_nfl_api.py      # API client & retry unit tests
│   ├── test_nfl_normalizer.py # Normalizer, registry & models unit tests
│   └── test_nfl_pipeline.py # End-to-end pipeline extraction tests
└── verify_nfl_pipeline.py   # Standalone root verification CLI script
```

## Feature Inventory
Every feature from user requirements and the survey phase is enumerated here with its assigned milestone.

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Standalone Package Scaffolding | Create `outlier_nfl/` with zero imports from `outlier_scrapers` | M1 | R1, Survey |
| F2 | NFL Constants & Endpoints Config | `constants.py`: API base URL, `NFL` token, timeout & retry limits | M1 | R2, Survey |
| F3 | 32 NFL Team Registry & Market Taxonomy | `config.py`: Canonical 32-team aliases, abbreviations, market codes | M1 | R3, Survey |
| F4 | Domain Data Models | `models.py`: Immutable dataclasses `BookPrice`, `NflGameLine`, `NflPlayerProp` | M1 | R1, Survey |
| F5 | Resilient NFL API Client | `api.py`: `OutlierNflApiClient`, auth discovery, exponential backoff, gzip, strict=False | M1 | R2, Survey |
| F6 | Schema Validation Gates | `schema.py`: Validate raw API payloads and normalized datasets | M1 | R1, Survey |
| F7 | Resilient Utilities & File I/O | `utils.py`: Atomic write/replace with retry on WinError 32, date helpers | M1 | R1, Survey |
| F8 | NFL Normalization Engine | `normalizer.py`: Odds to implied prob %, signed lines, football scopes | M2 | R3, Survey |
| F9 | Game Lines Extraction | `games.py`: Game totals (O/U), point spreads (H/A), team totals (O/U) | M2 | R3, Survey |
| F10 | Player Props Extraction | `props.py`: Passing, rushing, receiving, TD props with book odds & lines | M2 | R3, Survey |
| F11 | End-to-End Pipeline Orchestrator | `pipeline.py`: `NflPipeline` managing extraction, normalization, persistence | M2 | R1, R3, Survey |
| F12 | Offline Test Fixtures | `tests/fixtures/nfl/`: Realistic schedule, event markets, and player props fixtures | M_E2E | Acceptance Criteria |
| F13 | Unit Test Suite | `tests/test_nfl_*.py`: Comprehensive pytest suite verifying API, normalizer, pipeline | M_E2E | Acceptance Criteria |
| F14 | Standalone Verification Runner | `verify_nfl_pipeline.py`: Root-level script asserting totals, spreads, team totals, props | M_E2E | Acceptance Criteria |
| F15 | Final Verification & Audit | 100% test pass rate, execution of verify script, independent victory audit | M3 | Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | NFL Core & API Client | F1, F2, F3, F4, F5, F6, F7 (`constants`, `config`, `models`, `schema`, `api`, `utils`) | none | IN_PROGRESS |
| M2 | NFL Normalization & Extractors | F8, F9, F10, F11 (`normalizer`, `games`, `props`, `pipeline`) | M1 | PLANNED |
| M_E2E | E2E Testing Track | F12, F13, F14 (`fixtures/nfl/`, `test_nfl_*.py`, `verify_nfl_pipeline.py`, `TEST_READY.md`) | M1 interface contracts | IN_PROGRESS |
| M3 | Final Acceptance & Verification | F15 (100% pytest pass, end-to-end `verify_nfl_pipeline.py` execution, forensic audit) | M2, M_E2E | PLANNED |

## Interface Contracts

### `outlier_nfl.api` ↔ Extractors (`games.py`, `props.py`, `pipeline.py`)
- `OutlierNflApiClient(base_url: str = API_BASE_URL, session_path: Path | None = None, bearer_token: str | None = None)`
- `client.fetch_schedule() -> dict`: Returns `{"events": [...]}`
- `client.fetch_event_markets(event_id: str, market_type: str) -> dict`: Returns `{"markets": [...]}`
- `client.fetch_player_props(max_pages: int = 60) -> dict`: Returns `{"props": [...], "_page": {...}}`

### `outlier_nfl.normalizer` ↔ Models (`models.py`)
- `normalize_game_markets(event: dict, markets_payload: dict, team_index: dict) -> list[NflGameLine]`
- `normalize_player_props(props_payload: dict, schedule_index: dict) -> list[NflPlayerProp]`
- `american_to_implied_probability(odds: int | str) -> float | None` (returns percentage e.g. 52.381)
- `format_signed_line(line: float | int | None) -> str | None` (e.g. -3.5 -> "-3.5", 3.5 -> "+3.5")

### Pipeline ↔ Downstream / Verification
- `pipeline = NflPipeline(client=..., data_dir=...)`
- `pipeline.run(date: str | None = None, offline_fixtures_dir: Path | None = None) -> dict`:
  Returns summary: `{"status": "OK", "date": "...", "game_lines_count": N, "player_props_count": M, "totals_count": T, "spreads_count": S, "team_totals_count": TT, "props_count": P}`
- Persisted outputs:
  - `data/NFL/normalized/nfl_games_latest.json`
  - `data/NFL/normalized/nfl_props_latest.json`

## Code Layout
- Package: `C:\Users\dasil\Dev\GitHub\outlier\outlier_nfl\`
- Tests: `C:\Users\dasil\Dev\GitHub\outlier\tests\`
- Fixtures: `C:\Users\dasil\Dev\GitHub\outlier\tests\fixtures\nfl\`
- Verification Script: `C:\Users\dasil\Dev\GitHub\outlier\verify_nfl_pipeline.py`
- Agent metadata: `C:\Users\dasil\Dev\GitHub\outlier\.agents\`
