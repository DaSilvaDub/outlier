# Handoff Summary — 2026-08-22 — Grok (predictor accuracy)

**Agent:** Grok  
**Time:** 2026-08-22 (session continued from predictor assessment → SO gamelog → accuracy branch)  
**Canonical master HEAD:** `a464dbb` (pipeline-safe; do not mix feature work here)  
**Active feature branch HEAD:** `ab9c14a` on `feat/predictor-accuracy-upgrades`  
**Feature worktree:** `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`  
**PR:** https://github.com/DaSilvaDub/outlier/compare/master...feat/predictor-accuracy-upgrades (open if not already)

---

## Last Commit SHAs

| Tree | Branch | SHA | Notes |
|---|---|---|---|
| `C:\Users\dasil\Dev\GitHub\outlier` | `master` | `a464dbb` | Live pipeline checkout. Keep clean for `daily_job`. |
| Feature worktree | `feat/predictor-accuracy-upgrades` | `ab9c14a` | Accuracy upgrades. **Do not run daily_job here.** |
| Master SO foundation (already merged) | `master` ancestry | `1be5bc1`, `41358bb` | Gamelog SO + empty-hash eligibility fix |

Sync attestation when this handoff was written:
```
REPORT STATUS: OK
RUN-NONCE: 815b9611e50f446f  utc=2026-08-22T12:40:26Z  head=a464dbb  status=OK
```

---

## What landed on master (already shipped — do not redo)

1. Predictor foundation (`c8ca8c9`): signal-gated PLAY, 1u market-devig cap, L10→`recency_hit_prob`, blend audit-only, IL Out-only, insight no fake 50.0, shadow fallback guard.
2. `aeb042b`: `import re` for SO name parser.
3. `1be5bc1`: pitcher-specific SO BF/K from MLB Stats API gamelogs; `GAMELOG_SO_HASH` independent; league-avg audit-only; `pitcher_id` + enrich on probable export.
4. `41358bb`: empty/missing feature hash no longer blocks non-SO independents (only `LEAGUE_AVG_SO_HASH` blocked).

Live proof on pack `2026-08-21`: 7/7 SO candidates had gamelog independent probs; gates/1u cap/MLB SO-only whitelist held.

---

## What landed on feature branch `ab9c14a` (NOT merged)

Worktree: `C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades`

### Files touched
- `outlier_scrapers/slate_quality.py` — `promote_independent_so_sizing` (gamelog → live Kelly, 2u cap, requires predictive signal)
- `outlier_scrapers/line_movement.py` — `STALE_PROPS_MAX_AGE_HOURS` 12→**6** (align feed_health)
- `outlier_scrapers/results.py` — `_latest_local_close` fallbacks (any book → market_id+selection)
- `outlier_scrapers/projections.py` — `apply_so_context_adjustments`, WNBA minutes/PPM scaffold
- `outlier_scrapers/so_eval.py` — **new** offline Brier eval vs market
- `tests/test_predictor_gates.py`, `tests/test_so_eval_and_context.py`
- `docs/plans/2026-08-22-predictor-accuracy-branch.md`

### Tests
```powershell
Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
python -m pytest tests/test_predictor_gates.py tests/test_so_eval_and_context.py tests/test_pitcher_so_features.py -q --tb=line
# → 26 passed
```

### Critical eval finding (do not merge sizing promotion blindly)
```powershell
python -m outlier_scrapers.so_eval
# Against canonical feedback DB (path override to master calibration):
# n=26 settled SO with both probs
# market_brier=0.260  independent_brier=0.270  prefer_independent=false
# gamelog_rows=0  ← no post-1be5bc1 gamelog settlements in ledger yet
```
**Gate:** accumulate gamelog-hashed settled SO rows, re-run `so_eval`, only then merge/promote live independent sizing.

---

## How to continue (next agent)

### Preferred resume path
```powershell
& "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
# Read this file:
#   C:\Users\dasil\Dev\GitHub\outlier\.agent-log\HANDOFF.md
Set-Location C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades
git status
git log -3 --oneline
```

If worktree missing:
```powershell
cd C:\Users\dasil\Dev\GitHub\outlier
git fetch origin
git worktree add C:\Users\dasil\Dev\GitHub\outlier-worktrees\predictor-accuracy-upgrades feat/predictor-accuracy-upgrades
```

### Next steps (ordered)
1. **Do not merge** `feat/predictor-accuracy-upgrades` until `so_eval` shows gamelog independent ≤ market Brier with `gamelog_rows > 0` (or user explicitly overrides).
2. After more daily packs settle (post-`1be5bc1`), re-run:
   `python -m outlier_scrapers.so_eval` from the feature worktree (point `--db` at `C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3`).
3. Wire real **opponent_k_rate / park_k_factor** enrichers (hooks exist; feeds not built).
4. Wire **WNBA minutes/PPM** into pack path (`wnba_points_projection_record` is scaffold-only).
5. Keep `config/portfolio_risk.json` **shadow**; do not `fit-blend` on old H/HRR/BB universe.
6. Open/update PR: `gh pr create --base master --head feat/predictor-accuracy-upgrades` if missing.
7. Live pipeline stays on **master** only: `C:\Users\dasil\Dev\GitHub\outlier`.

### House rules
- Never run paid desk/reasoning unless user asks this turn.
- Never blind `report-sync` with dirty intentional feature work (stash/commit first). Feature worktree is preserved by sync when it has upgrade markers.
- MLB player props remain SO-only at generation.

---

## Session continuity files
- Agent handoff (this file): `.agent-log/HANDOFF.md` (force-add when committing)
- Branch plan: `docs/plans/2026-08-22-predictor-accuracy-branch.md` (on feature branch)
- Longer session dump: `C:\Users\dasil\.claude\session-data\2026-08-22-predictor-accuracy-handoff-session.tmp`
- Prior sessions: `2026-08-20-outlier-predictor-session.tmp`, `2026-08-20-outlier-pred-handoff2-session.tmp`
