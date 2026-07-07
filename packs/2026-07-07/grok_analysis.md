# Daily Betting Report — 2026-07-07

Analysis from `grok_pack_2026-07-07.txt` using pack quotes only, with web research for injuries/lineups (ESPN WNBA injuries, StatMuse ATL@PIT probables). **High-variance props excluded** per prompt instructions.

**Pack freshness:** Built ~5:00–5:20 AM ET Jul 7. WNBA props LM OK; MLB props LM partial (10 fetch errors). Re-check all lines before betting.

---

## MLB Standouts

### 1. ATL @ PIT Money Line HOME @ 0.0 (-148) — **1.5u**
- **market_id:** `1ce0883f4e832fc818515237fe921ab6feef34a1`
- **edge:** 3.9% | **book:** BetRivers

**Rationale:** Pirates home with **Paul Skenes** (6-8, 3.62 ERA) vs Braves **Hurston Waldrep** (0-0, limited 7.1 IP sample). PIT is the market favorite; model shows modest edge at -148. Lower-variance game line vs pitcher K/outs props.

**News:** Braves missing Acuña (hamstring), multiple pitching injuries; Pirates without Cruz/Horwitz/Kim among others (StatMuse, Tier 2).

---

### 2. James Outman — Bases UNDER 0.5 @ 0.5 (-117) — **3.0u** *(downgrade to 2u if uneasy)*
- **market_id:** `ac7edfbffb7cb1e3b46fa06d143aa8e5f8af0671`
- **edge:** 14.7%

**Rationale:** Single-base **under** is relatively low variance vs multi-base overs. Pack's highest-confidence *under* style prop with sizing. No contradicting injury news found in pack.

---

### 3. Tarik Skubal — Outs UNDER 18.5 @ 18.5 (-134) — **3.0u**
- **market_id:** `1e93f2f7ffebb129f8bb650df558fc5dee749312`
- **edge:** 14.6%

**Rationale:** Ace workload under on a half-line; line flat at 17.5→17.5 in pack. Pitcher-out props carry more variance than gamelines but **under** on an elite starter is tighter than plus-money overs.

---

## WNBA Standouts

### 4. DAL @ NYL Spread AWAY +3.5 @ 3.5 (+122) — **1.5u**
- **market_id:** `fe5be47d3fb9143fe1dda697ff05927f84455aab`
- **edge:** 8.3% | **book:** Novig

**Rationale:** Best WNBA EV in pack. **Satou Sabally OUT** for New York (ESPN, Jul 6 listing). **Leonie Fiebich questionable** (foot, Jul 6). Line moved **3.5 → 4.5** in pack — if +3.5 is still available, you hold a key number; if market is +4.5, edge may shrink. Low-to-mid variance team spread vs player props.

**Fade (do not play):** NYL ML HOME signal (`c953e5dd416492907380d7c88669533a75b0732a`) — 90% tickets / 98% money on Liberty; no EV sizing.

---

## Leans (smaller or conflicting context)

| Play | market_id | Note |
|------|-----------|------|
| NYY @ TB ML AWAY @ 0.0 (+113) | `c33866fc9aff04ff0b1f7f6dffa0c6bbbddd5427` | 3.9% edge, **1.0u** — clean game line |
| Hurston Waldrep K OVER 4.5 @ 4.5 (-103) | `29033259ec69e92d79c7c392fb22e92a0881936a` | Only **0.5u** in pack; facing Skenes — thin |
| LAA @ TEX Total UNDER 6.5 @ 6.5 (+132) | `06e39a64beaf41a51353584c828e6d79e1dad5de` | **1.0u** in pack but total rose **7.0→7.5** (against under); HIGH research leverage — verify weather/starters |

---

## Passes / Excluded (high variance or rule conflicts)

