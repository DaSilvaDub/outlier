# Betting Reports Weakness Fix Plan — Corrected (Code-Accurate)

> Supersedes `ANTIGRAVITY_implementation_plan.md`. That draft diagnosed real
> symptoms from the four generated reports (`CHAT`, `CLAUDE`, `GEMINI`, `GROK`)
> but mapped fixes to files without reading the current tree. This version is
> verified against the codebase — every claim carries a `file:line` anchor.
> Fixes are ordered by leverage-to-risk, not by the original numbering.

## What changed vs. the original draft

| Original claim | Reality in code | Correction |
|---|---|---|
| #1 Add retries/backoff to line movement | Full HTTP-403 retry subsystem already exists (`line_movement.py:1230-1302`) | Root-cause surviving 403s; tune existing knobs. Do **not** write new retry code. |
| #2 Create `drop_locked_events` in `claude_reasoning.py` / `games.py` | Function already exists (`pack.py:554`) and runs at pack build (`pack.py:929`); reasoning stage never re-applies it | Re-apply the **existing** function at reasoning time. ~1 line. |
| #3 Fix WNBA context in `normalize_games` (`games.py`) | No `normalize_games` exists; opponent/home-away live in `pack.py:245-388` | Fix `_home_away`/matchup parsing in `pack.py`, not `games.py`. |
| #4 "Basic Poisson/Skellam" push prob in `game_totals.py` | Module is a deterministic market-devig board (`game_totals.py:1-5`); two-way devig cannot recover push mass | Split into a separate design decision. Prefer a deterministic half-point derivation over a scoring model. |
| #5 Variance taxonomy + anti-hallucination in prompts | `prompts/C.md`, `prompts/D.md` exist; pack already embeds anti-inference guidance (`pack.py:667-685`) | Keep; place taxonomy so it doesn't contradict existing prompt rules. |

---

## Fix A — Re-apply the lock filter at reasoning time (highest leverage, lowest risk)

**Problem (confirmed).** `drop_locked_events(rows, now=...)` already exists
(`pack.py:554`) and already runs when the pack is *built* (`pack.py:929`). But
`claude_reasoning.py` consumes the already-generated pack later and never
re-filters — it imports `pack` (`claude_reasoning.py:21`) yet never calls the
gate. When an event locks between pack build and model run, stale
pregame-marked rows reach the prompt. This is exactly why `CLAUDE`/`GEMINI`
recommended the already-started `ATH @ CWS` and `LAA @ MIN`.

**Fix.** Re-apply the existing filter with a fresh `now` immediately before the
candidate rows are serialized into the prompt, in every reasoning entrypoint
that builds a prompt from pack rows.

```python
# in the reasoning runner, after loading pack rows, before prompt assembly
from outlier_scrapers.pack import drop_locked_events

kept, locked = drop_locked_events(rows, now=datetime.now().astimezone())
if locked:
    logger.warning(
        "Reasoning-time lock filter dropped %d event(s) locked since pack build",
        len(locked),
    )
rows = kept
```

- **[MODIFY]** `outlier_scrapers/claude_reasoning.py` — apply before prompt build.
- **[AUDIT]** `outlier_scrapers/c_research.py`, `gemini_research.py`,
  `reasoning.py`, `run_desk.py` — apply the same re-filter anywhere pack rows are
  turned into a prompt. (Grep for the prompt-assembly site in each.)
- **Reuse, do not reimplement.** `drop_locked_events` is pure and returns new
  lists (no mutation) — safe to call at both stages.

**Tests.**
- Extend `tests/test_pack.py` (already exercises the filter) with a
  reasoning-time case: a row whose `_event_starts_at` is in the past relative to
  an injected `now` must be excluded from the prompt payload.
- Assert the runner logs the drop count so silent filtering can't hide a slate
  problem.

---

## Fix B — Root-cause the surviving line-movement fetch errors (do not add retries)

**Problem (re-scoped).** The 31 errors are surfaced and *already retried*. The
403 retry subsystem — cooldown, multi-round mop-up, throttled workers,
terminal-vs-retryable classification — is at `line_movement.py:1230-1302`, with
CLI knobs at `line_movement.py:1406-1423`, and failing markets are already named
in `error_market_ids` (`line_movement.py:1365`). So the open question is **why
403s survive the retry pass**, not "add retries."

**Investigation (do this before any code change).**
1. Read `line_movement_status_latest.json` / `games_line_movement_status_latest.json`:
   check `retry_403_recovered` vs `retry_403_still_failed` and the residual
   `error_market_ids`.
2. Classify the residuals:
   - **Post-lock 404s** → these are correct failures; fold them into Fix A's
     pregame gate rather than treating them as errors.
   - **Token expiry mid-run** → the fix is auth-refresh in `auth.py`/`api.py`,
     not retry tuning.
   - **Genuine rate-limit 403s** → tune existing knobs (raise
     `--retry-403-cooldown-seconds`, lower first-pass `--workers`).

- **[MODIFY — only if warranted]** `outlier_scrapers/line_movement.py` retry
  defaults (`DEFAULT_RETRY_403_*` at `line_movement.py:33-35`), and/or
  `outlier_scrapers/auth.py` for mid-run token refresh.

**Tests.** Add a case asserting a 404 residual is classified terminal (not
retried as 403) — the classification already exists at `line_movement.py:1275-1281`;
lock it in against regression.

---

## Fix C — WNBA opponent / home-away context (fix in pack.py, not games.py)

