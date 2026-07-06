# Project state — read first

_Last updated: 2026-07-06 by claude_

## Current state
- HOUSE RULES added (2026-07-06): HR / HRR (H+R+RBI) / BB (walks) markets are hard-excluded
  in `pack.py` (`EXCLUDED_MARKETS` + `is_excluded_market`, dropped in `build_row`); plus-money
  longshots (+150 or longer, e.g. a Hits Over at +181) are also HARD-FILTERED in `build_row`
  (`is_longshot_price` / `LONGSHOT_AMERICAN_PRICE`), with stand-down language in the briefing
  ROLE_BLOCK and prompts A/D/E. `BBA` (pitcher walks allowed) is NOT excluded.
- Prompt C is automated as a grounded Gemini injury/lineup pass on branch
  `codex/prompt-c-research` (`2bf8ffd`), based on canonical `master` history.
- A/B/C/D/E desk wiring is present without the recovery commit's recreated support stubs or
  fake local-synthesis placeholder.
- `tests/test_pack.py`: 26 pass / 3 pre-existing failures (`test_select_date`,
  `test_end_to_end`, `test_freshness_section_flags_stale_and_ok` — tests drifted from code:
  undated-row retention + "UNRELIABLE" wording). Ruff clean.

## Open follow-ups
- [ ] Reconcile the 3 drifted test_pack tests (undated rows in select_date; "UNRELIABLE"
      wording in freshness) — decide whether code or tests are right.
- [ ] Full pytest run hangs locally (live API calls from missing-key tests reloading `.env`);
      could not complete a full-suite run on 2026-07-06.
- [ ] Run a paid Prompt C smoke test when Gemini quota is intentionally available.
- [ ] Restore OpenAI capacity, Gemini quota, and Anthropic credits before expecting a FULL desk.
- [ ] Investigate 10 MLB prop line-movement fetch errors and zero WNBA candidate/game rows.
- [ ] Prevent pytest missing-key tests from reloading the real `.env` and making live API calls.
- [ ] Do not push the disconnected local recovery commit `d05eb21`; use `2bf8ffd` instead.
- [ ] WARNING: something re-materialized `pack.py` from origin/master mid-session on
      2026-07-06, wiping uncommitted edits (sync tooling or OneDrive). Commit early.

## How to update this file
Overwrite the two sections above at the end of your session. Keep it short —
this is the 10-second briefing, not the full history (that's in the dated
entries and `git log`).
