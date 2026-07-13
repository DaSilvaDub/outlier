## Session Summary

- **Last Commit SHA**: `6ce1389`
- **Branch**: `fix/spread-sign-conflict`
- **Files Touched**: `outlier_scrapers/cards.py`

## Work Completed
Fixed the `spread_sign_conflict` issue. The pipeline's `_pick_main_side_row` function selects lines for HOME and AWAY independently, which caused mismatched lines when one side had an EV target and the other fell back to pick'em. 

I introduced `_align_main_lines()` and called it from both `assemble_game_card` and `assemble_card` immediately after the independent selection loop. This function ranks each side's chosen line by strength (EV > Movement > Pick'em). If there is a mismatch (either differing magnitude for spreads, or differing lines for totals), the stronger side forces the weaker side to pick its exact mirror/matching line.

## Next Steps
- Open a Pull Request from `fix/spread-sign-conflict` to `master` (I did not create the PR locally because `gh` CLI isn't authenticated/setup for PR creation without user intervention, but the branch is pushed).
- After PR merge, the agent should verify `python -m outlier_scrapers.daily_job` no longer drops the LAS/ATL `03ae55e7` game spread card.
