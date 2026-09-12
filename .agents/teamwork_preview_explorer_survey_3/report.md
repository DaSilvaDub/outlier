# Investigation Report: NFL Market Coverage, Normalization, and Verification Strategy

**Author:** Explorer 3 (`teamwork_preview_explorer`)  
**Working Directory:** `C:\Users\dasil\Dev\GitHub\outlier\.agents\teamwork_preview_explorer_survey_3`  
**Date:** 2026-09-12  
**Target Project:** Standalone NFL Betting Data Pipeline (`outlier_nfl`)  
**Reference Document:** `ORIGINAL_REQUEST.md` (Section `## 2026-09-12T10:39:21Z`)  

---

## 1. Executive Summary

This report establishes the market coverage taxonomy, normalization architecture, and verification strategy for the new standalone `outlier_nfl` package. Per requirement **R1**, `outlier_nfl` must mirror the architecture of the existing pipeline while remaining completely decoupled from MLB and WNBA code paths. Per requirement **R3**, the pipeline must process NFL player props and team props, specifically extracting **game totals**, **team totals**, and **spreads**. Per the **Acceptance Criteria**, the pipeline must include a robust `pytest` suite and a standalone end-to-end verification script (`verify_nfl_pipeline.py`).

### Key Findings
1. **Zero Coupling Mandate**: The existing `outlier_scrapers/normalizer.py` and `registry.py` are tightly coupled to MLB (e.g. `ALLOWED_MLB_PLAYER_PROPS = frozenset({"SO"})`, batter strikeouts exclusion, pitcher pitch counts) and WNBA. In NFL, team abbreviations overlap (e.g., `ARI` Cardinals vs Diamondbacks, `DET` Lions vs Tigers, `SF` 49ers vs Giants), and market mechanics differ dramatically (football key numbers at 3 and 7; point totals in 40s vs run totals in single digits). `outlier_nfl` must define its own `registry.py`, `normalizer.py`, and `schema.py`.
2. **Comprehensive NFL Market Taxonomy**:
   - **Game Lines (`GAMELINE`)**: Full-game Spread (`SPREAD`), Game Total Points (`TOTAL`), Moneyline (`ML`).
   - **Team Props (`TEAM_PROP`)**: Team Total Points (`POINTS`, `TOTAL`, `TEAM_TOTAL`).
   - **Player Props (`PLAYER_PROP`)**: Passing Yards (`PASS_YDS`), Passing Touchdowns (`PASS_TD`), Pass Completions (`PASS_COMP`), Pass Attempts (`PASS_ATT`), Interceptions (`INT`), Rushing Yards (`RUSH_YDS`), Rushing Attempts (`RUSH_ATT`), Receiving Yards (`REC_YDS`), Receptions (`REC`), Rushing + Receiving Yards (`RUSH_REC_YDS`), Anytime Touchdown (`ANYTIME_TD`).
3. **Critical Codebase Quirks & Math Invariants**:
   - **Implied Probability**: `normalizer.implied_probability(odds)` returns a percentage (e.g., `52.5%`), NOT a decimal (`0.525`). Sizing/modeling math expecting `[0, 1]` must divide by 100.
   - **Push Probability Adjustment**: For integer spreads (e.g. -3.0, -7.0) and integer totals (e.g. 44.0, 47.0), absolute win probability equals `(1.0 - push_prob) * conditional_win_prob`. Failing to make this adjustment will dangerously overestimate edge.
   - **JSON Parsing**: All API response parsing via `json.loads()` must use `strict=False` to tolerate unescaped control characters.
   - **Books Shape Difference**: Player props use `outcome["bookOdds"]` (a dictionary of books to `{odds: ...}`), whereas Games API returns `outcome["odds"]` (a flat list of `[{book, american, decimal}]`). Normalization must emit uniform book representations.
4. **Verification Strategy**:
   - Complete unit test suite in `tests/test_nfl_normalizer.py`, `tests/test_nfl_api.py`, and `tests/test_nfl_pipeline.py` using offline JSON fixtures (`tests/fixtures/nfl_schedule.json`, `nfl_player_props.json`, `nfl_games.json`).
   - Standalone CLI verification script `verify_nfl_pipeline.py` executable with `--fixture` (deterministic offline) or `--live` (with Outlier API credentials) that asserts expected markets, non-zero records, and correct schema types.

---

## 2. Existing Normalization Architecture Analysis

An exhaustive review of `outlier_scrapers/normalizer.py` (793 lines), `outlier_scrapers/registry.py` (428 lines), `outlier_scrapers/schema.py` (275 lines), `outlier_scrapers/game_totals.py` (1040 lines), and `outlier_scrapers/alt_spreads.py` (153 lines) revealed the exact mechanics that `outlier_nfl` should replicate and where football-specific divergence is required.

