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
| #4 "Basic Poisson/Skellam" push prob in `game_totals.py` | Module is a deterministic market-devig board (`game_totals.py:1-5`); two-way devig cannot recover push mass | **E1 confirmed:** derive push mass deterministically from bracketing half-point rungs; modeling (E2) rejected. |
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

## Fix E — Push probability for integer totals (deterministic, half-point derivation)

**Decision: E1 (deterministic). Confirmed 2026-07-11.** No scoring model is
introduced; push probability is derived only from prices the book already
publishes, preserving the module's determinism contract.

**Why deterministic.** `game_totals.py` is a market-devig board: "Reasoning
agents consume the output; they never recompute probability or edge"
(`game_totals.py:1-5`). Probabilities come from interpolating a two-way
over/under ladder (`game_totals.py:192-204`). A two-outcome devig **structurally
cannot recover push mass**, which is exactly why integer lines carry
`push_capable_no_prob` (`game_totals.py:342-345`). A Poisson/Skellam model would
need a projected run/point mean the pipeline doesn't compute, and injecting a
modeled estimate into a market-derived file conflicts with the "packs stay
deterministic, no live/model-derived numbers" house rule — so it is explicitly
rejected here (see "Rejected alternative" below).

**Approach.** For an integer line `L`, the push mass is the probability the game
lands exactly on `L`. Half-point lines cannot push, so their devigged
probabilities are clean. When the ladder contains the bracketing rungs `L-0.5`
and `L+0.5`, derive:

```
P(push at L) ≈ P(Under L+0.5) - P(Under L-0.5)
```

i.e. the cumulative-probability gap between the two neighboring half-point
lines, using the same per-line devig already computed in
`aggregate_line_p_over` (`game_totals.py:171-189`). The ladder is already built
as `line -> {over, under}` in `build_market_ladder` (`game_totals.py:146-168`),
so the neighboring rungs are available without new data.

**Fallback.** If either bracketing half-point rung is missing (or its side is
incomplete), keep the existing `push_capable_no_prob` flag and leave `push_prob`
empty — never fabricate the number. This makes the feature purely additive: it
only fills a value where the market already provides enough structure.

**Wiring.** Replace the current unconditional block at `game_totals.py:340-345`
(which always blanks `push_prob` and stamps `push_capable_no_prob` on integer
lines) with the half-point derivation, falling back to today's behavior. When a
push prob is computed, gate whether it should also unlock `actionable` — an
integer line with a known push mass is no longer a hard EV dead-end, but confirm
the EV math accounts for the push before flipping `actionable` (keep it
conservative; a follow-up can revisit `actionable` once push-aware EV is
reviewed).

- **[MODIFY]** `outlier_scrapers/game_totals.py` — add a `derive_push_prob(ladder_p_by_side, line)`
  helper and call it from the integer-line branch (`game_totals.py:340-345`).

**Rejected alternative (E2, modeling).** A Poisson (MLB runs) / Normal-approx
(WNBA points) scoring distribution was considered and rejected: it is a new
modeling capability with its own calibration/validation/ownership and breaks the
deterministic-pack guarantee. Not to be implemented inside `game_totals.py`.

**Tests.** `tests/test_game_totals.py` already exists — extend it:
- integer line **with** both bracketing half-point rungs → non-empty `push_prob`
  equal to the half-point cumulative gap; `push_capable_no_prob` cleared.
- integer line **missing** a bracketing rung → `push_prob` stays empty and
  `push_capable_no_prob` is retained (fallback path).
- half-point line → unchanged (no push branch taken).

---

## Sequencing

1. **Fix A** — lock re-filter (unblocks the most damaging failure; ~1 line + audit).
2. **Fix B** — 403 investigation (read status reports first; may need no code).
3. **Fix C** — WNBA context in pack.py.
4. **Fix D** — prompt taxonomy.
5. **Fix E** — deterministic half-point push-prob derivation (E1, confirmed).

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

## Status

> [!NOTE]
> All five fixes (A–E) are scoped and code-accurate. Fix E is confirmed as the
> deterministic E1 approach (half-point derivation; no scoring model). Ready to
> implement on the `docs/betting-reports-fix-plan` feature branch.
