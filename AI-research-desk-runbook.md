# AI Research Desk — Daily Betting Guide Runbook

**Purpose:** Turn ChatGPT, Gemini, and Claude into a research desk layered on top of the Outlier pipeline. The pipeline supplies the numeric edge (EV board + signal board at `market_id` grain, multi-book odds, publicMoney, injuries, matchup). The three models add late news, situational context, and an independent cross-check, then produce one consolidated daily guide.

**Mode:** Manual paste. **Output:** Full guide (singles, props, SGP/parlays, stand-downs, unit sizing).
**As-of discipline:** Every model sees a timestamped pack and is told to reason *only* from it. Nothing is valid without the line/price it was taken at.

---

## 0. Mental model — who does what

| Model | Role | Why |
|---|---|---|
| **ChatGPT** (o-series reasoning + Deep Research) | Stress-tester + per-game news | Strong structured decomposition; good at "is this edge real or an artifact?" Deep Research pulls late injury/lineup/weather news per game. |
| **Gemini** (Pro + Deep Research) | Wide scanner | Largest context — ingest the full pack + raw odds; broad multi-source web sweep across the whole slate. |
| **Claude** (extended thinking) | Synthesizer + red-team | Calibrated uncertainty; resolves conflicts; writes the final guide. |

**Key principle:** the desk's job is *as much about killing bad pipeline cards as confirming good ones*. A stand-down is a win. Consensus across models is a filter, not proof — weight independent **information** (deep-research news) above **opinion**.

---

## 1. Daily sequence (manual paste)

Times are relative to the **first market lock** of the slate (e.g. first pitch / tip-off).

| When | Step | Tool |
|---|---|---|
| **T‑180 min** | Run pipeline → produce today's EV/signal cards. Build the **briefing pack** (§2). | Pipeline + export script |
| **T‑170** | Kick off **both** Deep Research jobs first (they take 5–15 min): Gemini wide-scan (Prompt B), ChatGPT per-game (Prompt C). | Gemini + ChatGPT web |
| **T‑160** | While they run, paste **Prompt A** (reasoning stress-test) into ChatGPT o-series and **Prompt D** (reasoning pass) into Claude. | ChatGPT + Claude |
| **T‑140** | Collect all four structured outputs (A–D). | — |
| **T‑130** | Paste **Prompt E** (synthesis) + the four outputs into Claude → **final guide**. | Claude |
| **T‑30** | **Line re-check:** compare current odds vs the pack's `as_of` lines. Kill any play where the value is gone (line moved through your number → CLV already taken). | Pipeline / book |
| Post-slate | Log results + grade each model and the pipeline (§5). | Calibration log |

> Deep Research is a **pre-slate** tool (latency 5–15 min). Not for live in-game.

---

## 2. Briefing-pack spec (what the export script emits)

The export reads the day's pipeline output and writes a dated folder `packs/YYYY-MM-DD/` with:

### 2a. `briefing.md` — the master pack (paste into Gemini + Claude)
Header block (mandatory):
```
SLATE: <sport> <date>   AS_OF: <ISO timestamp, local + UTC>
RULES FOR MODEL:
- Reason ONLY from data in this pack. Do NOT invent or recall odds/lines.
- Every verdict must quote the exact line/price it applies to.
- If you need info not in this pack, list it under "NEEDS" — do not guess.
- Flag any edge that looks like a data artifact (stale line, injury already priced, wrong side of a key number).
```
Body:
- **Top EV cards** (capped to N, default 15): `market_id`, game, market, side, pipeline line/price, best book, model fair value, edge %, edge source tag.
- **Top signal cards** (capped to N): `market_id`, signal type (sharp/steam/reverse-line-move), publicMoney %, money vs bet %, line move since open.
- **Key injuries / status** per game (from injuries endpoint), with timestamp.
- **Notable line moves** since open.
- **Slate index**: every game + first lock time.

### 2b. `candidates.csv` — the shortlist (paste into ChatGPT for A & C)
One row per `market_id` on the EV or signal board, columns:
`market_id, game, market, side, line, price, book, model_fair, edge_pct, edge_source, public_money_pct, money_pct, line_open, line_now, injury_flags`

Cap to the top ~25 by combined EV+signal rank so it fits context. **Never dump the whole slate.**

