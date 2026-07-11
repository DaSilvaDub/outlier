# Betting Reports — Round 2 Weakness Fix Plan (Code-Accurate)

> Follow-up to `2026-07-11-betting-reports-weakness-fix-plan.md` (Fixes A–E,
> merged in PR #5). Sourced from a cross-report scan of the four generated
> reports in `Desktop/BETTING REPORTS/` (`CHAT`, `CLAUDE`, `GROK`, `GEMINI`)
> for the 2026-07-11 slate. Every finding is verified against the pipeline
> code; each carries `file:line` anchors and an honest scope verdict.

## Critical scope fact (read first)

**The four reports are generated *externally*, not by this pipeline.** Each
report is produced by pasting a pack (`claude_pack_*.txt`, `grok_pack_*.txt`, an
uploaded Google-Drive pack, etc.) into a chat app. Confirmed in code:
`gemini_research.py:main` only calls `client.models.generate_content`
(`gemini_research.py:54`) for a text research pass — it does **not** emit the
Tailwind/Chart.js HTML seen in `GEMINI_711_betting_report.html`.

**Consequence:** the pipeline cannot lint or rewrite report output it never
produced. The durable levers are (1) **harden the pack** so models have less room
to err, and (2) **strengthen the embedded prompt** (`ROLE_BLOCK`). Findings that
are purely about external model output are marked **[External]** and reframed
accordingly — do not scope pipeline code changes to them.

## Finding → fix summary

| # | Finding | Severity | Pipeline-addressable? | Primary change |
|---|---|---|---|---|
| 1 | Stale-line "phantom edge" (Bonner +13.3%) | 🔴 High | **Yes** | `pack.py build_row` — stale-line edge gate |
| 2 | `injury_flags` empty → models freelance | 🔴 High | **Yes (investigate)** | `games.py` injury fetch chain |
| 3 | Model recommends high-variance vs stated rule | 🟠 Med | Partial **[External]** | Prompt + optional linter |
| 4 | Hallucinated pitchers (e.g. "Shane Drohan") | 🟠 Med | Partial **[External]** | Root cause = #2 |
| 5 | Report HTML not self-contained (4 CDNs) | 🟡 Low | **No [External]** | N/A unless in-pipeline generator built |
| 6 | No same-game correlation handling | 🟡 Low | **Yes** | `pack.py` correlation tag |

---

## Fix 1 — Stale-line "phantom edge" gate 🔴

**Evidence.** `f3891aa671...` DeWanna Bonner Pts OVER 10.5 (+111), pack
`edge_pct` **+13.31%**. GEMINI ranked it #1 at 3.0u; CLAUDE sized 3.0u. GROK and
CHAT **faded it as a model artifact**: line moved **9.5 → 10.5** (against the
over), flags `reverse_line_movement` + `thin_liquidity`, season avg ~9.0 PPG
(below the line). The edge was almost certainly devigged against a stale basis
while `line_now` drifted worse.

**Code reality.**
- `model_prob = 1.0 / devig` from the EV record (`pack.py:426-428`); `edge_pct`
  from `compute_sizing(...)` (`pack.py:432-434`).
- `line_open`/`line_now` come from movement (`pack.py:398-399`).
- `reverse_line_movement` / `thin_liquidity` / `high_vig` are already computed in
  `cards.py` (`_board_a_flags` `cards.py:782-800`, `_board_b_flags`
  `cards.py:806-811`) and arrive as `card.get("flags")` (`pack.py:461`).
- There is already a precedent gate — `ev_line_fallback:priced_at=…` +
  `priced_line` when EV was priced at a different line (`pack.py:462-467`). This
  is the same *class* of protection; it just doesn't cover the RLM+thin+adverse-
  drift case.

**Change.** In `build_row`, after `edge_pct` is set (`pack.py:434`), compute the
line drift direction relative to the bet side and, when the move is **adverse**
AND both `reverse_line_movement` and `thin_liquidity` are present, append an
`edge_suspect_stale_line` data-quality flag and zero (or withhold)
`recommended_units_pre_news`. Keep `edge_pct` visible but no longer treat the row
as a confident stake.

- Adverse = for an OVER, `line_now > line_open`; for an UNDER, `line_now <
  line_open` (the number moved through the bet).
- **[MODIFY]** `outlier_scrapers/pack.py` (`build_row`, after `pack.py:434`; reuse
  the flag list already assembled at `pack.py:461-473`).

**Tests.** `tests/test_pack.py` — a row with RLM + thin + adverse drift gets
`edge_suspect_stale_line` and withheld units; a clean row with the same edge is
untouched; a favorable drift does **not** trip the gate.

---

## Fix 2 — `injury_flags` are empty (investigate the existing chain) 🔴

**Evidence.** CHAT: *"Some qualitative `injury_flags` fields are blank."* Because
the pack ships no authoritative injuries, every model freelances and they
disagree — A'ja Wilson (out per CHAT / healthy per others), Natasha Mack (Out in
GEMINI **and** GROK), Angel Reese (only CLAUDE). This is the root cause behind the
hallucinations in Fix 4.