| market_id | Play | Why |
|-----------|------|-----|
| `7bcde97730da891e562afad318c84a6c9b730d68` | Mookie Betts Bases **OVER 1.5** (+113) | **Excluded** — multi-base over = high variance per prompt rule |
| `982d8a076ed89376e1e278c5137b826a03b34a43` | Michael Lorenzen Outs **OVER 15.5** (+144) | **Excluded** — plus-money pitcher out over |
| `bb423ab974b8ebcd7f4c16c7dddc4d7d1b6e918f` | Ian Seymour Outs OVER 15.5 (+121) | **Excluded** — plus-money pitcher out over |
| `d763e307b067739f72c05f33155a33abd5b82ed4` | Tyler Callihan Bases UNDER 0.5 (+120) | **20.5% edge** looks like possible artifact; +120 under vs Braves/Skenes day — pass despite sizing |
| `0e31a792af42e407ecaa3f92e2010584d9d0fa05` | Payton Tolle K UNDER 5.5 (+122) | Line moved **4.5→5.5** against under; pitcher K variance |
| `0e4dbe802743c9267e3b1b74c822a0f7cf678f7d` / `dabeffbeb31a9530924edc089de09aa636079323` | Alvarez / Wrobleski K props | Pitcher K variance — excluded |
| `46916449d8c9ce1e731b0264b6a69782b9df7097` | CHI @ PHX Total UNDER 176 | **`push_capable_no_prob`** — not sized; stand down |
| `a4f5cecb052dcc9a0c05d645d7c6896e361d51e7` | Skylar Diggins AST OVER 3.5 | Reports she **lost starting spot / bench role** (Jul 6); conflicts with AST over |
| `9cb830aaecebdd2b17ea8ced8e68ae7ea6f08574` | CHI @ PHX PTS UNDER 85.5 | Signal only; line **83.5→85.5** against under |
| `a8f571e6594fb250b83a27551777ddff5a665041` | Arike Ogunbowale PA UNDER 14.5 | Signal only, no EV/units |

---

## Injury Context (web-sourced)

**DAL @ NYL (Tier 2 — ESPN, Jul 6)**
- **Liberty:** Satou Sabally **OUT**; Leonie Fiebich **questionable** (foot)
- Supports Wings +3.5; weakens Liberty ML favorite thesis

**CHI @ PHX (Tier 2 — ESPN)**
- **Sky:** DiJonai Carrington **OUT** (foot); Rickea Jackson out season
- **Mercury:** Noemie Brochant **probable** (ankle); Natasha Mack out
- Skylar Diggins role reduced (bench) per Jul 6 reports — **avoid her AST over**

**ATL @ PIT (Tier 2 — StatMuse)**
- **Probables:** Waldrep vs **Paul Skenes**
- Braves: Acuña IL among notable absences
- Supports Pirates ML; makes Callihan/Braves offensive props riskier

---

## Stale-Line & Data Quality Notes

1. **MLB props LM partial** — soften steam/signal reads on pitcher props.
2. **Several lines moved against the pack side:** Tolle K (4.5→5.5), LAA/TEX total (7.0→7.5), CHI team pts (83.5→85.5), DAL spread (3.5→4.5).
3. **CHI @ PHX total 176** — model edge in briefing but **no units** (push sizing missing).
4. Pack built pre-dawn ET — **T-30 line kill pass** required.

### NEEDS before locking
- Confirm DAL +3.5 (+122) still exists vs market +4.5
- Official WNBA injury PDF / lineup cards for both games
- Payton Tolle / Skubal / Outman game confirmations and weather for LAA@TEX

---

## Bottom line (actionable card)

| Priority | Play | Units |
|----------|------|-------|
| 1 | PIT ML -148 | 1.5 |
| 2 | DAL +3.5 +122 | 1.5 |
| 3 | James Outman bases U0.5 -117 | 2–3 |
| 4 | Tarik Skubal outs U18.5 -134 | 2–3 |
| 5 | NYY ML +113 | 1.0 |

**Total suggested exposure:** ~9–10 units across 5 plays, all low-to-mid variance types (ML, spread, single-base under, pitcher workload under).

---

*Source: Desktop `grok_pack_2026-07-07.txt` + local reasoning synthesis. Agent: grok.*