**Problem (correct location).** There is no `normalize_games` in `games.py`.
Opponent and home/away are derived in `pack.py`: `_home_away()` splits the
matchup on `" @ "` (`pack.py:245-259`) and `opponent`/`home_away` are assigned at
`pack.py:378-388`. Missing WNBA context means a malformed or empty `matchup`
string is reaching `_home_away`, which then can't resolve the side.

**Fix.**
1. Instrument `_home_away` to emit a quality flag (e.g. `HOME_AWAY_UNRESOLVED`)
   instead of silently returning empty, so bad rows are visible in the pack the
   same way other data-quality flags are.
2. Trace the WNBA `matchup`/`matchup_raw` upstream to its source
   (`_ordered_market_ids` context in `line_movement.py:134-147`, and the games
   export in `games.py`) and confirm both `away @ home` tokens are populated for
   WNBA before pack assembly.

- **[MODIFY]** `outlier_scrapers/pack.py` (`_home_away` + context assignment).
- **[AUDIT]** `outlier_scrapers/games.py` `export_games_for_league`
  (`games.py:60`) for the WNBA home/away side loop (`games.py:125`).

**Tests.** Add WNBA fixtures to `tests/test_pack.py`: a well-formed matchup
resolves `home_away` and `opponent`; a malformed one raises the new flag rather
than producing a blank, unreproducible row.

---

## Fix D — Prompt variance taxonomy + anti-hallucination (low risk)

**Problem (valid).** Models wavered on whether strikeout (K) props are "high"
vs "moderate" variance, and `GEMINI` invented data ("Natasha Mack (Out)",
pitcher "Shane Drohan") while rendering HTML.

**Fix.**
1. Add an explicit variance table to the base prompt guidance:
   - **High variance:** 3PM, hits allowed, total bases, turnovers.
   - **Moderate variance:** strikeouts, assists, points.
2. Strengthen the HTML-generation constraint: **render only fields present in
   the candidate rows; never introduce a player, injury status, or pitcher not
   in the pack.** Note the pack already forbids inference from event-id hashes
   and terse codes (`pack.py:667-685`) — place the new rule alongside it so the
   two don't conflict.

- **[MODIFY]** `prompts/C.md`, `prompts/D.md` (confirm which template each model
  actually loads before editing; `prompts/A.md`–`E.md` all exist).

**Tests.** N/A (prompt content) — verify via the manual dry-run below.

---

## Fix E — Push probability for integer totals (SEPARATE DECISION — do not batch)

**Why it's carved out.** `game_totals.py` is a deterministic market-devig board:
"Reasoning agents consume the output; they never recompute probability or edge"
(`game_totals.py:1-5`). Probabilities come from interpolating a two-way
over/under ladder (`game_totals.py:192-204`). A two-outcome devig **structurally
cannot recover push mass**, which is exactly why integer lines carry
`push_capable_no_prob` (`game_totals.py:342-345`). A Poisson/Skellam model needs
a projected run/point mean the pipeline doesn't compute, and injecting a modeled
estimate into a deliberately market-derived file conflicts with the
"packs stay deterministic, no live/model-derived numbers" house rule.

**Two options — pick before writing code:**

- **E1 (preferred, deterministic).** Derive push probability from adjacent
  half-point ladder rungs when both exist (the mass between `line-0.5` and
  `line+0.5` cumulative probabilities), falling back to the existing flag
  otherwise. Stays market-derived; no scoring model. Keeps the module's
  determinism contract intact.
- **E2 (modeling).** Introduce a scoring distribution (Poisson for MLB runs,
  Normal approx for WNBA points). This is a new modeling capability with its own
  calibration, validation, and ownership — out of scope for a bugfix batch.

**Recommendation:** do E1 or defer. Do not implement E2 inside `game_totals.py`
without a dedicated design pass.

- **[MODIFY — E1 only]** `outlier_scrapers/game_totals.py` push-prob branch.

**Tests.** `tests/test_game_totals.py` already exists — extend it: an integer
line with bracketing half-point rungs yields a push prob; without them it keeps
`push_capable_no_prob`.

---

## Sequencing

1. **Fix A** — lock re-filter (unblocks the most damaging failure; ~1 line + audit).
2. **Fix B** — 403 investigation (read status reports first; may need no code).
3. **Fix C** — WNBA context in pack.py.
4. **Fix D** — prompt taxonomy.
5. **Fix E** — separate decision (E1 or defer).

## Branch & review discipline

Per project rules this is product code, so: one **feature branch**, PR to
`master`, no direct commits. Run `pytest tests/` (existing `test_pack.py` and
`test_game_totals.py` already cover the touched areas — extend, don't duplicate).

## Verification

**Automated**
- `pytest tests/` green.
- New/extended cases per Fix A, B, C, E above.

**Manual dry-run**
- Generate a pack, then run the reasoning pipeline **with a `now` set past a
  known lock** and confirm the locked event is absent from the prompt (Fix A).
- Confirm no residual 403s are actually post-lock 404s (Fix B).
- Confirm every WNBA row has resolved `opponent`/`home_away` or a visible flag
  (Fix C).
- Confirm HTML output introduces no player/injury/pitcher absent from the pack
  (Fix D).

## User review required

> [!IMPORTANT]
> Fixes A–D are code-accurate and safe to implement on a feature branch.
> **Fix E requires a decision (E1 deterministic vs E2 modeling, or defer)**
> before any edit to `game_totals.py`. Confirm the scope before I start.