### 2c. `dossiers/<game>.md` — per-game deep-research briefs (for ChatGPT Prompt C)
One short file per game with: teams, time, the specific `market_id`s in play, current pipeline lines, and the open questions the news pass should answer. The export auto-selects the question set by sport from the bank in **§2d**.

### 2d. Sport-specific question banks (auto-inserted into each dossier + deep-research prompts)

**MLB** — ask/resolve, each with source + timestamp:
- **Starters:** both confirmed SPs, days rest, recent form, pitch-count limit / opener or bullpen game.
- **Bullpen:** who threw the last 1–2 days, closer availability, gassed pen.
- **Lineup:** posted lineup card, key bats in/out, platoon/handedness edge, regulars resting (day-after-night, getaway day).
- **Weather/park:** wind speed + direction (out vs in), temp, rain-delay risk, roof open/closed, hitter vs pitcher park, altitude (Coors).
- **Umpire:** home-plate ump strike-zone tendency (tight/wide → totals & K props).
- *Market types in play:* full game, **F5 (first 5)**, run line, total, **NRFI/YRFI**, strikeout props, H+R+RBI.

**WNBA** — ask/resolve, each with source + timestamp:
- **Availability:** injury report status (out/quest/prob), load management, rest decisions.
- **Lineup/rotation:** confirmed starters, any rotation change, minutes restriction returning from injury.
- **Schedule/fatigue:** back-to-back, travel/time-zone, schedule density.
- **Usage shift:** if a star is out, who absorbs usage/shots → which player-prop **overs** light up.
- **Game script:** pace matchup, blowout risk (→ star minutes capped → prop **unders**), foul-trouble tendencies.
- *Market types in play:* spread, total, player points/reb/ast, **PRA**, 3PM, alt lines.

> `candidates.csv` carries a `sport` and `market_type` column so each market routes to the right question set. WNBA slates are small (run Prompt C on every game); MLB slates are large (cap Prompt C to the top games by EV/signal rank).

**Anchoring rules baked into the pack** (repeat in every file header): only provided odds; quote the line; flag artifacts; list unknowns under NEEDS.

---

## 3. The five prompt templates

> Paste the relevant pack section *above* each prompt. All ask for **structured output** so synthesis is mechanical.

### Prompt A — ChatGPT reasoning stress-test (input: `candidates.csv`)
```
You are a sharp betting analyst. Below is a shortlist of markets my quant model flagged as +EV, with the model's fair value and the current line. Reason ONLY from this data; never invent odds.

For EACH market_id, return:
- verdict: BET | LEAN | PASS | FADE
- side & the exact line/price it applies to
- confidence: 1–5 (qualitative, separate from the model's numeric edge)
- edge_source_check: is the model's edge plausibly real, or an artifact? Pick one: REAL / STALE_LINE / ALREADY_PRICED / KEY_NUMBER_WRONG_SIDE / UNCLEAR
- key_factors: ≤2 lines
- needs: any info not in the pack that would change the verdict

Output as a markdown table, one row per market_id. End with the 3 strongest BETs and any FADEs where the model is likely wrong.
```

### Prompt B — Gemini wide-scan Deep Research (input: full `briefing.md`)
```
Deep research task. Here is today's slate with my model's flagged markets (as-of timestamp in header). Each game is tagged MLB or WNBA — use the matching question set:
- MLB: confirmed starters + days rest, bullpen availability, posted lineup, wind/temp/roof/park, home-plate umpire zone.
- WNBA: injury report status + load management, confirmed starters/minutes limits, back-to-back/travel, usage shift if a star sits, pace & blowout risk.
Search current sources (last 24h) and include sharp-vs-public reporting where available.

For EACH game return:
- news items: bullet, each with source + timestamp
- impact: which market_id(s) it affects and direction
- verdict vs my model: CONFIRMS / CONTRADICTS / NEUTRAL, with one line why
Only report what you can source. Do not invent odds or restate my lines as facts. Flag anything my as-of data (timestamp in header) likely missed.
```

### Prompt C — ChatGPT per-game Deep Research (input: one `dossiers/<game>.md` at a time, top games only)
```
Deep research on this single game. Answer the open questions in the brief using current sources (last 24h). For each answer give source + timestamp. Then state, for each listed market_id, whether the news CONFIRMS / CONTRADICTS / is NEUTRAL to a bet at the quoted line, and why. Do not invent odds.
```

