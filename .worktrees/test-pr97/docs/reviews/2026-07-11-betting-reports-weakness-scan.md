# Betting Reports Weakness Scan — Round 2 (post A–E)

> Read-only code audit of the report-generation pipeline, run after the A–E
> weakness fixes merged in PR #5. Every finding carries a `file:line` anchor and
> was verified against the current tree. Ordered by leverage-to-risk, not
> severity alone. No code was changed by this scan.

**Scope audited:** `reasoning.py`, `claude_reasoning.py`, `gemini_research.py`,
`c_research.py`, `claude_synthesis.py`, `run_desk.py`, `runner_common.py`,
`game_totals.py`, `sizing.py`, `cards_html.py`, and the relevant slices of
`pack.py` (`_home_away`, `drop_locked_events`, sizing integration).

**Healthy (no action):** the Fix A/C internals themselves — `pack.drop_locked_events`
(`pack.py:557`), `_home_away` (`pack.py:246`), and the push-aware sizing call at
`pack.py:432` — are correct and pure. Prompt C (`c_research.py:128`) and Prompt D
(`claude_reasoning.py:88`) both re-apply the lock filter via
`runner_common.validate_candidates`. The gaps below are where the pipeline
*diverges* from those good paths.

---

## F1 — [HIGH] Prompt A bypasses the reasoning-time lock filter (Fix A gap)

**Where.** `reasoning.py:46-67` (`validate_pack_dir`) → `reasoning.py:138` →
`call_openai_responses_api` (`reasoning.py:170`).

**Problem.** Fix A re-applies `drop_locked_events` at reasoning time so events
that lock *between* pack build and model run never reach the prompt. That fix
lives in `runner_common.validate_candidates` (`runner_common.py:88-122`) and is
used by C (`c_research.py:128`) and D (`claude_reasoning.py:88`). **Prompt A uses
its own `validate_pack_dir`, which validates header + row count but never calls
`drop_locked_events`.** So the OpenAI report (`chatgpt_a.md`) still ingests
already-started games — the exact "recommended `ATH @ CWS` / `LAA @ MIN` after
lock" failure Fix A was written to kill. Fix A landed on 3 of the 4 model paths.

**Secondary effect.** A hashes the *unfiltered* `raw_bytes` (`reasoning.py:54,
138`) while C/D hash the *filtered* candidates, so A's `candidates_sha256` is
computed on a different basis than the others.

**Fix.** Replace `validate_pack_dir` with `rc.validate_candidates(pack_dir)`
(returns filtered bytes + hash) exactly as `claude_reasoning.py:88` does. This
also deletes the duplicated validation (see F5).

---

## F2 — [HIGH] `game_totals` reports a stale `projected_over_prob`

**Where.** `game_totals.py:408` (write) vs `game_totals.py:315-320` (source of
the stale value) and `game_totals.py:336` (the correct value).

**Problem.** The ladder-building loop reuses the name `p_over` per line:

```python
for line, sides in ladder.items():                 # :315
    p_over, book_count, lf = aggregate_line_p_over(...)   # :316  <-- last iter leaks
```

After the loop, `p_over` holds whatever the **last-iterated ladder line** produced.
The headline-line probability is computed separately as `p_over_headline`
(`:336`), and `projected_under_prob` is derived from it (`p_under`, `:347`). But
the row writes:

```python
"projected_over_prob":  round(p_over, 4) ...          # :408  STALE
"projected_under_prob": round(p_under, 4) ...         # :409  headline-correct
```

So the board's **over** probability corresponds to an arbitrary line (dict
insertion order), while **under** corresponds to the headline line — the two no
longer sum to 1, and the over prob is essentially non-deterministic w.r.t. record
order. Reasoning agents read this board as ground truth.

**Fix.** Use `p_over_headline` at `:408` (and when it's `None`, blank both
`projected_over_prob` and `projected_under_prob` together).

---

## F3 — [MED] `game_totals` edge/actionable ignore push mass; derived `push_prob` is inert

**Where.** `game_totals.py:207-213` (`compute_side_edge`), `:353-360` (push
derivation), `:366-380` (`actionable`).

**Problem.** Fix E1 now derives `push_prob` for integer lines
(`derive_push_prob`, `:226-234` — logic is correct: `P(Over L-0.5) − P(Over L+0.5) = P(land on L)`).
But the derived value is never used in the math: `compute_side_edge` is
`p_side − implied` with no push term, and `build_game_totals` never calls
`sizing.compute_sizing`. Integer lines are hard-forced `actionable="false"`
(`:376 not push_blocked`), so the derived `push_prob` only lands in a display
column. Meanwhile the `edge_pct` shown for an integer line comes from a two-way
devig that structurally can't hold push mass, so it's **push-contaminated and
still displayed** — the reasoning agent can act on a misleading edge for a line
the board itself flags as push-capable.

