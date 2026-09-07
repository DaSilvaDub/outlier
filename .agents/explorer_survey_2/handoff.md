# Handoff Report: Outlier NCAAFB Props & Insights Survey

**Agent:** explorer_survey_2  
**Handoff Type:** Hard  
**Target Repository Worktree:** `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`  
**Current Branch:** `feat/outlier-props-insights`  
**Analysis File:** `C:\Users\dasil\Dev\GitHub\outlier\.agents\explorer_survey_2\analysis.md`  

---

## 1. Observation

### 1.1 Discovery Commit & Probe Artifacts
At commit `a9371c35529c90d71551e726c387414500f1247f` (`feat(discovery): R1 discovery probe script, fixtures, and findings`), the following probe artifacts and fixtures were committed:
- `docs/probes/2026-09-05-ncaafb-props-discovery.md` (141 lines, 9,000 bytes)
- `scripts/probe_ncaafb_outlier.py` (890 lines, 38,195 bytes)
- `tests/test_fixtures_cleanliness.py` (55 lines, 2,015 bytes)
- `tests/fixtures/outlier/` (16 JSON fixture files)

### 1.2 Probed MarketType Tokens & HTTP Response Status
As documented in `docs/probes/2026-09-05-ncaafb-props-discovery.md` (lines 41-79) and verified against `tests/fixtures/outlier/`:
1. `GAMELINE`:
   - Endpoint: `GET /sportsdata/events/{eventId}/markets?marketType=GAMELINE`
   - Response status: HTTP 200 OK across all sampled events.
   - Fixture sizes: ~1.5 MB to 1.7 MB (53,841 to 58,530 lines per event).
2. `TEAM_PROP`:
   - Endpoint: `GET /sportsdata/events/{eventId}/markets?marketType=TEAM_PROP`
   - Response status: HTTP 200 OK.
   - Fixture content: `{"markets": []}` (21 bytes).
3. `PLAYER_PROP`:
   - Endpoint: `GET /sportsdata/events/{eventId}/markets?marketType=PLAYER_PROP`
   - Response status: HTTP 200 OK.
   - Fixture content: `{"markets": []}` (21 bytes).
4. `GAME_PROP`:
   - Endpoint: `GET /sportsdata/events/{eventId}/markets?marketType=GAME_PROP`
   - Response status: HTTP 200 OK.
   - Fixture content: `{"markets": []}` (21 bytes).
5. `/insights`:
   - Endpoint: `GET /sportsdata/events/{eventId}/insights`
   - Response status: HTTP 200 OK.
   - Fixture content: `{"insights": []}` (22 bytes).
   - Does NOT return 404 or 403.

### 1.3 Proposition Frequency & Distinct Sportsbook Depth
Across the 3 sampled events on slate `2026-09-05`:
- `SPREAD`: 25 market cards (3/3 events, 100%); 13 distinct sportsbooks (`BETRIVERS`, `CAESARS`, `DRAFTKINGS`, `FANATICS`, `FANDUEL`, `FLIFF`, `HARDROCK`, `HARDROCK_R`, `KALSHI`, `MIDNITE`, `NOVIG`, `PROPHETX`, `THESCOREBET`); median 13.0 books/game.
- `TOTAL`: 26 market cards (3/3 events, 100%); 13 distinct sportsbooks (identical 13 books); median 13.0 books/game.
- `MONEYLINE`: 19 market cards (3/3 events, 100%); 12 distinct sportsbooks; median 9.0 books/game.
- `DOUBLE_RESULT`: 3 market cards (3/3 events, 100%); 3 distinct sportsbooks (`DRAFTKINGS`, `FANATICS`, `MIDNITE`); median 3.0 books/game.
- `MONEYLINE_THREE_WAY`: 8 market cards (3/3 events, 100%); 3 distinct sportsbooks (`DRAFTKINGS`, `FANATICS`, `THESCOREBET`); median 2.0 books/game.
- `WINNING_MARGIN`: 5 market cards (3/3 events, 100%); 2 distinct sportsbooks (`DRAFTKINGS`, `FANDUEL`); median 2.0 books/game.
- `TEAM_PROP` propositions: 0. (0 cards, 0 books).
- `PLAYER_PROP` propositions: 0. (0 cards, 0 books).
- `GAME_PROP` propositions: 0. (0 cards, 0 books).

### 1.4 Player Identity Field
Direct inspection of outcome keys in all market fixtures revealed:
`['bookPrimary', 'books', 'label', 'line', 'lineLabel', 'odds', 'outcomeId', 'outcomeProps', 'position', 'primary', 'stats']`
- Zero `playerId` or `athleteId` fields exist in market outcomes.
- Zero player display names exist in market outcomes.
- `PLAYER_PROP` endpoints return empty market arrays.

