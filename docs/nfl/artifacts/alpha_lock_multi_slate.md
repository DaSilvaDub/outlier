# Laplace α lock — multi-slate OOS (2026-09-26)

**Recommendation: keep α=2** (default remains α=2).

Only 1 Sunday pack(s) settleable; midweek packs labeled separately. Best alphas by slate: [('2026-09-20', 4), ('2026-09-21', 6)]. Do not bump default without ≥2 OOS Sundays agreeing.

## Availability

| Pack | Kind | Status |
|---|---|---|
| 2026-09-20 1pm | Sunday Week 2 | settled |
| 2026-09-21 MNF | midweek Week 2 | settled (separately labeled) |
| 2026-09-24 TNF | midweek Week 3 | Drive pack; assemble/settle when complete |
| 2026-09-27 | Sunday Week 3 | **not settleable yet** (Sat 09-26) |

## 2026-09-20 Sunday 1pm (Week 2)

Market Brier: **0.2094**

| α | model Brier | vs market | n_settled | hit rate |
|---|---|---|---|---|
| 0 | 0.2378 | +0.0284 | 237 | 0.725 |
| 1 | 0.2150 | +0.0056 | 237 | 0.725 |
| 2 | 0.2048 | -0.0046 | 237 | 0.725 |
| 4 | 0.1991 ← best | -0.0104 | 237 | 0.725 |
| 6 | 0.2001 | -0.0093 | 237 | 0.725 |
| 10 | 0.2062 | -0.0032 | 237 | 0.725 |

## 2026-09-21 MNF NYG@LAR (Week 2 midweek)

Market Brier: **0.1361**

| α | model Brier | vs market | n_settled | hit rate |
|---|---|---|---|---|
| 0 | 0.2517 | +0.1156 | 29 | 0.724 |
| 1 | 0.2258 | +0.0897 | 29 | 0.724 |
| 2 | 0.2136 | +0.0775 | 29 | 0.724 |
| 4 | 0.2054 | +0.0693 | 29 | 0.724 |
| 6 | 0.2050 ← best | +0.0689 | 29 | 0.724 |
| 10 | 0.2096 | +0.0735 | 29 | 0.724 |

## Decision rule

Keep α=2 unless **≥2 OOS Sunday** slates agree on a different α that also beats market on each.
Midweek (MNF/TNF) packs are reported for honesty but do not alone unlock a default bump.