**Fix (matches the plan's deferred item).** Either blank `edge_pct` for
push-capable lines, or compute a push-aware edge/units via
`sizing.compute_sizing(..., push_prob=...)` — the same helper `pack.py:432`
already uses for candidate sizing — before deciding `actionable`.

---

## F4 — [MED] `cards_html` renders untrusted scraped strings without escaping

**Where.** `cards_html.py:71-113` (JS `innerHTML` paths), `cards_html.py:139-176`
(server-side f-string), `:147` (`skew.reason`), `:150` (only the JSON blob is
guarded).

**Problem.** Payload fields flow straight into the DOM via `innerHTML` template
literals — `c.player`, `c.matchup`, `c.market`, `sv.insights[].text`, book names
— and into the page via Python f-strings (`{league}`, `{gen}`, `skew.reason`).
Only the embedded JSON is hardened (`.replace("</","<\\/")`, `:150`). Player /
matchup / insight strings come from scraped external feeds; a crafted value like
`<img src=x onerror=...>` executes when the card is opened. Impact is bounded (a
local `file://` artifact, not a served page) but non-trivial — `file://` context
can read local files and exfiltrate.

**Fix.** HTML-escape every interpolated text node: a small `esc()` helper for the
JS `innerHTML` paths (or switch those to `textContent`), and `html.escape(...)`
for the server-side f-string values.

---

## F5 — [LOW/MED] `reasoning.py` duplicates `runner_common` helpers (this is how F1 happened)

**Where.** `reasoning.py:30-43` (re-implements `rc.extract_yaml_request_hash`),
`reasoning.py:187-196` (re-implements `rc.atomic_write`), `reasoning.py:46-67`
(re-implements a non-filtering `validate_candidates`).

**Problem.** Prompt A predates the `runner_common` consolidation and still
carries its own copies of three shared helpers. The divergence is exactly why the
Fix A re-filter reached C/D but not A. Drift risk is ongoing.

**Fix.** Route A through `rc.validate_candidates`, `rc.extract_yaml_request_hash`,
and `rc.atomic_write`; delete the local copies.

---

## F6 — [LOW/MED] Manual/local-synthesis report skips the lock filter and writes non-atomically

**Where.** `run_desk.produce_manual_betting_report:182-220` (esp. `:212-217`
quoting `rows[:10]` from `_load_candidates:174-179`; `:219` `outp.write_text`).

**Problem.** The human-facing fallback report quotes candidates straight from
`candidates.csv` with no `drop_locked_events`, so it can surface locked events
even after Fix A cleaned the four model prompts. The write is also non-atomic,
inconsistent with `rc.atomic_write` used everywhere else in the desk.

**Fix.** Apply the same reasoning-time lock filter before quoting, and switch to
`rc.atomic_write`.

---

## F7 — [LOW] `sizing.compute_sizing` doesn't validate `push_prob + model_prob ≤ 1`

**Where.** `sizing.py:68-70`.

**Problem.** `p_lose = 1 − p_win − push_prob`; pathological inputs (e.g.
`model_prob=0.6, push_prob=0.6`) yield a negative `p_lose` that still passes the
`(p_win + p_lose) <= 0` guard (`:70`) and inflates `edge_pct` / Kelly. Callers
currently supply sane inputs, so this is latent, but it's an unguarded boundary.

**Fix.** Clamp/validate `push_prob` and assert `p_win + push_prob <= 1` (or return
the null-sizing branch) before computing edge.

---

## Minor notes (NOTE)

- `runner_common.py:83-84,115` — `import logging`, `from datetime import datetime`,
  and `import io` sit mid-file / inside a function; move to the top per PEP 8.
- `sizing.py:11` / `:82` — the field named `edge_pct` is EV-in-units
  (`p_win·b − p_lose`), not a percentage; the name misleads readers of the CSV.
- `game_totals.py:433-434` — the sort key substitutes `-1.0` for missing
  `edge_pct`, so a row with a real edge below −1.0 sorts after data-less rows;
  cosmetic only.

---

## Suggested sequencing

1. **F1** — one-line-ish swap to `rc.validate_candidates` (closes the remaining
   Fix A hole; highest leverage) — folds F5 in.
2. **F2** — use `p_over_headline` (single-line correctness fix to the board).
3. **F4** — escape HTML in `cards_html` (contained but real).
4. **F3** — push-aware edge/actionable for integer totals (revisits the plan's
   deferred E-follow-up; needs a small design call).
5. **F6 / F7** — filter + atomic manual report; sizing input guard.

All are additive/low-risk except F3, which changes what shows as `actionable`.
`tests/test_game_totals.py`, `tests/test_pack.py`, `tests/test_sizing.py`, and the
per-runner tests already exist — extend, don't duplicate.
