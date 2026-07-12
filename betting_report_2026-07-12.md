# Betting Report — Slate 2026-07-12 (MLB + WNBA)

*Generated from `claude_pack_2026-07-12.txt`. Every line/price quoted verbatim from the pack; news verified via live web sources (last 24h). No high-variance props included, per house rule.*

---

## Screened OUT (do not bet)

| Card | Reason |
|---|---|
| **Luis García Jr. – Total Bases OVER 1.5** (+125, pack's #1 EV, 3.0u) | **Total bases = HIGH variance.** Excluded despite top EV. |
| **Aliyah Boston – 3-Pointers OVER 0.5** (-260) | **3PM = HIGH variance.** Also signal-only (no EV). |
| **ATH @ CWS Total OVER 8.5** (-107) | `edge_suspect_stale_line` + `SOURCE_INTEGRITY` + RLM + thin liquidity. Unreliable. |
| **SEA @ WAS Total UNDER 159** (+104) | `push_capable_no_prob` — no usable model prob, no units. |
| Gunnar Henderson Hits U1.5 (-210); ARI@LAD Hits U9.5; Baldwin Doubles U0.5 (-486); ATH@CWS 1B U6.5; Meneses Fantasy U5.5; Shakira Austin Reb O8.5 | Signal-only cards, no EV sizing. Not firm plays. |

**Data flags I resolved (both were stale/suspicious — now cleared):**
- **Cade Cavalli** pack tag "Suspended" is **stale**. His 7-game ban was cut to 5; he's confirmed **starting today vs NYY**. Prop is LIVE. [1][2]
- **Germán Márquez** listed on SD looked wrong (ex-Rockie) — **confirmed traded, starting for Padres vs TOR today.** Row is valid. [3]

---

## Recommended plays

### Tier 1 — strongest (confirmed + clean variance)

- **Seth Lugo (KC) – Strikeouts UNDER 4.5** @ **+125** · edge 0.173 · **3.0u**
  - Confirmed starter vs BAL (opp: Shane Baz). Lugo ~modest K rate (80 K, 4.56 ERA). Strikeouts = moderate variance. [4]
  - `market_id 858204d…`→ correlated with KC@BAL total under (see below).

- **IND @ LVA – Total UNDER 182.5** @ **-105** · edge 0.096 · **2.5u**
  - **Caitlin Clark confirmed playing but on a 20–25 min back-injury restriction.** Fewer Clark possessions = less Indiana pace/scoring → supports the UNDER. [5]
  - WNBA games line-movement is authoritative (44/44). Good spot.

- **Germán Márquez (SD) – Earned Runs OVER 2.5** @ **+105** · edge 0.147 · **3.0u**
  - 5.02 ERA vs a strong Toronto offense (Gausman opposing). Run-scoring environment supports OVER. [3]

- **JR Ritchie (ATL) – Earned Runs UNDER 1.5** @ **+122** · edge 0.158 · **3.0u**
  - Confirmed starter vs STL (Dustin May opposing). `thin_liquidity` — price may be soft, take early. [6]

### Tier 2 — good but caveated

- **Trevor McDonald (SF) – Outs OVER 15.5** @ **+121** · edge 0.172 · **3.0u**
  - Confirmed vs COL at pitcher-friendly Oracle; weak Rockies road bats favor going 5.1+. `thin_liquidity`. [6]
  - ⚠️ **Correlation:** you also have *COL ML AWAY* below. McDonald pitching deep/well and Colorado winning are **opposing outcomes — do not stack both at full size.** Pick one lean per this game.

- **Cade Cavalli (WSH) – Strikeouts OVER 5.5** @ **+115** · edge 0.168 · pack 3.0u → **I'd cut to 1.5u**
  - ⚠️ Returning from a 5-game suspension → likely **managed pitch count caps innings, which caps K upside.** NYY also missing Judge & Stanton (both 10-day IL) — softer lineup cuts whiff volume. Real risk to an OVER. Downgrade. [1][2]

- **ATL @ STL – Run Line AWAY -1.5** @ **-170** · edge 0.046 · **2.0u**
  - Braves -1.5. Note **public 92% / money 100%** already on it — you're on the same side as the crowd; edge is thin. Fine but not a value spot.

### Tier 3 — small dog MLs (1.0u each, all `thin_liquidity`, low edge ~0.04)

- **SEA @ TB – ML AWAY** @ **+123** (edge 0.042) [7]
- **LAA @ MIN – ML AWAY** @ **+125** (edge 0.042)
- **COL @ SF – ML AWAY** @ **+126** (edge 0.041) — ⚠️ opposes McDonald Outs Over (same game); don't double-count.
- **NYL @ TOR – Spread AWAY -6.5** @ **-100** (edge 0.065, 1.5u) — Liberty laying 6.5 **shorthanded** (Sykes OUT; Fiebich/Sabally GTD). Live-check actives before laying the number.

---

## Correlation / staking notes
- **KC @ BAL:** Lugo K-Under **and** the KC@BAL total under are the same game / same "low-scoring" thesis → discount combined stake, don't size independently. (Total also carries `SOURCE_INTEGRITY_FLAG`, so I left it off the firm list — lean only.)
- **COL @ SF:** McDonald Outs Over vs COL ML are negatively correlated — one lean only.
- Everything else is single-game, uncorrelated.

## Gaps / freshness
- Pack built ~06:02 ET 7/12; MLB **props** stream was `partial` (10 fetch errors) — current odds still fresh but treat prop line-movement as context-only.
- No primary NWS weather pulled (none of the leans are weather-sensitive totals).
- WNBA actives (GTD players) can flip at tip — recheck NYL@TOR and IND@LVA final injury reports before locking.

---

### Sources
1. [MLB.com — Cavalli/Contreras discipline](https://www.mlb.com/news/cade-cavalli-willson-contreras-disciplined-for-benches-clearing-incident)
2. [Audacy — Cavalli suspension reduced to 5, returns Sunday vs NYY](https://www.audacy.com/theteam980/sports/nationals/cade-cavalli-s-suspension-reduced-from-seven-games-to-five)
3. [Bleacher Nation — Padres vs Blue Jays, Márquez starting 7/12](https://www.bleachernation.com/picks/2026/07/08/san-diego-padres-vs-toronto-blue-jays-series-july-10-12-odds-starting-pitchers-predictions/)
4. [Field Level Media — Orioles vs Royals, Lugo/Baz probables](https://fieldlevelmedia.com/mlb/orioles-look-to-enter-break-on-first-4-game-win-streak-of-26-vs-royals/)
5. [Yahoo Sports — Final injury report, Clark plays on minutes restriction](https://sports.yahoo.com/articles/final-injury-report-fever-aces-190000159.html)
6. [Yahoo Sports — Braves' JR Ritchie vs Giants/Cardinals probables](https://sports.yahoo.com/articles/braves-hope-jr-ritchie-pull-213448803.html)
7. [Baseball-Reference — MLB probable pitchers 7/12/2026](https://www.baseball-reference.com/previews/index.shtml)
