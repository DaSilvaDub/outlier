# Handoff — Grok → Antigravity

**Date:** 2026-07-12  
**Agent:** grok  
**Branch:** `feat/split-team-totals` (based on `bbbb30f` / origin/master at handoff + `4cf37d0`)  
**Last commit SHA on this branch:** `4cf37d0` (applied)
**Background subagent:** cancelled mid-work

---

## Mission

Implement **game totals vs team totals split** (not teamRankings).

### Locked decisions (Grok review + user “best judgment”)
1. **`teamRankings` OUT OF SCOPE** — matchup API no longer returns it; normalizer already drops it.
2. **Product split only:**  
   - `game_totals.csv` = `GAMELINE` + `TOTAL` (`total_kind=game`)  
   - `team_totals.csv` = `TEAM_PROP` + `POINTS` (`total_kind=team`)  
3. **Shared schema:** `TEAM_TOTALS_HEADER = GAME_TOTALS_HEADER`  
4. **One projection engine:** `build_totals(..., kind=)` + wrappers `build_game_totals` / `build_team_totals`  
5. **Desk consumes both** (runner_common inject + prompts A–E)  
6. **Legacy parse:** filter by `total_kind`; blank kind → treat as game  
7. **Path helpers** on `LeaguePaths` (pack/games/line_movement)  
8. **No reasoning/desk live runs** unless user asks  

Original plan reviewed:  
`file:///c:/Users/dasil/.gemini/antigravity/brain/fd3822cb-5aa0-4122-b2ef-52cda6a8436b/implementation_plan.md`

---

## CRITICAL: tree is PARTIAL / BROKEN

**Do not ship as-is.** Downstream was edited before core producers landed.

### Present (uncommitted working tree on `feat/split-team-totals`)
| Area | Status |
|------|--------|
| `runner_common.py` | Team load/parse/hash/inject + kind filter; **imports `TOTAL_KIND_GAME/TEAM` from game_totals (symbols missing → runtime break on parse)** |
| Desk runners | Partial: `reasoning.py`, `claude_reasoning.py`, `gemini_research.py`, `c_research.py`, `run_desk.py`, `claude_synthesis.py` |
| `games.py` / `line_movement.py` | Partial path-helper usage |
| Prompts A, B, D, E | Mentions `team_totals` |
| Tests | `test_game_totals.py`, `test_pack.py`, `test_runner_common.py` extended (expect split APIs) |

### Missing / still stock (must implement)
| Area | Status |
|------|--------|
| `game_totals.py` | **Still combined** `build_game_totals` only; no `TOTAL_KIND_*`, no `build_team_totals`, no split eligibility |
| `pack.py` | **Still single** `game_totals.csv`; no team file/sections/briefing inject |
| `paths.py` | **No** `games_normalized_latest` / cards helpers |
| `prompts/C.md` | No `team_totals` yet |
| `daily_job.py` | Likely no team actionable count (verify) |
| `tests/test_paths.py` | Absent |

### Note on branch chaos
Session hit repeated branch resets (master / feat / test e2e). At one point `4cf37d0` briefly appeared with paths+game_totals on another branch; **this handoff branch is back at `bbbb30f` without that commit**. Prefer rebuilding cleanly on `feat/split-team-totals` from the uncommitted runner/prompt/test work rather than hunting ghosts.

---

## Recommended finish order for Antigravity
1. `git status` / stay on `feat/split-team-totals`  
2. Implement **core first:** `game_totals.py` → `paths.py` → `pack.py`  
3. Finish gaps: `prompts/C.md`, `daily_job.py` count  
4. Align path helpers with whatever `games.py` / `line_movement.py` already call  
5. Offline tests only:  
   `python -m pytest tests/test_game_totals.py tests/test_paths.py tests/test_runner_common.py tests/test_pack.py tests/test_daily_job.py tests/test_c_research.py tests/test_run_desk.py -q --tb=line`  
6. Commit on feature branch, push, open PR to `master`  
7. Update this HANDOFF with SHA + PR link  

---

## Files currently dirty (uncommitted)
```
outlier_scrapers/c_research.py
outlier_scrapers/claude_reasoning.py
outlier_scrapers/claude_synthesis.py
outlier_scrapers/games.py
outlier_scrapers/gemini_research.py
outlier_scrapers/line_movement.py
outlier_scrapers/reasoning.py
outlier_scrapers/run_desk.py
outlier_scrapers/runner_common.py
prompts/A.md
prompts/B.md
prompts/D.md
prompts/E.md
tests/test_game_totals.py
tests/test_pack.py
tests/test_runner_common.py
```

---

## Next steps
1. Complete core split + make tests green  
2. Push `feat/split-team-totals` and open PR  
3. Do **not** treat `teamRankings` as in-scope  

**Grok stopping here per user handoff to Antigravity.**