### 2.1 Registry & Sport Configuration Pattern (`registry.py`)
In `outlier_scrapers/registry.py:8-32`, sports are modeled using the `SportConfig` frozen dataclass:
```python
@dataclass(frozen=True)
class SportConfig:
    league_id: str
    app_route: str
    enabled_by_default: bool
    team_aliases: Mapping[str, str]
    market_aliases: Mapping[str, str]
    opp_rank_applicable: bool
    team_display: Mapping[str, str] = field(default_factory=dict)
```
- `_compact(value)`: strips all non-alphanumeric characters and converts to uppercase (`"".join(ch for ch in str(value).upper() if ch.isalnum())`).
- `normalize_team(config, value)`: matches `_compact(value)` against `config.team_aliases`. Unknown values return `None` (preserving raw values under `team_raw`).
- `normalize_market(config, value)`: matches `_compact(value)` against `config.market_aliases`.

### 2.2 Schedule & Team Context Indexing (`normalizer.py:188-323`)
- `build_schedule_index(schedule_payload, config)` parses the schedule's `events` list:
  - Extracts `eventId` (or `id`).
  - Extracts `away` and `home` dicts (`alias`, `name`, `teamId`).
  - Canonicalizes team codes via `normalize_team` and constructs `matchup` string (`"AWAY @ HOME"`).
  - Captures start times (`scheduledTime`, `startTime`, `startDate`, `date`).
- `build_team_index(schedule_payload, config)` maps every `teamId` globally to its canonical alias as a fallback when an event ID is omitted from the schedule index.
- `_extract_team_context(outcome, event_info, team_index, config)` resolves which team the outcome belongs to and who the opponent is.

### 2.3 Scope Detection (`normalizer.py:143-186`)
Outlier reuses proposition names across full-game and partial-period markets (e.g., full-game Total Points vs 1st Half Total Points).
- `_SCOPE_CHECKS` matches human labels:
  - `"first_half"`: `("1st half", "first half", "1h")`
  - `"second_half"`: `("2nd half", "second half", "2h")`
  - `"first_quarter"`: `("1st quarter", "first quarter", "1q")`
  - `"second_quarter"`: `("2nd quarter", "second quarter", "2q")`
  - `"third_quarter"`: `("3rd quarter", "third quarter", "3q")`
  - `"fourth_quarter"`: `("4th quarter", "fourth quarter", "4q")`
- In `outlier_nfl`, partial periods must be explicitly captured in `scope` (`"full_game"` vs `"first_half"`, etc.).

### 2.4 Books Ingestion Variance (Props vs Games)
There is a documented structural difference in how Outlier formats sportsbook odds:
1. **Player Props (`normalizer.py:324-345`)**:
   - Outlier provides `outcome["bookOdds"]`: `{"DRAFTKINGS": {"odds": -110}, "FANDUEL": {"odds": -115}}`.
   - Ingested via `_books_from_outcome`: emits `[{"book": "DraftKings", "odds": -110, "odds_raw": "-110"}]`.
2. **Games / Spreads / Totals (`normalizer.py:348-375`)**:
   - Outlier provides `outcome["odds"]`: `[{"book": "DRAFTKINGS", "american": -110, "decimal": 1.91}]`.
   - Ingested via `_books_from_game_outcome`: emits `[{"book": "DraftKings", "odds": -110, "odds_raw": "-110", "decimal": 1.91}]`.
3. **Trap in `outcomes[].books` ordering**:
   - In raw JSON, `outcomes[].books` (a list of book names) is **not** guaranteed to align positionally with `outcomes[].odds`. The parser must always read the book name directly from each odds element.

### 2.5 Deduplication Contract (`normalizer.py:378-388`)
Deduplication key for player props:
`_dedupe_key(row) = (league, event_id, market_id_or_raw, player_raw, side, line)`
If duplicate records exist, the entry with the richest book coverage (`len(row["books"])`) is retained.

---

## 3. Comprehensive NFL Market Coverage & Taxonomy

In accordance with requirement **R3**, the NFL pipeline must extract player props, game totals, team totals, and spreads. Below is the complete market taxonomy for the NFL domain.

### 3.1 Game Lines (`market_type == "GAMELINE"`)

