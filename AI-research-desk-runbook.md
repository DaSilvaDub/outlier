# AI Research Desk — Daily Betting Guide Runbook

**Purpose:** Turn ChatGPT, Gemini, and Claude into a research desk layered on top of the Outlier pipeline. The pipeline supplies the numeric edge (EV board + signal board at `market_id` grain, multi-book odds, publicMoney, injuries, matchup) **and the bet sizing**. The three models add late news, situational context, and an independent cross-check, then produce one consolidated daily guide.

**Mode:** Manual paste. **Output:** Full guide (singles, props, SGP/parlays as candidates, stand-downs, unit sizing).
**Core discipline:** Two rule modes (see §2a). *Reasoning* passes use the pack only. *Research* passes may use the web but may never invent or update a betting line — every finding ties back to a quoted `market_id` + line/price from the pack. Sizing is computed by the pipeline, never by a model.

---

## 0. Mental model — who does what

| Model | Role | Why |
|---|---|---|
| **ChatGPT** (o-series reasoning + Deep Research) | Stress-tester (A, pack-only) + per-game news (C, web) | Strong structured decomposition; good at "is this edge real or an artifact?" Deep Research pulls late injury/lineup/weather news per game. |
| **Gemini** (Pro + Deep Research) | Wide scanner (B, web) | Largest context — ingest the full pack + raw odds; broad multi-source web sweep across the whole slate. |
| **Claude** (extended thinking) | Synthesizer + red-team (D pack-only, E synthesis) | Calibrated uncertainty; runs the provenance/validation pass; writes the final guide. |

**Key principle:** the desk's job is *as much about killing bad pipeline cards as confirming good ones*. A stand-down is a win. Consensus across models is a filter, not proof — weight independent **sourced information** (deep-research news) above **opinion**.

---

## 1. Daily sequence (manual paste)

Times are relative to the **first market lock** of the slate (e.g. first pitch / tip-off).

| When | Step | Tool |
|---|---|---|
| **T‑180 min** | Run pipeline → produce today's EV/signal cards **with sizing fields** (§4). Build the **briefing pack** (§2). | Pipeline + export script |
| **T‑170** | Kick off **both** Deep Research jobs first (they take 5–15 min): Gemini wide-scan (Prompt B), ChatGPT per-game (Prompt C). | Gemini + ChatGPT web |
| **T‑160** | While they run, paste **Prompt A** (reasoning stress-test) into ChatGPT o-series and **Prompt D** (reasoning pass) into Claude. | ChatGPT + Claude |
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