### Prompt D — Claude reasoning pass (input: `briefing.md`)
```
Act as a calibrated, skeptical betting analyst. From the pack only, independently evaluate the top EV and signal cards. For each market_id give: verdict (BET/LEAN/PASS/FADE), the line it applies to, confidence 1–5, and the single biggest reason you might be WRONG. Separately, list any card that looks like a data artifact and should be stood down. Be honest about uncertainty; prefer PASS to a forced lean.
```

### Prompt E — Claude synthesis → final guide (input: outputs A + B + C + D)
```
You are the head of the desk. Below are four analyses of today's slate: two reasoning passes (A, D) and two deep-research news passes (B, C). Build the final guide.

Rules:
- Weight independent NEWS (B, C) above opinion (A, D). News that contradicts a pick overrides agreement between reasoners.
- A play needs: positive model edge AND no contradicting news AND at least one reasoner BET/LEAN.
- Anything contradicted by sourced news or flagged as an artifact → STAND-DOWN with reason.
- Size with fractional Kelly (default ¼-Kelly) using the model edge; cap any single play at <X> units. Round to ½-unit.

Output the final guide in the schema in §4. Include an AGREE/DISAGREE matrix (market_id × A/B/C/D verdict) so I can see where the desk split.
```

---

## 4. Final-guide output schema

```
DATE / AS-OF / first-lock time

A. SINGLES (straight bets)
   market_id | game | side @ line (book) | model edge% | confidence | units | one-line rationale | news support (B/C)

B. PROPS
   same columns; note correlation if used in a parlay below

C. SGP / PARLAYS  (only +EV correlated legs)
   legs (market_ids) | why correlated | combined price | units | note
   — only build when legs are positively correlated AND each leg is independently non-negative
     (e.g. game total OVER + that game's pace/usage prop). Avoid book-shaded random parlays.

D. STAND-DOWNS  (the discipline section)
   market_id | what the pipeline said | why we're passing/fading (artifact or contradicting news)

E. AGREE/DISAGREE MATRIX
   market_id × {ChatGPT-A, Gemini-B, ChatGPT-C, Claude-D} verdicts → where the desk split

F. WATCH / NEEDS
   open line-move triggers and unresolved unknowns to recheck at T-30
```

**Staking:** fractional Kelly on the model edge (default ¼-Kelly), single-play cap, ½-unit rounding. Confidence (qualitative) can downgrade but never upgrade above the Kelly number.

---

## 5. Calibration log (so the desk improves)

After results settle, append one row per play to `calibration/log.csv`:
```
date, market_id, side, line_taken, price_taken, closing_line, CLV, units, result(W/L/push), pnl,
chatgpt_verdict, gemini_verdict, claude_verdict, final_verdict, news_overrode(bool)
```
Track weekly: **CLV first** (did you beat the close?), then ROI and hit rate. Grade each model's verdicts and the pipeline separately so you learn whose calls to trust on which market types. CLV is the leading indicator; W/L is noisy short-term.

---

## 6. Guardrails (built into the flow)

- **Context limits** → pre-rank and cap candidates (§2); never paste the full slate.
- **Hallucinated/stale odds** → header rules force "quote the line / don't invent"; reject any output that cites a price not in the pack.
- **Garbage-in rationalization** → the `edge_source_check` field exists to catch the model justifying a stale-line edge.
- **False consensus** → models share training biases; the synthesis rule weights sourced news over agreement.
- **Latency** → deep research is pre-slate only; kick both jobs off first.
- **CLV decay** → T‑30 re-check kills plays where the market already moved through your number.
- **ToS/privacy** → proprietary pipeline output leaves your machine into 3rd-party models; fine for personal use, just noted.
- **Bankroll** → fractional Kelly + per-play cap; decision support, not a guarantee.

---

## 7. Build checklist (what I'd implement next, on GO)

1. **Export script** in the pipeline: read today's EV/signal boards + odds/injuries → write `packs/YYYY-MM-DD/` (briefing.md, candidates.csv, dossiers/). ~The only code that touches your repo.~
2. **Prompt files** saved as `prompts/A..E.md` for one-click copy.
3. **Calibration logger**: small script to append closing lines + results and compute CLV.
4. **Optional later:** Chrome-MCP automation to drive the three web UIs; scheduled task to build the pack each morning.