| Market Name | Market Type | Proposition Aliases | Canonical Market Code | Position / Sides | Line Format | Scope |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Point Spread** | `GAMELINE` | `SPREAD`, `POINT_SPREAD` | `SPREAD` | `HOME`, `AWAY` | Signed float (`-3.5`, `+7.0`) | `full_game`, `first_half` |
| **Game Total Points** | `GAMELINE` | `TOTAL`, `TOTAL_POINTS`, `OVER_UNDER` | `TOTAL` | `OVER`, `UNDER` | Positive float (`44.5`, `47.0`) | `full_game`, `first_half` |
| **Moneyline** | `GAMELINE` | `MONEYLINE`, `ML` | `ML` | `HOME`, `AWAY` | Float `0.0` or `None` | `full_game` |
| **3-Way Moneyline** | `GAMELINE` | `MONEYLINE_THREE_WAY`, `ML_3WAY` | `ML_3WAY` | `HOME`, `AWAY`, `DRAW` | Float `0.0` or `None` | `full_game` (regulation) |

#### Key Invariants for Spreads & Totals:
- **Spread Sides**: Position must be `"HOME"` or `"AWAY"`. The team name must match the home/away team derived from matchup.
- **Signed Line Formatting**:
  - If home line is `-3.5`, selection is `"Kansas City Chiefs -3.5"`.
  - If away line is `+3.5`, selection is `"Baltimore Ravens +3.5"`.
- **Total Sides**: Position must be `"OVER"` or `"UNDER"`.

### 3.2 Team Props (`market_type == "TEAM_PROP"`)

| Market Name | Market Type | Proposition Aliases | Canonical Market Code | Position / Sides | Line Format | Scope |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Team Total Points** | `TEAM_PROP` | `POINTS`, `TOTAL`, `TEAM_TOTAL`, `TOTAL_POINTS` | `POINTS` | `OVER`, `UNDER` | Positive float (`24.5`, `27.0`) | `full_game`, `first_half` |
| **Team Offensive Yards** | `TEAM_PROP` | `OFFENSIVE_YARDS`, `TEAM_OFFENSIVE_YARDS` | `OFF_YDS` | `OVER`, `UNDER` | Positive float (`340.5`) | `full_game` |
| **Team Passing Yards** | `TEAM_PROP` | `PASSING_YARDS`, `TEAM_PASSING_YARDS` | `PASS_YDS` | `OVER`, `UNDER` | Positive float (`235.5`) | `full_game` |
| **Team Rushing Yards** | `TEAM_PROP` | `RUSHING_YARDS`, `TEAM_RUSHING_YARDS` | `RUSH_YDS` | `OVER`, `UNDER` | Positive float (`110.5`) | `full_game` |

#### Team Total Specifics:
In `outlier_scrapers/team_totals.py:12-19`, team total propositions are indexed by sport:
```python
TEAM_TOTAL_PROPOSITIONS = {
    "MLB": frozenset({"POINTS", "RUNS", "R", "TOTAL_RUNS", "TOTAL", "TEAM_TOTAL"}),
    "WNBA": frozenset({"POINTS", "TOTAL", "TEAM_TOTAL"}),
    "NBA": frozenset({"POINTS", "TOTAL", "TEAM_TOTAL"}),
    "NHL": frozenset({"GOALS", "TOTAL", "TEAM_TOTAL"}),
}
```
For NFL, `TEAM_TOTAL_PROPOSITIONS["NFL"]` must accept:
`frozenset({"POINTS", "TOTAL", "TEAM_TOTAL", "TOTAL_POINTS"})`.

### 3.3 Player Props (`market_type == "PLAYER_PROP"`)