**Code reality — the plumbing already exists end-to-end:**
1. `games.py:132` fetches per-team injuries via `client.fetch_team_injuries`,
   stores them in `injuries_by_team`, and merges into `event_payload["injuries"]`
   (`games.py:133-141`); fetch failures are swallowed to `_record_error`
   (`games.py:135`).
2. `normalize_games` keys `context.teams` by team id (per the comment at
   `games.py:139`).
3. `build_injuries` reads `context.teams[tid].injuries` for each event's
   home/away team ids and joins them (`pack.py:195-216`).
4. `build_row` writes `injury_flags = injuries.get(str(event_id), "")`
   (`pack.py:395`).

So this is a **"why is the output empty"** investigation (like Fix B), not
missing code. Likely causes to check, in order:
- `fetch_team_injuries` failing/403 (silently recorded at `games.py:135`) — check
  the games status report for injury fetch errors.
- Key mismatch: `context.teams` keyed by a team id that doesn't match
  `event.home_team_id`/`away_team_id` used in `build_injuries` (`pack.py:205`).
- Empty upstream feed (endpoint returns no `players`).

**Change.** Diagnose which link is empty, then fix that link only. Add an
`injuries_fetch_error` surfacing in the games status report so a silent failure
is visible (mirrors the line-movement `error_market_ids` pattern from Fix B).

- **[INVESTIGATE/MODIFY]** `outlier_scrapers/games.py` (`export_games_for_league`
  injury fetch, `games.py:132-141`) and/or the `normalize_games` `context.teams`
  keying.

**Tests.** `tests/test_games.py` already stamps injury team ids
(`test_games_export_stamps_injury_team_id`) — extend so a fetched injury lands in
`context.teams[tid].injuries`; add a `tests/test_pack.py` case asserting
`injury_flags` is populated when the games payload carries team injuries.

---

## Fix 3 — House-rule compliance of recommendations 🟠 [External]

**Evidence.** GEMINI's own text claims a "strict low-variance doctrine," then its
Final Ledger is **4 of 6 high-variance**: Jewell Loyd **3PM** 3.0u (`daf85b...`),
Logan Gilbert **Hits Allowed** 2.5u (`f69926...`), plus Bonner and Sánchez. Every
other model excluded 3PM / hits-allowed by rule.

**Scope.** The pipeline does not generate this report, so it cannot reject it.
Fix D already added the variance taxonomy + "render only pack fields" rule to
`ROLE_BLOCK` (`pack.py:674` and the taxonomy lines). Remaining levers:
- **Prompt (low cost):** make the exclusion imperative and self-checkable — e.g.
  "Before emitting the final ledger, drop any 3PM / hits-allowed / total-bases /
  turnover selection; state the exclusion." Extend `ROLE_BLOCK`.
- **Optional tooling (new, out of core pipeline):** a standalone
  `report_lint` that parses a pasted report's market_ids against
  `candidates.csv` and flags any recommended row whose `market_type` is
  high-variance, `>= +150`, or locked. Reuses the market_id cross-check already
  proven in `c_research.validate_output` (`c_research.py:85`). Propose as a
  separate opt-in utility, not a pipeline gate.