### 1.5 Structural Traps
1. **Trap 1 (Ordering Mismatch):** In `tests/fixtures/outlier/*_GAMELINE.json`, 661 outcome records have `outcome.books != [o.book for o in outcome.odds]`. For instance, in `event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json`, `outcome.books[0]` is `THESCOREBET` while `outcome.odds[0].book` is `DRAFTKINGS`.
2. **Trap 2 (Multi-Row Spanning):** Single propositions span multiple market objects (e.g. 25 `SPREAD` cards and 26 `TOTAL` cards across 3 games).

---

## 2. Logic Chain

1. **Schedule Discovery:** `GET /sportsdata/leagues/NCAAFB/schedule` resolves with HTTP 200, containing 125 total events and 31 events on the `2026-09-05` reference slate (Observation §1.1, §1.2).
2. **Market Evaluation:** When probing event markets across four tokens:
   - `GAMELINE` returns HTTP 200 with heavy liquidity (Observation §1.2, §1.3).
   - `TEAM_PROP`, `PLAYER_PROP`, and `GAME_PROP` accept the token and return HTTP 200, but yield empty envelopes `{"markets": []}` (Observation §1.2).
3. **Insights Evaluation:** `GET /sportsdata/events/{eventId}/insights` resolves with HTTP 200, but yields an empty envelope `{"insights": []}`. It is neither 404 nor 403, but is unpopulated (Observation §1.2).
4. **Player Identity Absence:** Because player props are unoffered and gameline outcomes contain only game/team outcome metadata, no player identity exists in Outlier NCAAFB betting data (Observation §1.4).
5. **Parser Rules Derived from Evidence:**
   - Because of Trap 1, the parser must never use `outcome.books` for book attribution; `odds_entry["book"]` must be the sole source of book identity (Observation §1.5).
   - Because of Trap 2, the parser must union all market rows for a proposition and deduplicate by `(book, side, line)` (Observation §1.5).

---

## 3. Caveats

1. **Slate Scope:** The discovery probe evaluated a representative Week 1 slate (`2026-09-05`). It remains possible that Outlier offers team props or exotic props for College Football Playoff or bowl games, but for regular season slates, props are unoffered.
2. **Read-Only / Hermetic Replay:** Live network calls to `api.outlier.bet` were not performed in this session per the read-only benchmark mandate and reasoning-off rule; all findings derive from committed probe reports, scripts, and verified JSON fixtures from commit `a9371c3`.
3. **Alternative Interpretation Considered:** Could `TEAM_PROP` or `PLAYER_PROP` require different query parameters (such as `scope` or `period`)? Inspection of `scripts/probe_ncaafb_outlier.py` and Outlier REST conventions in other sports (MLB/NBA) indicates `marketType={marketType}` is the canonical top-level filter.

---

## 4. Conclusion

1. **Outlier NCAAFB Offerings:** Outlier offers liquid, multi-book coverage for standard NCAAFB gamelines (`SPREAD`, `TOTAL`, and `MONEYLINE`).
2. **Prop & Insights Availability:** Outlier does **not** offer college football player props, team props, game props, or insights. They return empty envelopes (`{"markets": []}` and `{"insights": []}`).
3. **Pipeline Action:**
   - Do not attempt to ingest player props into `odds_snapshots` or widen tables with `player_id`.
   - Implement graceful degradation: empty prop or insight responses must be handled cleanly with 0 rows inserted and no crashes.
   - Keep `--with-insights` and `--with-props` flags disabled by default, honoring the R1 stop condition.

---

## 5. Verification Method

### 5.1 Independent Reproduction Commands
In `C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights`:

1. **Verify Fixture Cleanliness:**
   ```powershell
   pytest tests/test_fixtures_cleanliness.py
   ```
   *Expected result:* 1 passed in ~0.15s.

2. **Verify Fixture Contents & Proposition Counts:**
   ```powershell
   python -c "import json, pathlib; [print(f.name, json.loads(f.read_text('utf-8'))) for f in pathlib.Path('tests/fixtures/outlier').glob('*') if 'GAMELINE' not in f.name and 'schedule' not in f.name]"
   ```
   *Expected result:* All non-gameline fixtures print `{'markets': []}` or `{'insights': []}`.

3. **Verify Trap 1 (Order Mismatch):**
   ```powershell
   python -c "import json, pathlib; f = pathlib.Path('tests/fixtures/outlier/event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json'); data = json.loads(f.read_text('utf-8')); mismatches = [o for m in data['markets'] for o in m['outcomes'] if o.get('books') != [x.get('book') for x in o.get('odds', [])]]; print('Mismatches:', len(mismatches))"
   ```
   *Expected result:* Prints `Mismatches: 221` (for that event alone).

### 5.2 Invalidation Conditions
This assessment is invalidated if:
- A new probe against `api.outlier.bet` during active season games returns non-empty market cards in `PLAYER_PROP` or `TEAM_PROP`.
- Outlier modifies the `/insights` endpoint to return active editorial cards for `NCAAFB`.