| Category | Market Name | Proposition Aliases in Outlier API | Canonical Code | Side / Position | Typical Line Range |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Passing** | Passing Yards | `PASSING_YARDS`, `PASS_YARDS`, `PASS_YDS` | `PASS_YDS` | `OVER`, `UNDER` | `175.5` – `310.5` |
| **Passing** | Passing Touchdowns | `PASSING_TOUCHDOWNS`, `PASSING_TDS`, `PASS_TDS` | `PASS_TD` | `OVER`, `UNDER` | `0.5`, `1.5`, `2.5` |
| **Passing** | Pass Completions | `PASS_COMPLETIONS`, `COMPLETIONS` | `PASS_COMP` | `OVER`, `UNDER` | `16.5` – `26.5` |
| **Passing** | Pass Attempts | `PASS_ATTEMPTS`, `PASSING_ATTEMPTS`, `ATTEMPTS` | `PASS_ATT` | `OVER`, `UNDER` | `26.5` – `41.5` |
| **Passing** | Interceptions | `INTERCEPTIONS`, `PASS_INTERCEPTIONS`, `INTS` | `INT` | `OVER`, `UNDER` | `0.5` |
| **Passing** | Longest Completion | `LONGEST_COMPLETION`, `LONGEST_PASS` | `LONG_PASS` | `OVER`, `UNDER` | `33.5` – `40.5` |
| **Rushing** | Rushing Yards | `RUSHING_YARDS`, `RUSH_YARDS`, `RUSH_YDS` | `RUSH_YDS` | `OVER`, `UNDER` | `35.5` – `95.5` |
| **Rushing** | Rushing Attempts | `RUSHING_ATTEMPTS`, `RUSH_ATTEMPTS`, `CARRIES` | `RUSH_ATT` | `OVER`, `UNDER` | `10.5` – `21.5` |
| **Rushing** | Longest Rush | `LONGEST_RUSH`, `LONGEST_RUSHING_ATTEMPT` | `LONG_RUSH` | `OVER`, `UNDER` | `12.5` – `18.5` |
| **Receiving**| Receiving Yards | `RECEIVING_YARDS`, `REC_YARDS`, `REC_YDS` | `REC_YDS` | `OVER`, `UNDER` | `25.5` – `85.5` |
| **Receiving**| Receptions | `RECEPTIONS`, `CATCHES`, `REC` | `REC` | `OVER`, `UNDER` | `2.5` – `7.5` |
| **Receiving**| Longest Reception | `LONGEST_RECEPTION`, `LONGEST_CATCH` | `LONG_REC` | `OVER`, `UNDER` | `18.5` – `32.5` |
| **Combo** | Rush + Rec Yards | `RUSHING_RECEIVING_YARDS`, `RUSH_REC_YARDS` | `RUSH_REC_YDS`| `OVER`, `UNDER` | `55.5` – `120.5` |
| **Combo** | Pass + Rush Yards| `PASSING_RUSHING_YARDS`, `PASS_RUSH_YARDS` | `PASS_RUSH_YDS`| `OVER`, `UNDER` | `220.5` – `330.5`|
| **Scoring** | Anytime Touchdown | `ANYTIME_TOUCHDOWN`, `ANYTIME_TD`, `TD_SCORER` | `ANYTIME_TD` | `OVER` (`YES`) | `0.5` |
| **Scoring** | First Touchdown | `FIRST_TOUCHDOWN`, `FIRST_TD` | `FIRST_TD` | `OVER` (`YES`) | `0.5` |
| **Kicking** | Field Goals Made | `FIELD_GOALS_MADE`, `FGM` | `FGM` | `OVER`, `UNDER` | `1.5` |
| **Kicking** | Kicking Points | `KICKING_POINTS`, `KICKER_POINTS` | `KICK_PTS` | `OVER`, `UNDER` | `6.5` – `8.5` |
| **Defense** | Tackles + Assists | `TACKLES_ASSISTS`, `TOTAL_TACKLES` | `TKL_AST` | `OVER`, `UNDER` | `5.5` – `10.5` |
| **Defense** | Sacks | `SACKS` | `SACKS` | `OVER`, `UNDER` | `0.5`, `1.5` |

### 3.4 32 NFL Teams Taxonomy & Alias Mappings
In `outlier_nfl/registry.py`, all 32 NFL franchises must be mapped to standard canonical 2/3-letter codes, accounting for Outlier API aliases, cities, and historical nicknames:

```python
NFL_TEAM_ALIASES = {
    # AFC East
    "BUF": "BUF", "BUFFALO": "BUF", "BUFFALOBILLS": "BUF", "BILLS": "BUF",
    "MIA": "MIA", "MIAMI": "MIA", "MIAMIDOLPHINS": "MIA", "DOLPHINS": "MIA",
    "NE": "NE", "NWE": "NE", "NEWENGLAND": "NE", "NEWENGLANDPATRIOTS": "NE", "PATRIOTS": "NE",
    "NYJ": "NYJ", "NEWYORKJETS": "NYJ", "JETS": "NYJ",
    
    # AFC North
    "BAL": "BAL", "BALTIMORE": "BAL", "BALTIMORERAVENS": "BAL", "RAVENS": "BAL",
    "CIN": "CIN", "CINCINNATI": "CIN", "CINCINNATIBENGALS": "CIN", "BENGALS": "CIN",
    "CLE": "CLE", "CLEVELAND": "CLE", "CLEVELANDBROWNS": "CLE", "BROWNS": "CLE",
    "PIT": "PIT", "PITTSBURGH": "PIT", "PITTSBURGHSTEELERS": "PIT", "STEELERS": "PIT",
    
    # AFC South
    "HOU": "HOU", "HOUSTON": "HOU", "HOUSTONTEXANS": "HOU", "TEXANS": "HOU",
    "IND": "IND", "INDIANAPOLIS": "IND", "INDIANAPOLISCOLTS": "IND", "COLTS": "IND",
    "JAX": "JAX", "JAC": "JAX", "JACKSONVILLE": "JAX", "JACKSONVILLEJAGUARS": "JAX", "JAGUARS": "JAX",
    "TEN": "TEN", "TENNESSEE": "TEN", "TENNESSEETITANS": "TEN", "TITANS": "TEN",
    
    # AFC West
    "DEN": "DEN", "DENVER": "DEN", "DENVERBRONCOS": "DEN", "BRONCOS": "DEN",
    "KC": "KC", "KAN": "KC", "KANSASCITY": "KC", "KANSASCITYCHIEFS": "KC", "CHIEFS": "KC",
    "LV": "LV", "LVR": "LV", "LAS": "LV", "LASVEGAS": "LV", "LASVEGASRAIDERS": "LV", "RAIDERS": "LV", "OAK": "LV",
    "LAC": "LAC", "LOSANGELESCHARGERS": "LAC", "CHARGERS": "LAC", "SD": "LAC",
    
    # NFC East
    "DAL": "DAL", "DALLAS": "DAL", "DALLASCOWBOYS": "DAL", "COWBOYS": "DAL",
    "NYG": "NYG", "NEWYORKGIANTS": "NYG", "GIANTS": "NYG",
    "PHI": "PHI", "PHILADELPHIA": "PHI", "PHILADELPHIAEAGLES": "PHI", "EAGLES": "PHI",
    "WAS": "WAS", "WSH": "WAS", "WASHINGTON": "WAS", "WASHINGTONCOMMANDERS": "WAS", "COMMANDERS": "WAS",
    
    # NFC North
    "CHI": "CHI", "CHICAGO": "CHI", "CHICAGOBEARS": "CHI", "BEARS": "CHI",
    "DET": "DET", "DETROIT": "DET", "DETROITLIONS": "DET", "LIONS": "DET",
    "GB": "GB", "GNB": "GB", "GREENBAY": "GB", "GREENBAYPACKERS": "GB", "PACKERS": "GB",
    "MIN": "MIN", "MINNESOTA": "MIN", "MINNESOTAVIKINGS": "MIN", "VIKINGS": "MIN",
    
    # NFC South
    "ATL": "ATL", "ATLANTA": "ATL", "ATLANTAFALCONS": "ATL", "FALCONS": "ATL",
    "CAR": "CAR", "CAROLINA": "CAR", "CAROLINAPANTHERS": "CAR", "PANTHERS": "CAR",
    "NO": "NO", "NOR": "NO", "NEWORLEANS": "NO", "NEWORLEANSSAINTS": "NO", "SAINTS": "NO",
    "TB": "TB", "TAM": "TB", "TAMPABAY": "TB", "TAMPABAYBUCCANEERS": "TB", "BUCCANEERS": "TB", "BUCS": "TB",
    
    # NFC West
    "ARI": "ARI", "AZ": "ARI", "ARIZONA": "ARI", "ARIZONACARDINALS": "ARI", "CARDINALS": "ARI",
    "LAR": "LAR", "LOSANGELESRAMS": "LAR", "RAMS": "LAR", "LA": "LAR",
    "SF": "SF", "SFO": "SF", "SANFRANCISCO": "SF", "SANFRANCISCO49ERS": "SF", "49ERS": "SF", "NINERS": "SF",
    "SEA": "SEA", "SEATTLE": "SEA", "SEATTLESEAHAWKS": "SEA", "SEAHAWKS": "SEA",
}
```

---

## 4. Normalization Architecture & Quirks for `outlier_nfl`

### 4.1 Football Key Numbers & Push Probability Adjustment
In baseball (MLB), game runs are low-scoring and discrete. In American football (NFL), scoring is governed by field goals (3 points) and touchdowns (7 points with PAT, 6 without, 8 with 2-pt conversion).
- **Spread Key Numbers**:
  - **3**: ~14.8% of NFL games end in a 3-point margin.
  - **7**: ~9.5% of NFL games end in a 7-point margin.
  - **6, 10, 4, 14**: Secondary key numbers.
- **Push Probability Impact**:
  - Half-point spreads (`-3.5`, `+3.5`, `-6.5`, `+7.5`) have `push_prob = 0.0`.
  - Integer spreads (`-3.0`, `+3.0`, `-7.0`, `+7.0`) have `push_prob` ranging from `0.08` to `0.15`.
  - **Mandatory Adjustment Rule**:
    $$\text{Absolute Win Prob} = (1.0 - \text{push\_prob}) \times \text{Conditional Win Prob}$$
    If this adjustment is omitted, sizing models will dangerously overestimate the expected value on integer lines.

### 4.2 Implied Probability Calculation
Per repository rules in `.agents/AGENTS.md`, `normalizer.implied_probability(american_odds)` returns a **percentage** (e.g., `52.381` for `-110` odds):
```python
def implied_probability(american_odds: Any) -> float | None:
    text = str(american_odds or "").strip().upper().replace("−", "-")
    if not text:
        return None
    if text in {"EVEN", "EVENS"}:
        return 50.0
    price = _to_int(text)
    if not price:
        return None
    prob = 100.0 / (price + 100.0) if price > 0 else abs(price) / (abs(price) + 100.0)
    return round(prob * 100.0, 3)
```
When downstream edge calculations expect decimal probability $[0, 1]$, `implied_probability / 100.0` must be used.