- **[MODIFY]** `outlier_scrapers/pack.py` `ROLE_BLOCK`. **[OPTIONAL/NEW]**
  `outlier_scrapers/report_lint.py`.

**Tests.** For the optional linter only: a report recommending a 3PM market_id is
flagged; a compliant report passes.

---

## Fix 4 — Hallucinated pitchers/players 🟠 [External]

**Evidence.** GEMINI: *"Shane Drohan holds clear statistical edge"* (MIL @ PIT) —
absent from every other report and from any pack pitcher context; the Plotly
chart also invents pairings (e.g. "Mikolas (WSH)"). GROK independently
corroborates "Natasha Mack Out," which means the **original A–E plan's claim that
Mack was a GEMINI hallucination was likely wrong** — a caution against
suppressing data that is actually real.

**Scope.** Root cause is Fix 2: with authoritative injury/lineup data absent from
the pack, models invent it. The fix is **give them the data (Fix 2)** plus the
Fix-3 prompt hardening — not new report-side code. No separate change.

---

## Fix 5 — Report HTML not self-contained 🟡 [External]

**Evidence.** `GEMINI_711_betting_report.html` loads four external CDNs
(`cdn.tailwindcss.com`, `chart.js`, `plot.ly`, Google Fonts) — breaks offline,
supply-chain/injection risk for an archived artifact.

**Scope.** The pipeline does not emit this HTML, so there is nothing to change in
code today. **Only** relevant if a first-party in-pipeline report generator is
ever built — at which point the requirement is: inline all CSS/JS/assets, no
external hosts. Recorded here as a constraint for that hypothetical, not a task.

---

## Fix 6 — Same-game correlation tag 🟡

**Evidence.** CHAT: *"No cross-market correlation matrix."* Several recs are
same-game — Mize + Sánchez (both PHI@DET), Gray-under + Aces −9.5 (both PHX@LVA).
The pack sizes each row independently via `recommended_units_pre_news`
(`pack.py:437`), so stacking correlated legs over-stakes real risk.

**Code reality.** Every row already carries `event_id` (`pack.py:367`), which *is*
the same-game key — it's just not surfaced as a correlation signal, and
`ROLE_BLOCK` doesn't tell the desk to discount stacked same-event legs.

**Change (low effort).** Either (a) add a `correlation_group` column to
`CANDIDATES_HEADER` set to the `event_id` (explicit, machine-usable downstream),
or (b) at minimum add a `ROLE_BLOCK` instruction: "rows sharing an `event_id` are
correlated; do not size stacked same-event legs as independent." Prefer (a) +
(b).

- **[MODIFY]** `outlier_scrapers/pack.py` (`CANDIDATES_HEADER` + `build_row`
  assignment; `ROLE_BLOCK`). Note: adding a header column is a **schema change**
  — same regeneration caveat as Fix A's `_event_starts_at` (old packs fail the
  strict header check).

**Tests.** `tests/test_pack.py` — `correlation_group == event_id`; two rows in the
same event share the group.

---

## Sequencing

1. **Fix 2** — authoritative injuries (root cause of the hallucination cluster;
   unblocks the most model divergence). Investigation-first.
2. **Fix 1** — stale-line edge gate (prevents the worst confident-but-wrong
   stakes).
3. **Fix 6** — correlation tag (cheap; pair the schema bump with any other
   header change to amortize the regeneration cost).
4. **Fix 3** — prompt hardening now; optional linter later.
5. **Fixes 4 & 5** — no standalone work (4 folds into 2; 5 is a future-constraint
   note only).

## Branch & review discipline

Product code → one **feature branch**, PR to `master`, no direct commits.
`pytest tests/` (note: `tests/test_gemini_research.py::test_missing_key_on_cache_miss`
hangs pre-existing; a separate task is already tracking it — deselect it when
running the suite locally).

## Status

> [!NOTE]
> Findings verified against the code. Highest-leverage, fully in-scope work is
> **Fix 2** and **Fix 1**. Fixes 3–5 are wholly or partly about externally
> generated reports and are scoped accordingly. Awaiting go-ahead to implement,
> or to narrow to a subset.
