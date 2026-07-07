# Project state — read first

_Last updated: 2026-07-07 by codex_

## Current state
- PREGAME-ONLY GUARD added (2026-07-07): the 2026-07-06 "MLB data corruption" was actually
  LIVE in-game lines — the pipeline ran ~80-100 min after first lock (props 03:29/03:38Z vs
  locks 02:10/02:00Z). New `drop_locked_events` in `pack.py` drops candidates whose
  `_event_starts_at` <= build time (wired in `build_pack`, warning-logged); a follow-up review
  now also drops missing, malformed, or timezone-less starts so the rule fails closed. ROLE_BLOCK and
  prompts A/D/E now carry the stand-down rule (as_of at/after first lock = live-line leak,
  stand the whole event down). The 404/403 fetch-error bursts + `ev_record_count: 0` on that
  slate were live-game symptoms, not corruption. See
  `.agent-log/2026-07-07-claude-live-line-guard.md`.
- PACK REBUILD INVALIDATION added (2026-07-07): `write_pack` removes stale reasoner outputs,
  final reports, manifests, status, and old dossiers before writing replacement inputs. This
  prevents a same-date rebuild from reusing a pre-fix report. `daily_job` now skips all desk
  passes when safety filters produce an empty pack, while still writing a successful manifest.
  Freshness timestamps must be timezone-aware, no more than 6h old, and not >5m in the future.
  Focused pack/daily tests: 51 passed; Ruff clean. A read-only replay of canonical 2026-07-06
  data dropped 523 non-pregame/unverifiable candidates and returned zero rows.
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
- [ ] Upstream live-guard: skip already-started events at props/line-movement fetch time
      (404/403 bursts on live slates are expected noise until then).
- [x] daily_job: skip the paid desk passes when the pack is empty after the live filter.
- [x] Reconcile the 3 drifted test_pack tests — resolved on synced master;
      tests/test_pack.py is 37/37 green as of 2026-07-07.
- [ ] Full pytest run hangs locally (live API calls from missing-key tests reloading `.env`);
      could not complete a full-suite run on 2026-07-06.
- [ ] Run a paid Prompt C smoke test when Gemini quota is intentionally available.
- [ ] Restore OpenAI capacity, Gemini quota, and Anthropic credits before expecting a FULL desk.
- [x] Investigate MLB prop line-movement fetch errors — root-caused 2026-07-07: markets
      404/403 because the games were LIVE when fetched (see live-line-guard log entry).
- [ ] Prevent pytest missing-key tests from reloading the real `.env` and making live API calls.
- [ ] Reconcile `tests/test_run_desk.py` with current `run_desk.py`: tests still expect removed
      `PHASE_KEYS` and `load_environment` symbols (1 failure, 4 setup errors; unrelated to live guard).
- [ ] Do not push the disconnected local recovery commit `d05eb21`; use `2bf8ffd` instead.
- [ ] WARNING: something re-materialized `pack.py` from origin/master mid-session on
      2026-07-06, wiping uncommitted edits (sync tooling or OneDrive). Commit early.

## How to update this file
Overwrite the two sections above at the end of your session. Keep it short —
this is the 10-second briefing, not the full history (that's in the dated
entries and `git log`).