### 4.3 Safe JSON Decoding
Per repository rules in `.agents/AGENTS.md`, upstream Outlier responses can contain unescaped control characters.
All parsing must invoke:
```python
payload = json.loads(body.decode("utf-8"), strict=False)
```

---

## 5. Test Suite Architecture & Verification Strategy

To fulfill the **Acceptance Criteria**:
1. Unit test suite for `outlier_nfl` passing without errors via `pytest`.
2. Tests explicitly verifying that NFL player props, team props (totals), and game lines (totals, spreads) are parsed correctly.
3. Standalone end-to-end verification script (`verify_nfl_pipeline.py`) asserting expected markets.

### 5.1 Test Suite Organization
The test suite will reside in `tests/` following repository conventions:
```
tests/
├── fixtures/
│   ├── nfl_schedule.json          # Multi-game schedule fixture (e.g. KC@BAL, SF@LAR, DAL@PHI)
│   ├── nfl_player_props.json      # Rich player props payload (Pass/Rush/Rec/TD)
│   └── nfl_games.json             # Games payload (Spread, Game Total, Team Total, ML)
├── test_nfl_normalizer.py         # Unit tests for normalizer, registry, and schema
├── test_nfl_api.py                # Unit tests for OutlierApiClient targeting NFL endpoints
└── test_nfl_pipeline.py           # Unit tests for end-to-end extraction and export
verify_nfl_pipeline.py             # Standalone verification runner script at repo root
```

### 5.2 Fixture Specifications

#### `tests/fixtures/nfl_schedule.json`
Contains 3 realistic pregame events with full team details and UTC start timestamps:
- Event 1: `KC @ BAL` (Kansas City Chiefs at Baltimore Ravens)
- Event 2: `SF @ LAR` (San Francisco 49ers at Los Angeles Rams)
- Event 3: `DAL @ PHI` (Dallas Cowboys at Philadelphia Eagles)

#### `tests/fixtures/nfl_player_props.json`
Contains realistic player prop entries with active status, `outcomeId`, `playerId`, `teamId`, `position`, `line`, and `bookOdds`:
1. Patrick Mahomes: `PASS_YDS` Line `274.5`, Over `-115`, Under `-115`
2. Patrick Mahomes: `PASS_TD` Line `1.5`, Over `-140`, Under `+110`
3. Lamar Jackson: `RUSH_YDS` Line `54.5`, Over `-110`, Under `-110`
4. Derrick Henry: `RUSH_ATT` Line `16.5`, Over `-105`, Under `-125`
5. Derrick Henry: `ANYTIME_TD` Line `0.5`, Over `-150`
6. Travis Kelce: `REC_YDS` Line `62.5`, Over `-110`, Under `-110`
7. Travis Kelce: `REC` Line `5.5`, Over `-120`, Under `+100`

#### `tests/fixtures/nfl_games.json`
Contains `events` with markets for:
1. `GAMELINE`: `SPREAD` (e.g., `KC -3.0` vs `BAL +3.0`, line `-3.0` / `+3.0`)
2. `GAMELINE`: `TOTAL` (e.g., Over `47.5` vs Under `47.5`)
3. `GAMELINE`: `MONEYLINE` (e.g., `KC -160` vs `BAL +135`)
4. `TEAM_PROP`: `POINTS` / Team Total (e.g., `KC Over 24.5` / `Under 24.5`, `BAL Over 22.5` / `Under 22.5`)

### 5.3 Concrete Test Coverage Plan (`tests/test_nfl_*.py`)

#### `tests/test_nfl_normalizer.py`
- `test_nfl_team_normalization()`:
  - Asserts that variations (`"Chiefs"`, `"Kansas City"`, `"KAN"`, `"KC"`, `"KANSASCITYCHIEFS"`) all normalize to `"KC"`.
  - Asserts that all 32 NFL teams map correctly to their 2/3-letter canonical codes.
  - Asserts that unknown team strings safely return `None`.
- `test_nfl_market_normalization()`:
  - Asserts that prop variations map to canonical codes:
    - `"PASSING_YARDS"` / `"Passing Yards"` -> `"PASS_YDS"`
    - `"PASSING_TOUCHDOWNS"` / `"Pass TDs"` -> `"PASS_TD"`
    - `"RUSHING_YARDS"` / `"Rush Yards"` -> `"RUSH_YDS"`
    - `"RECEIVING_YARDS"` / `"Rec Yards"` -> `"REC_YDS"`
    - `"RECEPTIONS"` / `"Catches"` -> `"REC"`
    - `"SPREAD"` / `"Point Spread"` -> `"SPREAD"`
    - `"TOTAL"` / `"Total Points"` -> `"TOTAL"`
    - `"POINTS"` / `"Team Total"` -> `"POINTS"`