### 2b. `candidates.csv` — the shortlist (paste into ChatGPT for Prompt A; Prompt C reads the per-game dossiers)
One row per `market_id` on the EV or signal board. Columns (identity + sizing fully anchored so props and alt lines can't be confused):
```
sport, event_id, market_id, market_type, player_id, selection, line, price, decimal_price, book, as_of,
model_prob, push_prob, implied_prob, edge_pct, kelly_025_units, max_units, recommended_units_pre_news,
outlier_ev_pct, outlier_kelly_pct,
line_open, line_now, public_money_pct, money_pct, injury_flags, research_leverage, source_timestamps
```
This is the canonical, complete column list — the export header must match it exactly (no extra, no missing).
- `price` = American odds (display); `decimal_price` = same price in decimal form, the value the sizing formula uses (§4). Source the decimal book price from the normalized `ev_records` (`book_decimal_odds`); never derive the bet price from de-vig/fair odds.
- `selection` = the exact side/outcome (e.g. `HOME -1.5`, `Player X Over 5.5 K`), so alternate lines never collide.
- `push_prob` = pipeline's probability the bet pushes (0 for no-push markets — moneylines, half-point lines, run line ±1.5). For push-capable **whole-number** spreads/totals where no real push probability exists yet, the row is **sizing-ineligible** (units empty) rather than sized with `push_prob=0`, which would mis-size it. See §4.
- `model_prob` / `implied_prob` / `edge_pct` and the three `*_units` fields are **computed by the pipeline** (see §4). Models read them; they never recompute sizing.
- `outlier_ev_pct` / `outlier_kelly_pct` = Outlier's own EV% and Kelly% for the side (pipeline cross-check, not used for our sizing) — lets the reasoning models compare our computed edge against the source's.
- `research_leverage` (low/med/high) = how much an unknown (weather, lineup, starter, rest) could move the number — used to prioritize Prompt C (§2d).

Cap to the top ~25 by combined EV+signal rank so it fits context. **Never dump the whole slate.**

### 2c. `briefing.md` — the master pack (paste into Gemini B + Claude D)
Header = §2a rules. Body:
- **Top EV cards** (capped to N, default 15): identity per §2b, model fair value, `edge_pct`, `recommended_units_pre_news`, edge source tag.
- **Top signal cards** (capped to N): signal type (sharp/steam/reverse-line-move), `public_money_pct`, money vs bet %, line move since open.
- **Key injuries / status** per game (from injuries endpoint), with timestamp.
- **Notable line moves** since open.
- **Slate index**: every game + first lock time.

### 2d. `dossiers/<game>.md` — per-game deep-research briefs (for ChatGPT Prompt C)
One short file per game: teams, time, the `market_id`s in play, current pipeline lines, and the open questions (auto-selected by sport from §2f). 
**Prioritization:** run Prompt C on **every** WNBA game (small slates). For MLB (large slates), cap by **`research_leverage`, not raw edge** — a small edge on a weather-sensitive total or a game with an unconfirmed starter deserves research more than a larger but stable moneyline edge.

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

> Paste the §2a rule block + the relevant pack section *above* each prompt. All ask for **structured output** so synthesis is mechanical.

### Prompt A — ChatGPT reasoning stress-test (input: `candidates.csv`) — PACK-ONLY
```
You are a sharp betting analyst. Below is a shortlist of markets my quant model flagged, with model probability, edge, and pipeline-computed unit sizing. Use this data ONLY — no web, no memory. Never invent odds.

For EACH market_id, return:
- verdict: BET | LEAN | PASS | FADE
- selection & the exact line/price it applies to (copy from the row)
- confidence: 1–5 (qualitative, separate from the model's numeric edge)
- edge_source_check: REAL / STALE_LINE / ALREADY_PRICED / KEY_NUMBER_WRONG_SIDE / UNCLEAR
- key_factors: ≤2 lines
- needs: any info not in the pack that would change the verdict

Output a markdown table, one row per market_id. End with the 3 strongest BETs and any FADEs where the model is likely wrong. Do not suggest unit sizes — those are fixed by the pipeline.
```

### Prompt B — Gemini wide-scan Deep Research (input: full `briefing.md`) — WEB ALLOWED
```
Deep research task. Here is today's slate with my model's flagged markets (as-of timestamp in header). Each game is tagged MLB or WNBA — use the matching question set:
- MLB: confirmed starters + days rest, bullpen availability, posted lineup, wind/temp/roof/park, home-plate umpire zone.
- WNBA: injury status + load management, confirmed starters/minutes limits, back-to-back/travel, usage shift if a star sits, pace & blowout risk.
Search current sources (last 24h). Do NOT invent, quote, or update any betting line — the pack's lines are the only lines.

For EACH game return:
- news items: each as { claim | source name | source tier (1/2/3 per the pack) | timestamp }
- impact: which market_id(s) it affects and direction, tied to the quoted pack line
- verdict vs my model: CONFIRMS / CONTRADICTS / NEUTRAL, one line why
Only report what you can source. Flag anything my as-of data likely missed.
```

### Prompt C — ChatGPT per-game Deep Research (input: one `dossiers/<game>.md`, top-leverage games) — WEB ALLOWED
```
Deep research on this single game. Answer the brief's open questions using current sources (last 24h). Each answer: { claim | source name | source tier (1/2/3) | timestamp }. Then for each listed market_id state CONFIRMS / CONTRADICTS / NEUTRAL to a bet at the quoted pack line, and why. Do NOT invent or update any line/price.
```

### Prompt D — Claude reasoning pass (input: `briefing.md`) — PACK-ONLY
```
Act as a calibrated, skeptical betting analyst. From the pack ONLY (no web, no memory), independently evaluate the top EV and signal cards. For each market_id give: verdict (BET/LEAN/PASS/FADE), the line it applies to, confidence 1–5, and the single biggest reason you might be WRONG. Separately list any card that looks like a data artifact and should be stood down. Prefer PASS to a forced lean. Do not propose unit sizes.
```

### Prompt E — Claude synthesis → final guide (input: outputs A + B + C + D) — VALIDATION FIRST
```
You are the head of the desk. Below are four analyses: two pack-only reasoning passes (A, D) and two web research passes (B, C). Build the final guide.

STEP 1 — VALIDATION PASS (do this before any recommendation):
- Keep a claim ONLY if it is supported by EITHER the pack OR a sourced B/C item. Discard everything else.
- Discard any play whose cited line/price does not EXACTLY match the pack.
- News may override opinion only if it is Tier 1–2, sourced, and timestamped (per §2e). Tier 3 can only lower confidence.

STEP 2 — BUILD:
- A play needs: positive model edge AND no contradicting Tier 1–2 news AND ≥1 reasoner BET/LEAN.
- Anything contradicted by **Tier 1–2 sourced** news (Tier 3 cannot kill a play) or flagged as an artifact → STAND-DOWN with reason.
- SIZING IS FIXED: use the pipeline's recommended_units_pre_news. You MAY downgrade units (e.g. soft news, low confidence) but MUST NOT increase above it, and never above max_units.
- SGP/parlays: list as CANDIDATES ONLY (no units) unless the pack provides ALL THREE: `sgp_recommended_units_pre_news` + a book combined price + `sgp_correlation_rationale`. Do not multiply leg prices yourself.

Output the §4 schema, plus an AGREE/DISAGREE matrix (market_id × A/B/C/D).
```

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
Claude may only **downgrade** `recommended_units_pre_news` (news/confidence), never raise it. A row is **sizing-ineligible** (units empty → reasoning/news only, no stake) when any holds: `model_prob` is missing; OR the market is a push-capable whole-number spread/total and the pipeline has no real `push_prob` for it yet (sizing it with `push_prob=0` would mis-size). Half-point lines, moneylines, and run line ±1.5 are no-push (`push_prob=0`, sizing-eligible). **The model never sees the formula in its prompt — it only reads the emitted units.**

### Stale-line kill criteria (the T‑30 pass) — concrete
Kill or re-stake a play if any holds at T‑30:
- **No edge left:** current price has moved to/through model fair value (recomputed `edge_pct` < `MIN_EDGE`, default 2%).
- **Key number crossed against you:** MLB totals through 7/8/9, run line through 1.5; WNBA spread through 2/3/5/7, total through a whole-number key — re-evaluate, default kill.
- **Prop line drift:** prop line moved ≥ Y stat units (MLB ≥0.5 K / total bases; WNBA ≥1.0 pts, ≥0.5 reb/ast) → re-price before betting.
- **Tier 1–2 contradiction:** late scratch, lineup/starter change, weather flip → kill.
(Thresholds default here but should be emitted per `market_type` by the pipeline.)

---

## 5. Calibration log (so the desk improves)

Log **every play AND every stand-down/fade** — that's how you learn whether the desk is killing good bets or correctly avoiding bad ones. Append to `calibration/log.csv`:
```
date, market_id, event_id, selection, decision(PLAY/STAND_DOWN/FADE), line_taken, price_taken,
closing_line, CLV, units, result(W/L/push/would_have), pnl,
chatgpt_verdict, gemini_verdict, claude_verdict, final_verdict, news_tier_overrode, kill_reason
```
For stand-downs/fades, grade the **would-have** result + CLV — did avoiding it save or cost you? Track weekly: **CLV first** (did you beat the close, on plays and on the close of things you passed?), then ROI and hit rate. Grade each model and the pipeline separately so you learn whose calls to trust on which market types.

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

## 7. Build checklist (what I'd implement next, on GO)

1. **Export script** in the pipeline: read today's EV/signal boards + odds/injuries → emit per row `model_prob`, `push_prob`, `implied_prob`, `edge_pct`, `decimal_price`, the three `*_units` fields, and `research_leverage`; for parlays emit `sgp_recommended_units_pre_news` + `sgp_correlation_rationale` + book combined price together (all three or the SGP stays candidate-only) → write `packs/YYYY-MM-DD/` (candidates.csv, briefing.md, dossiers/). The only code that touches your repo.
2. **Prompt files** saved as `prompts/A..E.md` (with the §2a role block embedded) for one-click copy.
3. **Calibration logger**: append closing lines + results for plays *and* stand-downs, compute CLV and would-have.
4. **Optional later:** Chrome-MCP automation to drive the three web UIs; scheduled task to build the pack each morning.
