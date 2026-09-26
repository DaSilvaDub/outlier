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
| `NflPipeline` persistence | `data/NFL/normalized/nfl_high_prob_props_{date}.json` | Tier-1 props + optional `close_line` / `close_odds` / `close_implied` / `close_source` / `model_p` |
| same | `nfl_matchup_props_{date}.json` | Props with any `MATCHUP_*` tag (same schema) |
| same | `nfl_calibrated_props_{date}.json` | Full calibrated set |
| Close enricher | `python -m outlier_nfl.enrich_close` | Post-hoc attach of `close_*` |
| Tiering + `model_p` | `outlier_nfl/calibration.py` | `TIER_1_ANCHOR` when L5==1.0, L10>=0.80, books>=3; **`model_p` = empirical hit rate** (L10→L20→L5→season), source=`empirical_hit_rate` — **never** book implied |
| Matchup tags | `outlier_nfl/matchup.py` `apply_matchup_signals` | Stacks `MATCHUP_*` (+ fade/upgrade volume adj); re-attaches empirical `model_p` when missing |

**Result sources**

| Source | In-repo? | Notes |
|---|---|---|
| `outlier_scrapers/results.py` | Yes | MLB + WNBA ledger settle only — **no NFL** |
| ESPN NFL scoreboard/summary | External | Optional helper in `outlier_nfl/boxscore.py`; sandbox egress often **403** |
| **nflverse week stats** | External (GitHub releases CSV) | Preferred live/offline provider: `outlier_nfl/boxscore_nflverse.py` → simplified schema |
| Simplified box-score JSON | Fixtures / generated | CI path: `tests/fixtures/nfl/settle/boxscores_det_buf.json` |
| Closing lines | Optional on snapshot | `close_*` via pipeline emit (`snapshot_best`) or enricher; **not** silent use of `best_odds` |
| Model probability | Emitted on snapshot | `model_p` / `p_model` + `model_p_source`; empirical hit-rate path (not market). Absent ⇒ model Brier `n/a` |

Join strategy: **team pair + Eastern slate date + player name**. Outlier `event_id` is not assumed equal to ESPN/nflverse provider ids.

## Schema additions (optional; never invented)

| Field | Meaning |
|---|---|
| `close_line` | Pregame / book close line when known |
| `close_odds` | American odds at close (or labeled snapshot) |
| `close_implied` | Implied probability **percent** (same units as `implied_probability`) |
| `close_source` | `book_close` \| `pregame_snapshot_best_odds` \| … |
| `model_p` / `p_model` | Selected-side model probability (0–1 preferred; 0–100 also accepted) |
| `model_p_source` | `empirical_hit_rate` (L10→L20→L5→season) \| `external` \| … — **never** a copy of `implied_probability` |

**Honesty:** `best_odds` is **not** book close. Pipeline emit and `--mode snapshot_best` copy emit-time line/odds/implied into `close_*` and set `close_source=pregame_snapshot_best_odds` so CLV can leave `blocked` while remaining labeled. A true book-close feed should set `close_source=book_close`.

## Box providers

### nflverse (preferred on this box)

```bash
# Downloads (cached under ~/.cache/outlier_nflverse or OUTLIER_NFLVERSE_CACHE):
#   stats_player_week_{season}.csv.gz
#   games.csv
python -m outlier_nfl.settle \
  --predictions path/to/nfl_high_prob_props_2026-09-20.json \
  --provider nflverse \
  --slate-date 2026-09-20 \
  --season 2026 \
  --write-boxscores /tmp/boxscores_2026-09-20.json \
  --out-md /tmp/nfl_settle.md
```

Deps: stdlib only (`csv`, `gzip`, `urllib`). Failure modes: HTTP errors → `BoxScoreError`; missing week/date → empty events; unsupported markets → row skip (`unsupported_or_missing_stat`).

### Fixture / simplified JSON

```bash
python -m outlier_nfl.settle \
  --predictions tests/fixtures/nfl/settle/predictions_tier1.json \
  --boxscores tests/fixtures/nfl/settle/boxscores_det_buf.json \
  --out-json /tmp/nfl_settle.json \
  --out-md /tmp/nfl_settle.md
```

### Attach close post-hoc

```bash
python -m outlier_nfl.enrich_close \
  --predictions path/to/nfl_high_prob_props_DATE.json \
  --out path/to/nfl_high_prob_props_DATE_with_close.json \
  --mode snapshot_best \
  --attach-model-p empirical_hit_rate   # or: pass (alias only; never copies market)
```

## Sunday 2026-09-20 settle (shipped this PR)

Calendar: Sat 2026-09-26 → last Sunday **2026-09-20** (NFL 2026 Week 2).

| Item | Value |
|---|---|
| Props | Drive `nfl_high_prob_props_2026-09-20_1pm.json` (328 Tier-1 rows; assembled via Drive MCP — binary `download_file` unavailable this turn) |
| Box | nflverse `stats_player_week_2026` + `games.csv` (live fetch **worked** on box; ESPN scoreboard **403**) |
| Close | post-hoc `snapshot_best` (`close_source=pregame_snapshot_best_odds`) — **not** book close |
| Model p | empirical hit rate (L10) via enrich `--attach-model-p empirical_hit_rate` → all 328 rows; `model_p_source=empirical_hit_rate` |

| n pred | settled | W/L/P | hit rate | Brier (market) | logloss (market) | Brier (model_p) | logloss (model_p) | CLV |
|---|---|---|---|---|---|---|---|---|
| 328 | 237 | 171/65/1 | 0.725 | 0.209 | 0.611 | **0.238** (n=236) | **3.787** (n=236) | ok (mean implied pts **0.0** vs snapshot; sources=`pregame_snapshot_best_odds`) |

Model Brier is **worse** than market on this slate: raw L10 rates are overconfident (many `model_p=1.0`), so losses at p=1 inflate Brier/logloss. That is an honest measurement, not a blocker.

Skipped: 57 `player_not_in_boxscore`, 34 `unsupported_or_missing_stat` (e.g. LONG_REC / LONG_RUSH / some defensive labels).

Scorecard: `docs/nfl/artifacts/shadow_settle_2026-09-20.md` (summary JSON alongside).

### Fixture scorecard (DET@BUF)

| n | W/L/P | hit rate | Brier (implied) | logloss (implied) | CLV |
|---|---|---|---|---|---|
| 6 settled | 3/3/0 | 0.50 | ~0.245 | ~0.683 | ok when fixture carries `close_*` |

## Honest blockers (remaining)

1. **True book close** — still missing as a feed; snapshot CLV is labeled, not Pinnacle/book close.
2. **No ridge/Elo/ensemble** — `model_p` today is **empirical L10/L5 hit rate**, not an independent projection model. Volume haircuts are tags only (not folded into p).
3. **Overconfident empirical rates** — Tier-1 often has L10=1.0; Laplace/Beta shrinkage or a real projection model is the next calibration step.
4. **Live ESPN** — optional; often 403. Use nflverse.
5. **LONG_* / some defensive props** — may skip until mapped.
6. **Props location** — live packs live on Google Drive (`nfl_high_prob_props_*.json`); repo does not store full slate packs by default.

## What this is not

- Not a promotion gate, desk blend, or pack feedback loop.
- Not an extension of the MLB/WNBA ledger settler.
- Not permission to invent settle fields or fill missing CLV/Brier with placeholders.