- `test_nfl_schedule_indexing()`:
  - Validates `build_schedule_index` extracts event IDs, matchups (`"KC @ BAL"`), home/away team IDs, and kickoff times.
- `test_normalize_nfl_player_props()`:
  - Asserts normalized rows contain all mandatory schema fields: `league == "NFL"`, `event_id`, `market_id`, `outcome_id`, `player`, `player_id`, `market`, `position` (`OVER`/`UNDER`), `line`, `books`, `best_odds`, `ip_pct`.
  - Verifies that inactive props (`outcome.get("active") is False`) and partial period scopes are handled properly.
  - Verifies deduplication logic retains the row with the most book odds.
- `test_normalize_nfl_games_spreads_and_totals()`:
  - Verifies `SPREAD` outcomes map to `position` `"HOME"` / `"AWAY"`, with correct team attribution.
  - Verifies `TOTAL` outcomes map to `position` `"OVER"` / `"UNDER"`.
  - Verifies `TEAM_PROP` outcomes map to the specific team being priced.
  - Verifies `_books_from_game_outcome` properly parses the list-of-dicts `odds` format into uniform `{book, odds, odds_raw, decimal}` records.

#### `tests/test_nfl_api.py`
- `test_nfl_api_url_generation()`:
  - Tests client URL construction:
    - Schedule: `/sportsdata/leagues/NFL/schedule`
    - Props: `/sportsdata/leagues/NFL/playerProps`
    - Insights: `/sportsdata/leagues/NFL/insights`
    - Event Markets: `/sportsdata/events/{id}/markets?marketType=GAMELINE`
    - Event Markets: `/sportsdata/events/{id}/markets?marketType=TEAM_PROP`
- `test_nfl_api_mock_fetch()`:
  - Uses mock opener to simulate HTTP 200 responses with gzip compression and asserts `json.loads(..., strict=False)` operates without error.
- `test_nfl_api_retry_and_error_handling()`:
  - Verifies retry on transient 429/500/502 errors and immediate abort on 401 Unauthorized.

#### `tests/test_nfl_pipeline.py`
- `test_export_nfl_props_end_to_end()`:
  - Executes `export_props_for_league(client, "NFL")` using mocked client returning `nfl_schedule.json` and `nfl_player_props.json`.
  - Verifies payload contract: `dataset == "outlier_nfl_player_props"`, `record_count > 0`, `primary_record_array == "records"`.
- `test_export_nfl_games_end_to_end()`:
  - Executes `export_games_for_league(client, "NFL")` using mocked client returning `nfl_schedule.json` and `nfl_games.json`.
  - Verifies payload contract: `dataset == "outlier_nfl_games"`, `records` contains spreads, totals, and team totals.

---

## 6. Standalone Verification Script Specification (`verify_nfl_pipeline.py`)

Per the Acceptance Criteria:
> "A standalone script (e.g., `verify_nfl_pipeline.py`) is provided that runs the `outlier_nfl` extraction end-to-end. The script automatically asserts that the output data structure contains the expected NFL prop markets."

### 6.1 Script CLI Design
The script must reside at the repository root (`verify_nfl_pipeline.py`) and support the following CLI flags:
```
python verify_nfl_pipeline.py [--fixture | --live] [--date YYYY-MM-DD] [--verbose]
```
- `--fixture` (Default when credentials are not detected): Runs the complete end-to-end normalization pipeline using committed JSON fixtures (`tests/fixtures/nfl_*.json`).
- `--live`: Attempts real Outlier API calls against the live NFL endpoints using storage state credentials. If authentication fails, exits cleanly with actionable instructions.
- `--verbose`: Prints full breakdown of parsed markets, book counts, and sample normalized rows.

### 6.2 Execution Flow & Assertions Architecture

