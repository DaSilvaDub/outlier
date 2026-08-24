# AI Research Desk — Daily Betting Guide Runbook

**Purpose:** Turn ChatGPT, Gemini, and Claude into a research desk layered on top of the Outlier pipeline. The pipeline supplies the numeric edge (EV board + signal board at `market_id` grain, multi-book odds, publicMoney, injuries, matchup) **and the bet sizing**. The three models add late news, situational context, and an independent cross-check, then produce one consolidated daily guide.

**Mode:** Hybrid (automated reasoning + manual research paste). **Output:** Full guide (singles, props, SGP/parlays as candidates, stand-downs, unit sizing).
**Core discipline:** Two rule modes (see §2a). *Reasoning* passes use the pack only. *Research* passes may use the web but may never invent or update a betting line — every finding ties back to a quoted `market_id` + line/price from the pack. Sizing is computed by the pipeline, never by a model.

---

## 0. Mental model — who does what

| Model | Role | Why |
|---|---|---|
| **GPT-5.6 Sol** | Automated stress-tester (A, pack-only) | OpenAI's flagship model; unmatched at pure logic, probability math, and strictly following complex rule constraints without hallucinating. |
| **Gemini 3.1 Pro** | Wide scanner (B, web) | Google's top model for complex reasoning with a massive context window and native Google Search integration for broad sweeps. |
| **Grok 4.5** | Deep per-game news (C, web) | xAI's flagship; provides real-time access to the X firehose for breaking sports news, lineup changes, and late scratches. |
| **Claude Fable 5** | Red-team (D, pack-only) & Synthesizer (E) | Anthropic's most capable model; highest calibration for finding logical flaws, plus the most professional writing style for the final guide. |

**Key principle:** the desk's job is *as much about killing bad pipeline cards as confirming good ones*. A stand-down is a win. Consensus across models is a filter, not proof — weight independent **sourced information** (deep-research news) above **opinion**.

---

## 1. Daily sequence (hybrid)

Times are relative to the **first market lock** of the slate (e.g. first pitch / tip-off).

| When | Step | Tool |
|---|---|---|
| **T‑180 min** | Run pipeline → produce today's EV/signal cards **with sizing fields** (§4). Build the **briefing pack** (§2). | Pipeline + export script |
| **T‑170** | Kick off both grounded research passes: Gemini wide-scan (Prompt B) and injury/lineup research (Prompt C). | Gemini 3.1 Pro + Grok 4.5 |
| **T‑160** | While they run, execute **Prompt A** through `daily_job --analysis-profile openai`. Paste **Prompt D** into Claude. | GPT-5.6 Sol + Claude Fable 5 |
| **T‑140** | Collect all four structured outputs (A–D). | — |
| **T‑130** | Paste **Prompt E** (synthesis) + the four outputs into Claude → **final guide**. | Claude |
| **T‑30** | **Line re-check / kill pass** against the §4 stale-line kill criteria. | Pipeline / book |
| Post-slate | Log results — **plays AND stand-downs/fades** — and grade each model + the pipeline (§5). | Calibration log |

> Deep Research is a **pre-slate** tool (latency 5–15 min). Not for live in-game.

---

## 2. Briefing-pack spec (what the export script emits)

The export reads the day's pipeline output and writes a dated folder `packs/YYYY-MM-DD/` with the files below.

### 2a. Rules by role — paste at the top of EVERY pack file and prompt

```
SLATE: <sport> <date>   AS_OF: <ISO timestamp, local + UTC>

REASONING PASSES (A, D):
- Use this pack ONLY. Do not use memory or the web.
- Never invent or recall odds/lines. Every verdict quotes the exact market_id + line/price from the pack.
- If you need info not in the pack, list it under NEEDS — do not guess.
- Flag any edge that looks like a data artifact (stale line, injury already priced, wrong side of a key number).

RESEARCH PASSES (B, C):
- You MAY use current web sources (last 24h).
- Do NOT invent, quote, or update any betting line/price. The pack's lines are the only lines.
- Tie every finding back to a quoted market_id + line/price from the pack.
- Every news item must carry: claim, source name, SOURCE TIER (see §2e), and timestamp.
```

