# NFL shadow settle (Tier-1 / matchup tags)

**Status:** spike / measurement only. No promotion gates.

## Goal

Score Outlier NFL prediction snapshots out-of-sample against final box scores so
Tier-1 anchors and matchup-tagged props can be measured (hit rate, Brier,
logloss). Closing-line value fills when `close_*` is present on the snapshot;
model Brier fills when `model_p` / `p_model` is present. Values are never invented.

## Map (as of this spike)

| Emit point | Artifact | Keys carried |
|---|---|---|
| `NflPipeline` persistence | `data/NFL/normalized/nfl_high_prob_props_{date}.json` | Tier-1 props + optional `close_*` / `model_p` |
| same | `nfl_matchup_props_{date}.json` | Props with any `MATCHUP_*` tag (same schema) |
| same | `nfl_calibrated_props_{date}.json` | Full calibrated set |
| Close / model enricher | `python -m outlier_nfl.enrich_close` | Post-hoc `close_*` + `model_p` attach |
| Tiering + `model_p` | `outlier_nfl/calibration.py` | Tier-1 when L5==1.0, L10≥0.80, books≥3; **default `model_p` = Laplace(α=2) shrink of empirical hit rate** |
| Projection (optional) | `outlier_nfl/projection.py` | nflverse prior-week gamelog rate vs line |
| Matchup tags | `outlier_nfl/matchup.py` | Stacks `MATCHUP_*`; re-attaches empirical `model_p` when missing |

**Result sources**

| Source | In-repo? | Notes |
|---|---|---|
| `outlier_scrapers/results.py` | Yes | MLB + WNBA ledger settle only — **no NFL** |
| ESPN NFL scoreboard/summary | External | Optional helper; sandbox egress often **403** |
| **nflverse week stats** | External (GitHub releases CSV) | Preferred box + projection input |
| Simplified box-score JSON | Fixtures / generated | CI path |
| Closing lines | Optional on snapshot | `snapshot_best` or real `book_close` feed |
| Model probability | Emitted on snapshot | Never a copy of `implied_probability` |

Join strategy: **team pair + Eastern slate date + player name**.

## Schema additions (optional; never invented)

| Field | Meaning |
|---|---|
| `close_line` / `close_odds` / `close_implied` | Pregame / book close |
| `close_source` | `book_close` \| `pregame_snapshot_best_odds` \| … |
| `model_p` / `p_model` | Selected-side model probability (0–1 preferred) |
| `model_p_source` | `empirical_hit_rate` \| `empirical_hit_rate_laplace` \| `empirical_hit_rate_beta` \| `projection_nflverse_rate` \| `external` \| … |

**Honesty:** `best_odds` is **not** book close. `--mode snapshot_best` labels
`pregame_snapshot_best_odds`. `--mode book_close` requires `--close-feed` and
**refuses** to stamp snapshot odds as `book_close`.

## model_p hierarchy

1. **`projection_nflverse_rate`** — prior-week nflverse gamelog rate vs the prop
   line (Laplace-smoothed). Requires `--nflverse-week-stats` + `--before-week`
   (slate week; no leakage).
2. **`empirical_hit_rate_laplace`** — Laplace/add-α shrink of Outlier
   L10→L20→L5→season rates (default **α=2**, assumed n = 10/20/5/17).
3. **`empirical_hit_rate`** — raw empirical rate (legacy; overconfident at p=1.0).

`enrich_close --attach-model-p hierarchy` runs (1)→(2)→(3). Pipeline high-prob /
calibrated emit defaults to Laplace α=2.

### Shrink discipline (2026-09-20 holdout)

Only one settleable Sunday pack was available on box. Grid on that slate:

| α (Laplace, n=10) | model Brier | vs market 0.209 |
|---|---|---|
| 0 (raw) | 0.238 | +0.029 |
| 1 | 0.215 | +0.006 |
| **2 (default)** | **0.205** | **−0.005** |
| 4 (slate min) | 0.199 | −0.010 |
| 10 | 0.206 | −0.003 |

α∈[1.5, 12] beat market. **α=2 is the shipped default** (pre-specified add-2);
α=4 is reported but not locked without more Sundays.

## Box providers / CLI

