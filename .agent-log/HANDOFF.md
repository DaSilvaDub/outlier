## Handoff — `fix/pack-structural-integrity` (PR #31)

**Last Commit SHA:** `5cb6282` (code commit on `fix/pack-structural-integrity`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/31

**Files Touched:**
- `outlier_scrapers/props.py` — bounded repair of schedule gaps for prop-referenced events.
- `outlier_scrapers/cards.py` — preserved market type; headline-scoped line mismatch and ladder-aware spread mirror validation.
- `outlier_scrapers/pack.py` — coverage report, actionability, non-actionable EV audit section, team-total dedup/labels, and sourced proxy probability/edge/Kelly.
- `tests/test_props.py`, `tests/test_cards.py`, `tests/test_pack.py`, `tests/test_run_desk.py` — regression coverage and signature adaptation.

**Verification:** 105 focused tests passed (73 pack; 32 cards/props); Ruff and MyPy passed. Offline game-card/pack regeneration passed. `test_run_desk.py` collection hung before executing the mocked test and was terminated; no provider calls ran.

**Next Steps:**
- Review PR #31 and run CI.
- On the next authenticated props refresh, verify the MLB single-event enrichment restores `event_starts_at` and produces MLB candidates. Current stale July 13 source data still reports MLB as zero emitted with 394 unverifiable-start drops, now explicitly surfaced rather than silently omitted.

---

**Last Commit SHA:** `37e665d` (on branch `feat/desk2-manual-research-desk`)

---

## Handoff — `fix/spread-sign-conflict` (PR #32, merged)

- **Last Commit SHA**: `6ce1389`
- **Branch**: `fix/spread-sign-conflict`
- **Files Touched**: `outlier_scrapers/cards.py`

## Work Completed
Fixed the `spread_sign_conflict` issue. The pipeline's `_pick_main_side_row` function selects lines for HOME and AWAY independently, which caused mismatched lines when one side had an EV target and the other fell back to pick'em. 

I introduced `_align_main_lines()` and called it from both `assemble_game_card` and `assemble_card` immediately after the independent selection loop. This function ranks each side's chosen line by strength (EV > Movement > Pick'em). If there is a mismatch (either differing magnitude for spreads, or differing lines for totals), the stronger side forces the weaker side to pick its exact mirror/matching line.

## Next Steps
- Open a Pull Request from `fix/spread-sign-conflict` to `master` (I did not create the PR locally because `gh` CLI isn't authenticated/setup for PR creation without user intervention, but the branch is pushed).
- After PR merge, the agent should verify `python -m outlier_scrapers.daily_job` no longer drops the LAS/ATL `03ae55e7` game spread card.
