## Handoff — `claude/sgp-parlay-alt-totals-vcelz5` (PR #39)

**Last Product Commit SHA:** see branch head (`fix: address PR 39 blocking review`); merged `origin/master` (post-PR 36).

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/39

**Files Touched:**
- `outlier_scrapers/alt_team_totals.py` (new) — board of team-total OVER alt
  lines with L10 hit rate in the 90-100% band (from outcome `stats`
  `l10Results`/`l10`), best-line stamping, 2-leg parlay suggestions with SGP
  flagging, standalone CLI writing to `data/<LG>/reports/`.
- `outlier_scrapers/pack.py` — `write_pack` now emits `alt_team_totals.csv`,
  `alt_team_total_parlays.csv`, `sections/alt_team_totals.md`.
- `tests/test_alt_team_totals.py` (new, 14 tests), `tests/test_pack.py`
  (asserts the new pack files exist).

**Verification:** 303 offline tests pass (pytest not installable in the remote
sandbox — egress blocked; used a local shim runner, run real pytest locally).
Drove both league CLIs and `pack --no-feedback-ledger` end-to-end against a
staged games feed; probed missing/malformed feed, bad league, started events.

**Known issue found (pre-existing, NOT from this PR):** `pack.main` without
`--no-feedback-ledger` fails in `feedback._probability` — `game_totals.py`
writes `implied_prob` as 0-100 while the feedback ledger validates 0-1
(`implied_prob must be between 0 and 1, got 85.401`). Needs a follow-up fix.

**Review fixes (blocking review, all reproduced+regressed):** board now scoped
to the pack/CLI target date (`--date`, default today local; `write_pack` passes
`out_dir.name`); `is_active=False` records dropped fail-closed; `SHORT_SAMPLE`
rows stay on the board but are parlay-ineligible.

**Next steps:** confirm MLB team-total proposition token (POINTS vs RUNS) on a
live slate; optional follow-ups: cross-league parlays, 3-leg combos.

---

## Handoff — independent projection layer plan

**Last Commit SHA:** `a783bce` (`docs: resolve projection plan review feedback`)

**Files Touched:**
- `docs/plans/independent-projection-layer.md` — implementation plan covering MLB/WNBA features, full-distribution models, pipeline contracts, tests, shadow rollout, and resolved review decisions.

**Next Steps:**
- Review the updated PR #37 with the other agents.
- Implement only after plan approval; begin with the phased MLB work and the normalizer/schema contracts.
- Implement from synchronized `origin/master` on a separate feature branch after plan approval.

---

## Handoff — daily pipeline run (master)

**Last Commit SHA**: N/A (No new code changes made this session)
**PR Link**: N/A

**Files Touched:**
- Data outputs only (packs, prompts)

**Next Steps:**
- Re-ran the daily Outlier pipeline locally to filter out completed games and fetch the latest line-movement data for the remaining games.
- Successfully generated and exported the tailored manual prompt files for the web LLMs to the target Desktop and Drive folders.
- The user is all set to manually copy the prompts and run their desk routines.

---

## Handoff - historical_edge_pct (PR #42)

**Last Commit SHA**: 54125348a7984fd3abd8b33e56ac7488fcb0b23c (plus handoff/review fixes)
**PR Link**: https://github.com/DaSilvaDub/outlier/pull/42
**Files Touched**:
- `outlier_scrapers/sizing.py`
- `tests/test_sizing.py`
- `outlier_scrapers/pack.py`
- `tests/test_pack.py`
- `docs/plans/2026-07-15-historical-edge-pct.md`

**Next Steps**:
- The `historical_edge_pct` column has been successfully implemented and verified to be descriptive-only. The task is fully complete and PR #42 is open against master. No further action is required from the agent.
