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
| Odds-API close fetch | `python -m outlier_nfl.fetch_odds_close` | Live/event (hist optional) → close-feed JSON |
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

1. **`projection_nflverse_*`** — nflverse prior-week gamelog projection vs the
   prop line. **v2** (`gamelog_gaussian` / `gamelog_poisson`) activates only when
   ≥3 prior weeks exist; otherwise **v1** Laplace gamelog-rate. Requires
   `--nflverse-week-stats` + `--before-week` (slate week; no leakage). As of
   2026-09-26 only Weeks 1–2 are full (Week 3 thin/TNF) — v2 will not fire yet.
2. **`empirical_hit_rate_laplace`** — Laplace/add-α shrink of Outlier
   L10→L20→L5→season rates (default **α=2**, assumed n = 10/20/5/17).
3. **`empirical_hit_rate`** — raw empirical rate (legacy; overconfident at p=1.0).

`enrich_close --attach-model-p hierarchy` runs (1)→(2)→(3). Pipeline high-prob /
calibrated emit defaults to Laplace α=2.

### Shrink discipline / α lock (multi-slate, 2026-09-26)

See full table: [`docs/nfl/artifacts/alpha_lock_multi_slate.md`](artifacts/alpha_lock_multi_slate.md).

| Pack | Kind | Market Brier | Model Brier (α=2) | Best α on slate |
|---|---|---|---|---|
| 2026-09-20 1pm | **Sunday** Week 2 | 0.209 | **0.205** (beats) | 4 |
| 2026-09-21 MNF | midweek Week 2 | 0.136 | 0.214 (loses) | 6 |
| 2026-09-24 TNF | midweek Week 3 | — | Drive pack; settle after full assemble | — |
| 2026-09-27 | Sunday Week 3 | — | **not settleable yet** (today Sat 09-26) | — |

**Recommendation: keep α=2.** Only one Sunday OOS pack exists; midweek is labeled
separately and does **not** unlock a default bump. α=4/6 minimize individual
slates but do not agree across ≥2 Sundays.

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
# Minimal schema: {"records":[{"player_name","market","line","position","close_odds",
#   "close_implied"? , ...}]}  — see outlier_nfl/close_feed.py
#
# Live Odds-API → close-feed (kickoff capture). Auth: ODDS_API_KEY | THE_ODDS_API_KEY
# (apiKey query param). Player props use /events/{id}/odds. Historical close for
# past slates needs the paid historical endpoint (`--historical-date`).
python -m outlier_nfl.fetch_odds_close --probe-only   # plan coverage (no secrets printed)
python -m outlier_nfl.fetch_odds_close \
  --out path/to/closes.json \
  --predictions path/to/pack.json \
  --save-raw path/to/odds_api_raw_redacted.json
#
# Then enrich (join keys: player_name, market, line, position; matchup/event_id optional):
python -m outlier_nfl.enrich_close \
  --predictions path/to/pack.json \
  --out path/to/pack_book_close.json \
  --mode book_close \
  --close-feed path/to/closes.json \
  --attach-model-p pass
#
# Synthetic close-feed (CI / plumbing proof only — NOT live book close):
python -m outlier_nfl.enrich_close \
  --predictions path/to/pack.json \
  --out path/to/pack_book_close.json \
  --mode book_close \
  --close-feed path/to/closes.json \
  --attach-model-p pass
```

Close feed shape: `{ "records": [ { player_name, market, line, position,
matchup, event_id, close_line, close_odds, close_implied }, ... ] }`.

**Odds-API wiring (this PR):** `outlier_nfl/fetch_odds_close.py` maps NFL player
props → Outlier close-feed rows (best American price across books). Join uses
short keys `(player_name, market, line, position)` so Odds-API event ids need not
match Outlier. Fixture:
`tests/fixtures/nfl/settle/odds_api_event_props_sanitized.json` (+ derived
`book_close_feed_from_odds_api.json`). **Never** label snapshot `best_odds` as
`book_close`.

**Book-close live status (2026-09-26):** `ODDS_API_KEY` is present on the box
secret card (len=25) but The Odds API returns `INVALID_KEY` for sports/events/
props/historical probes — so live tier coverage and 2026-09-20 historical close
enrich are **blocked** until a valid key is injected. Use mocked fixtures for CI;
at kickoff, re-run `fetch_odds_close` once the key validates (`--probe-only`
should show `sports_ok` / `props_ok`).

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

1. **True book close** — `book_close` mode + `--close-feed` schema + Odds-API
   mapper (`python -m outlier_nfl.fetch_odds_close`) ship; fixtures prove CLV ≠ 0
   when closes move (`book_close_feed_moved.json`, `odds_api_event_props_sanitized.json`).
   **Live blocker (2026-09-26):** box has `ODDS_API_KEY` (len=25) but API returns
   `INVALID_KEY` — cannot probe props/historical or enrich 09-20 with real
   `book_close` until a valid key is available. Kickoff path: valid key →
   `fetch_odds_close --out closes.json [--predictions pack]` →
   `enrich_close --mode book_close --close-feed`. Never label snapshot as book_close.

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

## nflverse weeks available (as of 2026-09-26)

| Week | player-week rows | Notes |
|---|---|---|
| 1 | ~1118 | full |
| 2 | ~1107 | full |
| 3 | ~69 | TNF ATL@GB only — thin |

Projection v2 (≥3 prior weeks) therefore stays dormant until Week 4+ slates.
