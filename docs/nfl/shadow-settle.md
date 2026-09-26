# NFL shadow settle (Tier-1 / matchup tags)

**Status:** spike / measurement only. No promotion gates.

## Goal

Score Outlier NFL prediction snapshots out-of-sample against final box scores so
Tier-1 anchors and matchup-tagged props can be measured (hit rate, Brier,
logloss). Closing-line value is declared blocked until a close feed exists.

## Map (as of this spike)

| Emit point | Artifact | Keys carried |
|---|---|---|
| `NflPipeline` persistence | `data/NFL/normalized/nfl_high_prob_props_{date}.json` | Tier-1 (`TIER_1_ANCHOR`) props: `event_id`, `event_starts_at`, `matchup`, `team`, `opponent`, `player_name`, `player_id`, `market`, `position`, `line`, `implied_probability`, `best_odds`, `l5/l10`, `calibration_tags`, `window` |
| same | `nfl_matchup_props_{date}.json` | Props with any `MATCHUP_*` tag (same schema) |
| same | `nfl_calibrated_props_{date}.json` | Full calibrated set |
| Tiering | `outlier_nfl/calibration.py` | `TIER_1_ANCHOR` when L5==1.0, L10>=0.80, books>=3 |
| Matchup tags | `outlier_nfl/matchup.py` `apply_matchup_signals` | Stacks `MATCHUP_*` (+ fade/upgrade volume adj) |

**Result sources**

| Source | In-repo? | Notes |
|---|---|---|
| `outlier_scrapers/results.py` | Yes | MLB + WNBA ledger settle only — **no NFL** |
| ESPN NFL scoreboard/summary | External | Optional helper in `outlier_nfl/boxscore.py`; sandbox egress often **403** |
| Simplified box-score JSON | Fixtures | CI path: `tests/fixtures/nfl/settle/boxscores_det_buf.json` |
| Closing lines | **Missing** | No close snapshot on NFL prediction artifacts → CLV blocked |
| Model probability | **Missing** | Only book `implied_probability` (percent) — Brier/logloss use that and label it |

Join strategy: **team pair + Eastern slate date + player name**. Outlier `event_id` is not assumed equal to ESPN provider ids.

## How to run

```bash
# Offline (CI / no network)
python -m outlier_nfl.settle \
  --predictions tests/fixtures/nfl/settle/predictions_tier1.json \
  --boxscores tests/fixtures/nfl/settle/boxscores_det_buf.json \
  --out-json /tmp/nfl_settle.json \
  --out-md /tmp/nfl_settle.md

# Against a real pipeline artifact + hand-built or fetched box scores
python -m outlier_nfl.settle \
  --predictions data/NFL/normalized/nfl_high_prob_props_2026-09-17.json \
  --boxscores path/to/boxscores.json \
  --out-md reports/NFL/shadow_settle_2026-09-17.md
```

Tests (no network):

```bash
pytest tests/test_nfl_shadow_settle.py -q
```

## Box-score fixture schema

```json
{
  "events": [
    {
      "provider_event_id": "fixture-det-buf-20260917",
      "event_date": "2026-09-17",
      "away": "DET",
      "home": "BUF",
      "away_score": 31,
      "home_score": 41,
      "players": {
        "Jahmyr Gibbs": {"RUSHING:YDS": 52, "RUSHING:CAR": 16, "RUSHING:TD": 0, "RECEIVING:TD": 1}
      }
    }
  ]
}
```

Prefer group-qualified keys (`PASSING:YDS`, `RUSHING:YDS`, `RECEIVING:REC`) so units do not collide.

## Honest blockers

1. **CLV vs close** — blocked; no close line on snapshots.
2. **Model Brier** — blocked as model metric; report uses book-implied prob only.
3. **Live ESPN** — optional; often blocked from sandboxed runners. Do not fake metrics.
4. **FIRST_TD / some defensive props** — may skip (`unsupported_or_missing_stat`) until a richer feed exists.

## What this is not

- Not a promotion gate, desk blend, or pack feedback loop.
- Not an extension of the MLB/WNBA ledger settler.
- Not permission to invent settle fields or fill missing CLV/Brier with placeholders.