### 2b. `candidates.csv` — the shortlist (auto-read by Prompts A and C)
One row per `market_id` on the EV or signal board. Columns (identity + sizing fully anchored so props and alt lines can't be confused):
```
sport, event_id, _event_starts_at, market_id, outcome_id, market_type, player_id,
matchup, team, team_name, opponent, opp_name, home_away, market_label, selection,
line, priced_line, price, decimal_price, book, as_of, model_prob, model_prob_source,
market_consensus_prob, independent_model_prob, final_blended_prob, push_prob, implied_prob,
edge_pct, kelly_025_units, max_units, recommended_units_pre_news, sizing_flags,
data_quality_flags, board, signal_flags, hit_rate_component, insight_component,
movement_component, orf_component, actionable, outlier_ev_pct, outlier_kelly_pct,
local_ev_pct, local_kelly_pct, line_open, line_now, public_money_pct, money_pct,
injury_flags, research_leverage, source_timestamps
```
This is the canonical, complete column list — the export header must match it exactly (no extra, no missing).
- `price` = American odds (display); `decimal_price` = same price in decimal form, the value the sizing formula uses (§4). Source the decimal book price from the normalized `ev_records` (`book_decimal_odds`); never derive the bet price from de-vig/fair odds.
- `selection` = the exact side/outcome (e.g. `HOME -1.5`, `Player X Over 5.5 K`), so alternate lines never collide.
- `push_prob` = pipeline's probability the bet pushes (0 for no-push markets — moneylines, half-point lines, run line ±1.5). For any push-capable **whole-number** line (spreads, totals, **and integer-result player/team props**) where no real push probability exists yet, the row is **sizing-ineligible** (units empty) rather than sized with `push_prob=0`, which would mis-size it. See §4.
- `model_prob` = backward-compatible sizing probability. It is currently market-derived (`1.0 / devig_decimal` or the totals ladder), not an independently estimated truth.
- `market_consensus_prob` = explicit market-derived no-vig probability; `independent_model_prob` stays blank until a real independent model exists; `final_blended_prob` is the probability actually evaluated for calibration and currently equals market consensus.
- `push_prob` = The probability of exactly hitting the number (for whole-number lines).
- `edge_pct` = Our computed expected value based on price and model_prob (e.g. 0.052 = 5.2% edge).
- `kelly_025_units` / `max_units` = Sizing recommendations. The desk treats these as hard upper bounds.
- `sizing_flags` = Operational notes on sizing (e.g. `push_capable_no_prob`, `ev_line_fallback`).
- `outlier_ev_pct` / `outlier_kelly_pct` = Outlier's native EV% and Kelly% for the side (pipeline cross-check).
- `local_ev_pct` / `local_kelly_pct` = Locally synthesized EV% and Kelly% when Outlier native EV is missing.
- `research_leverage` (low/med/high) = how much an unknown (weather, lineup, starter, rest) could move the number — used to prioritize Prompt C (§2d).

Cap with **board quotas** so signal coverage is never starved by EV volume: take the top `top_ev_n` Board-A (EV) cards by `rank_value` desc **and** the top `top_signal_n` Board-B (signal) cards by `rank_value` desc (defaults 40 / 10 ≈ 50 total; the EV quota is deliberately wider than the number of playable cards so the enforced portfolio caps in `config/portfolio_risk.json` do the pruning instead of a pre-risk truncation), union them. One row per card, emitted from the card's `headline_side`. **Never dump the whole slate.**

### 2c. `briefing.md` — the master pack (read by Gemini B/C + Claude D)
Header = §2a rules. Body:
- **Top EV cards** (capped to N, default 15): identity per §2b, model fair value, `edge_pct`, `recommended_units_pre_news`, edge source tag.
- **Top signal cards** (capped to N): signal type (sharp/steam/reverse-line-move), `public_money_pct`, money vs bet %, line move since open.
- **Key injuries / status** per game (from injuries endpoint), with timestamp.
- **Notable line moves** since open.
- **Slate index**: every game + first lock time.

### 2d. `dossiers/<game>.md` — per-game review briefs (for manual escalation)
One short file per game: teams, time, the `market_id`s in play, current pipeline lines, and the open questions (auto-selected by sport from §2f). 
Prompt C now reads the complete capped candidates ledger. Use dossiers for manual follow-up,
prioritized by **`research_leverage`, not raw edge** — a small edge on a weather-sensitive
total or a game with an unconfirmed starter deserves review more than a larger but stable
moneyline edge.

### 2e. Source tiers (used by B, C, and the synthesis override rule)
```
TIER 1 (authoritative — can override a pick): official team injury report, confirmed lineup card /
        starting-pitcher confirmation, league transaction wire, NWS/official weather, official umpire assignment.
TIER 2 (credible — can override Tier 3 + opinion): established beat reporters, official club channels, reputable injury insiders.
TIER 3 (weak — cannot create or kill a play on its own): aggregators, model blurbs, betting-content sites, unsourced rumor.
```
Override rule: only **Tier 1–2, sourced + timestamped** news may flip a pick. Tier 3 may only lower confidence — never create or kill a play by itself.

### 2f. Sport-specific question banks (auto-inserted into each dossier + research prompts)

**MLB** — each answer needs source + tier + timestamp:
- **Starters:** both confirmed SPs, days rest, recent form, pitch-count limit / opener or bullpen game.
- **Bullpen:** who threw the last 1–2 days, closer availability, gassed pen.
- **Lineup:** posted lineup card, key bats in/out, platoon/handedness edge, regulars resting (day-after-night, getaway day).
- **Weather/park:** wind speed + direction (out vs in), temp, rain-delay risk, roof open/closed, hitter vs pitcher park, altitude (Coors).
- **Umpire:** home-plate ump strike-zone tendency (tight/wide → totals & K props).
- *Market types in play:* full game, **F5 (first 5)**, run line, total, **NRFI/YRFI**, strikeout props, H+R+RBI.

**WNBA** — each answer needs source + tier + timestamp:
- **Availability:** injury report status (out/quest/prob), load management, rest decisions.
- **Lineup/rotation:** confirmed starters, any rotation change, minutes restriction returning from injury.
- **Schedule/fatigue:** back-to-back, travel/time-zone, schedule density.
- **Usage shift:** if a star is out, who absorbs usage/shots → which player-prop **overs** light up.
- **Game script:** pace matchup, blowout risk (→ star minutes capped → prop **unders**), foul-trouble tendencies.
- *Market types in play:* spread, total, player points/reb/ast, **PRA**, 3PM, alt lines.

---

## 3. The five prompt templates

> Every pass is sent `pack.ROLE_BLOCK` as its system instruction, a
> `PACK IDENTITY` header (`pack_date` plus the three pack hashes), and its pack
> data. Each prompt file is the whole user-side contract for that pass.

**All five passes emit a structured envelope, not prose.** A, B and D emit a
*verdict* envelope (`BET` / `PASS` / `STAND_DOWN` with a stake), C emits a
*finding* envelope (`CONFIRMS` / `CONTRADICTS` / `NEUTRAL`, no stake), and E
emits a *reconciliation* envelope that may only narrow what A/D/B already
proposed. `verdict_gate.validate_envelope` checks every record against
`pack_index`, and the Markdown a human reads is rendered from the validated
envelope — never written by the model. Schemas live in `verdicts.py`; the
per-code rules are in `docs/plans/2026-08-12-structured-ai-verdicts.md`.

Two failure modes are worth knowing before editing any prompt:

* An envelope whose `pack_date` or three `*_sha256` values do not match the
  index is rejected **before any verdict is read**. Every runner therefore sends
  `rc.build_pack_identity_block(...)` and every prompt tells the model to echo it
  verbatim.
* One tampered identity field (`market_id`, `outcome_id`, `stream`, `selection`,
  `line`, `price`, `book`) on *one* record — including a `PASS` record — fails
  the whole pass. Judgement-class violations only fail the pass when more than
  `reject_fail_ratio` of the attempted `BET`s are rejected.

### Prompt A — reasoning stress-test (input: `candidates.csv` + totals) — PACK-ONLY
[prompts/A.md](prompts/A.md) — verdict envelope. No web tools: the prompt
forbids external research outright, and an `"external"` evidence item from this
pass is a fabrication. Run with `python -m outlier_scrapers.reasoning --date
YYYY-MM-DD`, or append `--run-reasoning` to the daily job. Fixed configuration:
`reasoning.effort="xhigh"`, `store=False`, OpenAI strict `json_schema` output.
Output is `packs/YYYY-MM-DD/chatgpt_a.md` plus `verdicts/A/<publication_id>`.
Paid API usage; requires `OPENAI_API_KEY`.

### Prompt B — wide-scan research (input: `briefing.md` + `candidates.csv` + totals) — WEB ALLOWED
[prompts/B.md](prompts/B.md) — verdict envelope. Google-Search-grounded (a single
grounded generation pass, not the multi-step Deep Research UI product).
Structured output is best-effort: on a grounded-config rejection the runner falls
back to prompt-instructed JSON, which is why B's prompt carries the envelope
shape inline and insists on JSON-only output. Every `"external"` evidence item
needs `source`, `tier` and `timestamp`; injury and lineup claims additionally
need a `player_id` and a timestamp inside 24 hours. Run with
`python -m outlier_scrapers.gemini_research --date YYYY-MM-DD`. Requires
`GEMINI_API_KEY`.

### Prompt C — injury / lineup research (input: `briefing.md` + `candidates.csv`) — WEB ALLOWED
[prompts/C.md](prompts/C.md) — finding envelope, JSON only. Narrow scope:
availability, lineups, starters, rest, usage. `source_timestamp` must be the
source's own publication time and must fall between `pack_date − 2d` and
`pack_date + 1d`. C never asserts a stake, and a C finding alone can never
justify a bet downstream. Run with `python -m outlier_scrapers.c_research --date
YYYY-MM-DD`. Requires `GEMINI_API_KEY`.

### Prompt D — red-team reasoning (input: `candidates.csv` + totals) — PACK-ONLY
[prompts/D.md](prompts/D.md) — verdict envelope, emitted through a forced
`emit_verdicts` tool call. Same gates as A, opposite posture: D reaches its own
independent read and must record the strongest argument *against* every bet it
backs in `contradictions`. A `BET` with an unanswerable `material` contradiction
is a `PASS`. Run with `python -m outlier_scrapers.claude_reasoning --date
YYYY-MM-DD`. Requires `ANTHROPIC_API_KEY`.

### Prompt E — reconciliation → final guide (input: validated A/D/B/C envelopes) — VALIDATION FIRST
[prompts/E.md](prompts/E.md) — reconciliation envelope. E consumes the upstream
passes' **validated envelopes**, never their Markdown, and cites each record by
`(pass, publication_id, record_id)`. It may only narrow: no new market, no new
number, no stake above the minimum upstream stake for that `outcome_id`. Injury
language in `narrative` must cite a validated B/C finding. Run with
`python -m outlier_scrapers.claude_synthesis --date YYYY-MM-DD`; A, B and D must
have published first. C is optional and included when present. Requires
`ANTHROPIC_API_KEY`.

### Master Cards — the human paste lane (not part of the automated desk)
[prompts/Master_Cards_Analysis.md](prompts/Master_Cards_Analysis.md) is the
Markdown-report prompt the manual export path pastes into a chat model. It is
deliberately *not* `A.md`: the automated Pass A emits JSON, while this lane wants
a written card. `generate_prompts.py` loads it by name and still falls back to
`A.md` if it is missing.

---

## 4. Final-guide output schema + deterministic sizing

```
DATE / AS-OF / first-lock time

A. SINGLES
   market_id | event_id | selection @ line (book) | edge% | confidence | units (≤ pipeline) | rationale | news support {tier, source, ts}
B. PROPS
   same columns; player_id + selection mandatory so alt lines don't collide
C. SGP / PARLAYS — CANDIDATES ONLY (no units)
   legs (market_ids) | sgp_correlation_rationale (from pack) | book combined price (from pack) | note
   — becomes a firm play (with units) ONLY if the pack emits ALL THREE: `sgp_recommended_units_pre_news`
     (needs a parlay-level model probability) + the book's combined price + `sgp_correlation_rationale`.
     Missing any one → stays a candidate.
D. STAND-DOWNS
   market_id | what the pipeline said | why we're passing/fading (artifact / contradicting Tier 1–2 news)
E. AGREE/DISAGREE MATRIX
   market_id × {ChatGPT-A, Gemini-B, ChatGPT-C, Claude-D}
F. WATCH / NEEDS
   open kill-triggers and unresolved unknowns to recheck at T-30
```

### Sizing — computed by the pipeline, not the model
The export emits the final `*_units` fields per row so no model ever does Kelly arithmetic. The formula below is the pipeline's internal reference, not something a model runs.
```
b      = decimal_price − 1                      # decimal_price is emitted explicitly (§2b)
p_win  = model_prob
p_push = push_prob                              # 0 for no-push markets (ML, runline 1.5, .5 totals)
p_lose = 1 − p_win − p_push

# Optimal Kelly with pushes (pushes return stake → 0 log-growth):
#   maximize  p_win·ln(1+f·b) + p_lose·ln(1−f)   →
full_kelly = (b·p_win − p_lose) / ( b · (p_win + p_lose) )
# Normalizes over the non-push mass (p_win + p_lose = 1 − p_push).
# Reduces to the no-push binary case  p_win − p_lose/b  when p_push = 0.

kelly_025_units            = round_to_half( 0.25 · full_kelly · UNIT_BANKROLL )
max_units                  = hard per-play cap (default 3u)
recommended_units_pre_news = min( kelly_025_units, max_units )  if edge_pct ≥ MIN_EDGE else 0
```
Claude may only **downgrade** `recommended_units_pre_news` (news/confidence), never raise it. A row is **sizing-ineligible** (units empty → reasoning/news only, no stake) when any holds: `model_prob` is missing; OR the market is a push-capable whole-number line (spread, total, or integer-result player/team prop) and the pipeline has no real `push_prob` for it yet (sizing it with `push_prob=0` would mis-size). Half-point lines, moneylines, and run line ±1.5 are no-push (`push_prob=0`, sizing-eligible). **The model never sees the formula in its prompt — it only reads the emitted units.**

### Stale-line kill criteria (the T‑30 pass) — concrete
Kill or re-stake a play if any holds at T‑30:
- **No edge left:** current price has moved to/through model fair value (recomputed `edge_pct` < `MIN_EDGE`, default 2%).
- **Key number crossed against you:** MLB totals through 7/8/9, run line through 1.5; WNBA spread through 2/3/5/7, total through a whole-number key — re-evaluate, default kill.
- **Prop line drift:** prop line moved ≥ Y stat units (MLB ≥0.5 K / total bases; WNBA ≥1.0 pts, ≥0.5 reb/ast) → re-price before betting.
- **Tier 1–2 contradiction:** late scratch, lineup/starter change, weather flip → kill.
(Thresholds default here but should be emitted per `market_type` by the pipeline.)

---

## 5. Calibration log (so the desk improves)

Log **every play AND every stand-down/fade** — that's how you learn whether the desk is killing good bets or correctly avoiding bad ones. Pack generation now captures the full pre-ranking opportunity set and seeds the permanent SQLite decision ledger automatically. Fill/import the pack-local `decisions.csv`, then import the post-slate settlement CSV:
```
python -m outlier_scrapers.feedback decisions --input packs/YYYY-MM-DD/decisions.csv
python -m outlier_scrapers.feedback settle --input settlements_YYYY-MM-DD.csv
python -m outlier_scrapers.feedback report
```
The permanent schemas, probability/edge semantics, identifier rules, CLV sign convention, and report files are documented in `docs/feedback-loop.md`. For stand-downs/fades, grade the **would-have** result + CLV — did avoiding it save or cost you? Track weekly: **CLV first** (did you beat the close, on plays and on the close of things you passed?), then ROI and hit rate. Grade each model and the pipeline separately so you learn whose calls to trust on which market types.

---

## 6. Guardrails (built into the flow)

- **Reasoning vs research split** → pack-only for A/D; web for B/C but never inventing/updating lines (§2a).
- **Hard provenance** → Prompt E keeps a claim only with pack OR sourced B/C support; Tier 1–2 is required to *override* (flip/kill) a pick, Tier 3 can only soften. Discards the rest before sizing.
- **Deterministic sizing** → Kelly units come from the pipeline; models may only downgrade. No LLM arithmetic on stakes.
- **Identity anchoring** → every row carries event_id, market_id, market_type, player_id, selection, line, price, book, as_of — props/alts can't be confused on paste.
- **Source quality** → Tier 1–2 only can flip a pick; Tier 3 can only soften.
- **SGP safety** → parlays stay candidate-only unless the pack emits all three: `sgp_recommended_units_pre_news` + book combined price + `sgp_correlation_rationale`.
- **Context limits** → pre-rank and cap candidates; never paste the full slate.
- **CLV decay** → T‑30 kill criteria are explicit thresholds, not vibes.
- **ToS/privacy** → proprietary pipeline output leaves your machine into 3rd-party models; fine for personal use, just noted.
- **Bankroll** → fractional Kelly + per-play cap; decision support, not a guarantee.

---

## 7. Implementation status and remaining work

1. **Implemented:** pack export writes `candidates.csv`, `briefing.md`, and dossiers under `packs/YYYY-MM-DD/`.
2. **Implemented:** Prompt A lives in `prompts/A.md` and can run through the GPT-5.5 xhigh Responses API with hash-based caching.
3. **Still manual:** Gemini B, ChatGPT Deep Research C, Claude D, and Claude synthesis E.
4. **Implemented:** permanent market-snapshot, decision, and settlement ledgers plus ROI/CLV/calibration/segment/model reports (`outlier_scrapers.feedback`).
5. **Still external:** an authoritative result/closing-line feed, structured A/B/D/E verdict sidecars, learned Board B weights, and a genuinely independent probability model.