```
                 +--------------------------------+
                 |    verify_nfl_pipeline.py      |
                 +--------------------------------+
                                 |
              [ Mode: --fixture (Offline Mock) or --live ]
                                 |
         +-----------------------+-----------------------+
         |                                               |
         v                                               v
[ Step 1: Schedule Ingestion ]                [ Step 2: Games & Props ]
- Fetch/load NFL schedule                      - Fetch/load GAMELINE
- Validate >= 1 pregame events                 - Fetch/load TEAM_PROP
- Index teams and matchups                     - Fetch/load PLAYER_PROP
         |                                               |
         +-----------------------+-----------------------+
                                 |
                                 v
                 [ Step 3: Normalization Engine ]
                 - outlier_nfl.normalizer.normalize_player_props()
                 - outlier_nfl.normalizer.normalize_games()
                                 |
                                 v
                 [ Step 4: Verification Assertions ]
                 (Hard checks against output data structure)
                 |
                 +--> Assert 1: record_count > 0
                 +--> Assert 2: "SPREAD" present in gameline markets
                 +--> Assert 3: "TOTAL" present in gameline markets
                 +--> Assert 4: "POINTS" present in team_props
                 +--> Assert 5: "PASS_YDS", "RUSH_YDS", "REC_YDS" in props
                 +--> Assert 6: Schema keys present on every row
                 +--> Assert 7: All odds are integer American prices
                 +--> Assert 8: Implied probabilities calculated
                                 |
                                 v
                 [ Step 5: Summary & Exit Status ]
                 - Print formatted validation matrix
                 - Exit 0 (SUCCESS) or Exit 1 (FAILURE)
```

### 6.3 Automated Validation Assertions
The verification script will execute the following rigorous assertions:
1. **Payload Schema Contract**:
   - `assert "records" in props_result and isinstance(props_result["records"], list)`
   - `assert "records" in games_result and isinstance(games_result["records"], list)`
   - `assert props_result["record_count"] == len(props_result["records"])`
   - `assert games_result["record_count"] == len(games_result["records"])`
2. **Game Line Markets**:
   - `spread_rows = [r for r in games_result["records"] if r.get("proposition") == "SPREAD"]`
   - `assert len(spread_rows) > 0, "No SPREAD markets found in normalized games"`
   - `assert all(r["position"] in ("HOME", "AWAY") for r in spread_rows)`
   - `assert all(isinstance(r["line"], (int, float)) for r in spread_rows)`
   - `total_rows = [r for r in games_result["records"] if r.get("proposition") == "TOTAL"]`
   - `assert len(total_rows) > 0, "No TOTAL markets found in normalized games"`
   - `assert all(r["position"] in ("OVER", "UNDER") for r in total_rows)`
3. **Team Props**:
   - `team_totals = [r for r in games_result["records"] if r.get("market_type") == "TEAM_PROP"]`
   - `assert len(team_totals) > 0, "No TEAM_PROP markets found in normalized games"`
   - `assert all(r.get("team") is not None for r in team_totals)`
4. **Player Props**:
   - `found_markets = {r.get("market") for r in props_result["records"]}`
   - `required_prop_markets = {"PASS_YDS", "RUSH_YDS", "REC_YDS"}`
   - `assert required_prop_markets.issubset(found_markets), f"Missing expected prop markets: {required_prop_markets - found_markets}"`
   - `assert all(r.get("player_id") is not None for r in props_result["records"])`
   - `assert all(r.get("player") is not None for r in props_result["records"])`

---

## 7. Recommended Implementation Sequence for the Team

Based on these findings, here is the recommended execution sequence for the teamwork team:

1. **Step 1 (Explorer 1 & Explorer 2 Coordination)**:
   - Finalize `outlier_nfl` package structure and API endpoint client definitions.
2. **Step 2 (Fixtures Creation)**:
   - Add `tests/fixtures/nfl_schedule.json`, `tests/fixtures/nfl_player_props.json`, and `tests/fixtures/nfl_games.json`.
3. **Step 3 (Core Normalizer & Registry)**:
   - Implement `outlier_nfl/registry.py` with 32 NFL team aliases and NFL market mappings.
   - Implement `outlier_nfl/schema.py` with schema validation gates.
   - Implement `outlier_nfl/normalizer.py` with football-specific prop and game normalization.
4. **Step 4 (Test Suite Implementation)**:
   - Implement `tests/test_nfl_normalizer.py`, `tests/test_nfl_api.py`, and `tests/test_nfl_pipeline.py`.
   - Run `pytest tests/test_nfl_*.py` to verify 100% pass rate.
5. **Step 5 (Standalone Verification Script)**:
   - Implement `verify_nfl_pipeline.py` at root.
   - Run `python verify_nfl_pipeline.py --fixture` and assert exit code 0.
6. **Step 6 (Git Branch Workflow)**:
   - Commit all work on a dedicated feature branch with `Agent:` trailer, push to origin, and open PR against `master`.

---

## 8. Conclusion

The existing `outlier` architecture provides a battle-tested template for sportsbook market ingestion and normalization. By constructing `outlier_nfl` as an independent standalone package with dedicated team/market registries, football key-number push probability handling, and comprehensive player/team prop taxonomies, the team can deliver a resilient NFL pipeline that leaves the MLB and WNBA pipelines untouched while fully meeting all requirements of R3 and the project acceptance criteria.