```bash
# Settle vs nflverse (or cached box JSON)
python -m outlier_nfl.settle \
  --predictions path/to/nfl_high_prob_props_2026-09-20.json \
  --provider nflverse --slate-date 2026-09-20 --season 2026 \
  --out-md /tmp/nfl_settle.md

# Attach Laplace model_p + snapshot close
python -m outlier_nfl.enrich_close \
  --predictions path/to/pack.json \
  --out path/to/pack_enriched.json \
  --mode snapshot_best \
  --attach-model-p empirical_hit_rate_laplace \
  --alpha 2.0 --overwrite-model-p

# Hierarchy: nflverse projection → Laplace → raw
python -m outlier_nfl.enrich_close \
  --predictions path/to/pack.json \
  --out path/to/pack_hier.json \
  --mode snapshot_best \
  --attach-model-p hierarchy \
  --nflverse-week-stats ~/.cache/outlier_nflverse/stats_player_week_2026.csv \
  --before-week 2 --alpha 2.0 --overwrite-model-p

# Real book close (requires feed; will not label snapshot as book_close)
python -m outlier_nfl.enrich_close \
  --predictions path/to/pack.json \
  --out path/to/pack_book_close.json \
  --mode book_close \
  --close-feed path/to/closes.json \
  --attach-model-p pass
```

Close feed shape: `{ "records": [ { player_name, market, line, position,
matchup, event_id, close_line, close_odds, close_implied }, ... ] }`.

**Book-close blocker:** No NFL prop closing-line capture is wired on this box
(TheOddsAPI / second Outlier scrape at kickoff / Pinnacle close). MLB ledger
has distinct-close logic in `outlier_scrapers/feedback_settlement.py` but it is
not NFL props. Credential/source needed: historical odds close API key **or**
a scheduled Outlier props scrape stored separately from the take snapshot.

## Sunday 2026-09-20 settle (updated this PR)

Calendar: Sat 2026-09-26 → last Sunday **2026-09-20** (NFL 2026 Week 2).

| Item | Value |
|---|---|
| Props | `nfl_high_prob_props_2026-09-20_1pm.json` (328 Tier-1) |
| Box | nflverse Week 2 |
| Close | `snapshot_best` (`pregame_snapshot_best_odds`) — **not** book close |
| Model p | **Laplace α=2** (`empirical_hit_rate_laplace`) on all 328 rows |

| n pred | settled | W/L/P | hit rate | Brier (market) | logloss (market) | Brier (model_p) | logloss (model_p) | CLV |
|---|---|---|---|---|---|---|---|---|
| 328 | 237 | 171/65/1 | 0.725 | 0.209 | 0.611 | **0.205** (n=236) | **0.606** (n=236) | ok (mean 0.0 vs snapshot) |

| Variant | model Brier | notes |
|---|---|---|
| Raw empirical | 0.238 | many p=1.0 |
| Laplace α=2 | **0.205** | **beats market** |
| Hierarchy (proj→Laplace) | 0.217 | 229 proj / 99 Laplace; Week-1-only prior is thin |

Scorecard: `docs/nfl/artifacts/shadow_settle_2026-09-20.md`

### Fixture scorecard (DET@BUF)

| n | W/L/P | hit rate | Brier (implied) | CLV |
|---|---|---|---|---|
| 6 settled | 3/3/0 | 0.50 | ~0.245 | ok when fixture carries `close_*` |

## Honest blockers (remaining)

1. **True book close** — schema + CLI `book_close` mode ship; **no live NFL
   close feed** on box yet (need Odds API / kickoff scrape).
2. **Projection still thin early season** — nflverse gamelog rate works but
   with 1 prior week it underperforms pure Laplace; ridge/Elo still absent.
3. **Single-slate α tuning** — lock α across more Sundays before changing default.
4. **Live ESPN** — optional; often 403. Use nflverse.
5. **LONG_* / some defensive props** — may skip until mapped.
6. **Props location** — live packs on Google Drive; repo does not store full
   slate packs by default.

## What this is not

- Not a promotion gate, desk blend, or pack feedback loop.
- Not an extension of the MLB/WNBA ledger settler.
- Not permission to invent settle fields or fill missing CLV/Brier with placeholders.
- Not permission to copy market implied into `model_p`.
