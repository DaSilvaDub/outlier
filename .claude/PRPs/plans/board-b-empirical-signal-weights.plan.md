# Plan: Empirically Learned Board B Signal Weights

## Summary
Replace Board B's hardcoded `SIGNAL_WEIGHTS` composite (`hit=0.40, insight=0.20, movement=0.20, orf=0.20`) in [outlier_scrapers/cards.py](../../../outlier_scrapers/cards.py) with a versioned, empirically fitted weight artifact — analogous to the market/model blend weights just shipped in PR #52 (`outlier_scrapers/probability_blend.py` + `feedback.py::fit_blend_weights`). Weights are fitted offline from settled `market_snapshots` history by minimizing Brier score over the 4-weight probability simplex, then shrunk toward the current hand-tuned defaults using a pseudo-sample-size prior — the same empirical-Bayes shrinkage idea the source research doc (Gemini "Empirical Calibration of Board B" plan) asks for, applied to composite weights rather than per-player hit rates.

## User Story
As a quant/analyst using Board B to triage signal candidates,
I want the hit/insight/movement/orf composite weights to be learned from realized win/loss outcomes instead of a fixed 40/20/20/20 split,
So that Board B's ranking reflects which signals have actually predicted outcomes historically, while staying stable (shrunk toward the existing defaults) when there isn't enough settled history yet.

## Problem → Solution
**Current**: `cards.py:signal_score()` always blends the 4 components with the hardcoded module constant `SIGNAL_WEIGHTS`. There is no feedback loop from realized outcomes back into these weights, even though every settled row already logs `hit_rate_component`, `insight_component`, `movement_component`, `orf_component` alongside `win_loss_push` in the `market_snapshots` table (`feedback.py`).

**Solution**: Add a new `outlier_scrapers/signal_weights.py` module (mirrors `probability_blend.py`) that fits/writes/loads a versioned `calibration/signal_weights.json` artifact. Add `feedback.py fit-signal-weights` CLI (mirrors `fit-blend`) that trains from the existing `market_snapshots` table — no schema change needed, the training columns already exist. Wire the resolved weights into `cards.py`'s `signal_score()` at read time, defaulting to the current hardcoded weights when the artifact is missing, inactive, or below the minimum sample threshold (fail-open to the known-safe default, exactly like `probability_blend.resolve_market_weight`'s `market_only_*` fallbacks).

## Metadata
- **Complexity**: Medium (mirrors an already-shipped pattern; no new external deps, no schema migration)
- **Source PRD**: `C:\Users\dasil\OneDrive\Desktop\Plans\GEMINI\Outlier Feature Implementation Plan.md` (Gemini research doc — "Empirical Calibration of Board B"), scoped down per user decision (2026-07-20): mirror PR #52's pattern for the 4 composite weights only. Full ORF model / Beta-Binomial per-player shrinkage / RLM-divergence-liquidity signal ingestion are explicitly OUT of scope for this plan (see "NOT Building").
- **PRD Phase**: N/A (free-form scoped slice, not a phased PRD)
- **Estimated Files**: 6 (2 new, 4 modified)

---

## UX Design
N/A — internal/backend change. Board B's JSON payload gains a `signal_weights` metadata block (source, model_version, weights used) but no UI changes are in scope. If a frontend consumes `cards_latest.json` and wants to surface "weights learned from N settled bets," that is a separate, later change.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `outlier_scrapers/probability_blend.py` | 1-333 (whole file) | The exact pattern to mirror: fit/shrink/write/load/resolve for a versioned learned-weight artifact. |
| P0 | `outlier_scrapers/cards.py` | 43-68, 214-298 | Current `HIT_WEIGHTS`/`SIGNAL_WEIGHTS` constants and `signal_score()` — the function being modified. |
| P0 | `outlier_scrapers/cards.py` | 315-323, 969-1046 | `Indexes` dataclass and `build_cards_payload()` — where the artifact gets loaded once and threaded down. |
| P0 | `outlier_scrapers/cards.py` | 592-637, 769-839 | The two `signal_score(...)` call sites (`assemble_card`, the game-card builder) that must read weights from `idx` instead of the module constant. |
| P0 | `outlier_scrapers/feedback.py` | 1490-1530 | `_joined_rows()` (already selects `hit_rate_component`/`insight_component`/`movement_component`/`orf_component` + `win_loss_push`) and `fit_blend_weights()` — the function to mirror for `fit_signal_weights()`. |
| P1 | `outlier_scrapers/feedback.py` | 2043-2095 | `fit-blend` argparse subcommand + dispatch — mirror for `fit-signal-weights`. |
| P1 | `outlier_scrapers/pack.py` | 516-556, 1614-1650 | `apply_learned_probability_blend()` and how `main()` loads `blend_artifact` once via CLI arg and threads it through. Confirms the composite (`signal.composite`) is computed upstream in `cards.py`, not `pack.py` — `pack.py` only flattens already-computed fields (lines 830-839), so `pack.py` needs **no changes**. |
| P1 | `tests/test_probability_blend.py` | 1-101 (whole file) | TEST_STRUCTURE to mirror for `tests/test_signal_weights.py`. |
| P2 | `outlier_scrapers/paths.py` | 1-10 | `PROJECT_ROOT` — base for the new `calibration/signal_weights.json` default path. |
| P2 | `.gitignore` | 21-26 | `calibration/*.sqlite3*` and `calibration/reports|exports/` are ignored; `calibration/blend_weights.json`-style artifacts are **not** ignored (they're committed, versioned config) — `signal_weights.json` follows the same convention. |

## External Documentation
No external research needed — feature uses established internal patterns (PR #52's blend-weight fitting) and pure-Python numeric methods already implicit in the codebase's no-numpy/no-scipy dependency footprint (`pyproject.toml` has no `numpy`/`scipy`; do not add them — implement the simplex-constrained fit in pure Python, same spirit as `probability_blend.fit_market_weight`'s closed-form 2-weight solver).

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: outlier_scrapers/probability_blend.py:14-22
SCHEMA_VERSION = 1
DEFAULT_WEIGHTS_PATH = paths.PROJECT_ROOT / "calibration" / "blend_weights.json"
DIMENSIONS = (
    "league",
    "market_type",
    "odds_range",
    "time_before_game",
    "data_quality_tier",
)
```
Mirror as `outlier_scrapers/signal_weights.py`:
```python
SCHEMA_VERSION = 1
DEFAULT_WEIGHTS_PATH = paths.PROJECT_ROOT / "calibration" / "signal_weights.json"
COMPONENTS = ("hit", "insight", "movement", "orf")  # matches cards.SIGNAL_WEIGHTS keys
DEFAULT_WEIGHTS: dict[str, float] = {"hit": 0.40, "insight": 0.20, "movement": 0.20, "orf": 0.20}
```

### ERROR_HANDLING
```python
# SOURCE: outlier_scrapers/probability_blend.py:243-253
def load_weight_artifact(path: Path = DEFAULT_WEIGHTS_PATH) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    return payload
```
Fail-open, never raise: a missing/corrupt/stale artifact silently falls back to `DEFAULT_WEIGHTS`.

### FAIL-CLOSED CUTOFF (freshness gate)
```python
# SOURCE: outlier_scrapers/probability_blend.py:267-276
generated = _parse_timestamp(artifact.get("generated_at"))
row_time = _parse_timestamp(row.get("captured_at") or row.get("as_of"))
if generated is None or row_time is None or generated > row_time:
    return {..., "source": "market_only_artifact_cutoff"}
```
Signal weights don't have a natural "row timestamp" the way blend weights do (cards are built per-league-run, not per-settled-snapshot at read time) — **the cutoff check does not apply here**. Instead, freshness is enforced only via `status == "active"` and `eligible_samples >= min_samples`. Document this deliberate deviation in the module docstring so a future reader doesn't assume parity with `probability_blend`.

### SHRINKAGE_PATTERN (the empirical-Bayes core)
```python
# SOURCE: outlier_scrapers/probability_blend.py:198-210
raw = fit_market_weight(pairs)
shrunk = (
    len(pairs) * float(raw["market_weight"]) + prior_strength * global_weight
) / (len(pairs) + prior_strength)
```
Mirror this exact `(n * raw + k * prior) / (n + k)` shrinkage formula per-weight, shrinking the raw fitted simplex point toward `DEFAULT_WEIGHTS` (the current hand-tuned composite) rather than toward a "global fit" (there is no higher-level global to shrink toward here — `DEFAULT_WEIGHTS` *is* the prior).

### LOGGING_PATTERN
N/A — neither `probability_blend.py` nor `cards.py` use `logging`; both are pure functions returning status/diagnostic fields in their result dicts (`status`, `source`, `model_version`). Follow this: no new logging calls, surface diagnostics as return-value fields instead.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_probability_blend.py:26-30
def test_fit_market_weight_minimizes_brier_loss():
    pairs = [(0.6, 0.4, 1.0)] * 54 + [(0.6, 0.4, 0.0)] * 46
    fitted = probability_blend.fit_market_weight(pairs)
    assert fitted["market_weight"] == pytest.approx(0.7)
    assert fitted["model_weight"] == pytest.approx(0.3)
```
Mirror with a synthetic dataset where the optimum is analytically obvious (e.g., `hit` component perfectly predicts the outcome, others are constant noise at 50 → optimal fit should push weight almost entirely onto `hit`).

### JSON_WRITE_PATTERN (atomic write)
```python
# SOURCE: outlier_scrapers/probability_blend.py:234-240
def write_weight_artifact(artifact: Mapping[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(json.dumps(dict(artifact), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return output_path
```
Copy verbatim (temp-file + atomic `.replace()` avoids readers seeing a half-written JSON file).

### CLI_SUBCOMMAND_PATTERN
```python
# SOURCE: outlier_scrapers/feedback.py:2043-2050, 2075-2089
blend_parser = subparsers.add_parser(
    "fit-blend", help="Fit market/model weights from settled pregame snapshots."
)
blend_parser.add_argument("--output", type=Path, default=probability_blend.DEFAULT_WEIGHTS_PATH)
blend_parser.add_argument("--min-samples", type=int, default=30)
blend_parser.add_argument("--prior-strength", type=float, default=30.0)
...
elif args.command == "fit-blend":
    artifact = fit_blend_weights(args.db, args.output, min_samples=args.min_samples, prior_strength=args.prior_strength)
    print(json.dumps({"output": str(args.output), "status": artifact["status"], "model_version": artifact["model_version"], "eligible_samples": artifact["eligible_samples"]}))
```
Mirror exactly for a new `fit-signal-weights` subcommand.

### ARTIFACT_LOADING_AT_CLI_LEVEL
```python
# SOURCE: outlier_scrapers/pack.py:1621-1638
parser.add_argument("--blend-weights", type=Path, default=probability_blend.DEFAULT_WEIGHTS_PATH, ...)
...
blend_artifact = probability_blend.load_weight_artifact(args.blend_weights)
build_kwargs: dict[str, Any] = {"opportunity_rows_out": opportunity_rows}
if blend_artifact is not None:
    build_kwargs["blend_artifact"] = blend_artifact
```
Mirror in `cards.py`'s `parse_args`/`main`/`export_cards_for_league`/`build_cards_payload` chain, but load the artifact **once per league build**, not per-row (there is no per-row segmentation, so one resolved weight dict serves the whole run — see Task 3).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `outlier_scrapers/signal_weights.py` | CREATE | New module: fit/shrink/write/load/resolve for the Board B composite weight artifact. |
| `tests/test_signal_weights.py` | CREATE | Unit tests mirroring `tests/test_probability_blend.py`. |
| `outlier_scrapers/cards.py` | UPDATE | `signal_score()` accepts weights; `Indexes` carries a resolved weights dict; `build_cards_payload`/`export_cards_for_league`/`parse_args`/`main` load the artifact once and thread it through; payload gains a `signal_weights` metadata block. |
| `outlier_scrapers/feedback.py` | UPDATE | Add `fit_signal_weights()` (mirrors `fit_blend_weights()`, reuses `_joined_rows()`) + `fit-signal-weights` CLI subcommand. |
| `tests/test_cards.py` | UPDATE | Add coverage for `signal_score()` weights parameter and the payload's new `signal_weights` metadata block. |
| `tests/test_run_desk.py` or wherever `fit-blend` CLI is smoke-tested | UPDATE | Add the equivalent smoke test for `fit-signal-weights` if `fit-blend` has one (check during Task 6; if none exists, skip — don't invent test infra beyond parity). |

## NOT Building
- Ordered Random Forest (ORF) model training/integration — out of scope; `orf_score` stays an externally-supplied input, not something this plan trains.
- Beta-Binomial per-player posterior hit rate, `effective_sample_size`, `confidence_interval_low/high`, `calibration_bucket` — a separate, larger effort (per-entity Bayesian shrinkage is a different axis from composite-weight shrinkage).
- New market-signal ingestion: reverse line movement (RLM) flag, public-money/ticket divergence, liquidity/book-count index — none of these exist in the current data schema; adding them is a distinct data-engineering project.
- Per-dimension weight segmentation (league / market_type / odds_range / etc., as `probability_blend` does for market/model weights) — v1 fits **global weights only**. Segmentation can be added later by generalizing `fit_signal_weights` the same way `fit_weight_artifact` generalizes `fit_market_weight`, once there's evidence the global fit needs it.
- Threading `signal_weight_source`/`signal_weight_version` audit fields down into `pack.py` rows and the `market_snapshots` SQLite schema — would require a schema migration (`ALTER TABLE`) matching the `hit_rate_component` etc. precedent (`feedback.py:249-253`). Deferred; v1 surfaces weight provenance only in the `cards_latest.json` payload metadata.
- Automatic scheduling of `fit-signal-weights` inside `daily_job.py` — `fit-blend` is not auto-scheduled either (confirmed: no reference to it in `pack.py` or `daily_job.py`); stays a manually-invoked operator command, consistent with existing convention.
- Shadow deployment / A/B rollout infrastructure — Board B is an internal descriptive ranking, not a customer-facing EV number (unlike Board A); the existing fail-open default (fall back to `DEFAULT_WEIGHTS` below `min_samples`) already provides the safety margin a shadow rollout would give.

---

## Step-by-Step Tasks

### Task 1: Create `outlier_scrapers/signal_weights.py`
- **ACTION**: New module implementing the fit/shrink/write/load/resolve lifecycle for Board B's 4-weight composite.
- **IMPLEMENT**:
  - `SCHEMA_VERSION = 1`, `DEFAULT_WEIGHTS_PATH = paths.PROJECT_ROOT / "calibration" / "signal_weights.json"`, `COMPONENTS = ("hit", "insight", "movement", "orf")`, `DEFAULT_WEIGHTS = {"hit": 0.40, "insight": 0.20, "movement": 0.20, "orf": 0.20}`.
  - `fit_signal_weights(rows: Iterable[tuple[dict[str, float], float]]) -> dict[str, Any]`: takes `(component_values, actual_outcome)` pairs where `component_values` is `{"hit": ..., "insight": ..., "movement": ..., "orf": ...}` on a 0-100 scale (same scale `signal_score` already uses) and `actual_outcome` is `1.0`/`0.0`. Rescale components to `[0, 1]` internally (divide by 100). Minimize mean squared error (Brier score) `L(w) = mean((sum_c w_c * x_c) - y)^2` over the probability simplex (`w_c >= 0`, `sum(w_c) == 1`) via **projected gradient descent**:
    1. Initialize `w = DEFAULT_WEIGHTS` (warm-start from the current prior, not uniform — faster, more stable convergence).
    2. Compute gradient `g_c = (2/N) * sum_i x_ic * (pred_i - y_i)`.
    3. Step: `w' = w - eta * g` with a fixed `eta = 1.0 / (2.0 * max(1.0, sum(x_c^2 for c in COMPONENTS) / N))` (conservative Lipschitz-bound step size — recompute once per fit, not per iteration).
    4. Euclidean-project `w'` onto the simplex (standard sort-based simplex projection algorithm — implement as a small private helper `_project_to_simplex(weights: dict[str, float]) -> dict[str, float]`; this is the one part of the algorithm worth a dedicated GOTCHA below).
    5. Repeat for a fixed `max_iterations = 2000` (deterministic — no early-stopping tolerance needed at this scale, 4 dimensions converges well within that budget).
    6. Return `{"hit": w_hit, "insight": w_insight, "movement": w_movement, "orf": w_orf, "n": N, "brier_score": final_loss}`.
  - `fit_weight_artifact(rows, *, min_samples=30, prior_strength=30.0, generated_at=None) -> dict[str, Any]`: mirrors `probability_blend.fit_weight_artifact` structure minus the `dimensions` table (v1 is global-only, see "NOT Building"):
    ```python
    eligible = [r for r in rows if _valid(r)]  # all 4 components present + numeric, outcome in {W, L}
    if len(eligible) < min_samples:
        weights, status = dict(DEFAULT_WEIGHTS), "insufficient_history"
        raw_fit = None
    else:
        raw_fit = fit_signal_weights(eligible)
        shrunk = {
            c: (len(eligible) * raw_fit[c] + prior_strength * DEFAULT_WEIGHTS[c]) / (len(eligible) + prior_strength)
            for c in COMPONENTS
        }
        total = sum(shrunk.values())
        weights = {c: shrunk[c] / total for c in COMPONENTS}  # renormalize after shrink (shrinkage can drift off-simplex by rounding)
        status = "active"
    artifact = {
        "schema_version": SCHEMA_VERSION, "status": status,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "objective": "brier_score", "min_samples": min_samples, "prior_strength": prior_strength,
        "eligible_samples": len(eligible), "weights": weights, "raw_fit": raw_fit,
    }
    # fingerprint + model_version exactly like probability_blend.py:226-230
    ```
  - `write_weight_artifact` / `load_weight_artifact`: copy `probability_blend.py`'s implementations verbatim (same atomic-write / schema-version-check pattern), just pointed at `signal_weights.DEFAULT_WEIGHTS_PATH`.
  - `resolve_signal_weights(artifact: Mapping[str, Any] | None) -> dict[str, Any]`: returns `{"weights": {...}, "source": "learned" | "prior_default_insufficient_history" | "prior_default_missing_artifact" | "prior_default_invalid_artifact", "model_version": "..."}`. No freshness/cutoff check (see FAIL-CLOSED CUTOFF pattern note above — deliberately simpler than `probability_blend`).
- **MIRROR**: SHRINKAGE_PATTERN, ERROR_HANDLING, JSON_WRITE_PATTERN (all above).
- **IMPORTS**: `from __future__ import annotations`, `hashlib`, `json`, `datetime`/`timezone` from `datetime`, `Path` from `pathlib`, `Any`, `Iterable`, `Mapping` from `typing`, `from outlier_scrapers import paths`.
- **GOTCHA**: The simplex-projection helper (`_project_to_simplex`) is the one nontrivial piece of math in this file. Standard algorithm (Wang & Carreira-Perpiñán 2013 / Duchi et al. 2008 "projection onto the simplex"): sort weights descending, find the largest `k` such that `sorted_w[k] - (cumsum[k] - 1) / (k+1) > 0`, compute `theta = (cumsum[k-1] - 1) / k`, then `w_i = max(w_i - theta, 0)`. With only 4 components, brute-force is fine: try all 4 candidate thresholds and pick the valid one — don't reach for a library, there is no numpy/scipy dependency in this project (`pyproject.toml` has none) and this stays consistent with `probability_blend.py`'s pure-Python approach.
- **VALIDATE**: `python -m pytest tests/test_signal_weights.py -v` (Task 2) plus `python -m mypy outlier_scrapers/signal_weights.py` (this file is NOT in the `mypy` exclude list in `pyproject.toml`, unlike `cards.py` — keep it fully typed and passing).

### Task 2: Create `tests/test_signal_weights.py`
- **ACTION**: Unit tests mirroring `tests/test_probability_blend.py`'s structure.
- **IMPLEMENT**: At minimum:
  - `test_fit_signal_weights_converges_to_dominant_predictor`: synthetic data where `hit` component perfectly separates W/L (e.g. `hit=90` on wins, `hit=10` on losses) and the other 3 components are constant `50` for every row (uninformative). Assert the fitted `hit` weight is close to `1.0` (e.g. `> 0.9`) and `brier_score` is near-zero.
  - `test_fit_weight_artifact_shrinks_toward_default_below_min_samples`: fewer rows than `min_samples` → `status == "insufficient_history"` and `weights == signal_weights.DEFAULT_WEIGHTS`.
  - `test_fit_weight_artifact_shrinks_toward_default_with_prior`: enough rows to fit, but `prior_strength` large relative to `n` → resulting weights stay close to `DEFAULT_WEIGHTS` (assert e.g. `abs(weights["hit"] - DEFAULT_WEIGHTS["hit"]) < 0.05`).
  - `test_weight_artifact_round_trip` (tmp_path): fit → write → load → compare, mirroring `test_probability_blend.py:85-91`.
  - `test_resolve_signal_weights_falls_back_when_artifact_missing`: `resolve_signal_weights(None)` returns `DEFAULT_WEIGHTS` with `source == "prior_default_missing_artifact"`.
- **MIRROR**: TEST_STRUCTURE pattern above.
- **IMPORTS**: `pytest`, `json`, `from outlier_scrapers import signal_weights`.
- **GOTCHA**: Weights must always sum to `1.0` within floating-point tolerance (`pytest.approx`) in every returned artifact — assert this explicitly in at least one test, since the shrink-then-renormalize step in Task 1 is the one place a bug could silently produce a non-normalized composite.
- **VALIDATE**: `python -m pytest tests/test_signal_weights.py -v` — all pass, and manually confirm the dominant-predictor test's asserted weight bound is actually met by running it once before moving on (this is the one piece of numerically nontrivial logic in the whole plan).

### Task 3: Wire learned weights into `cards.py`
- **ACTION**: Thread a resolved weights dict through the existing card-assembly pipeline without changing `signal_score`'s default (backward-compatible) behavior for any caller that doesn't pass weights.
- **IMPLEMENT**:
  - Add `from outlier_scrapers import signal_weights` import.
  - Change `signal_score`'s signature (`cards.py:271-276`) to accept an optional 5th parameter: `weights: Mapping[str, float] | None = None`. Inside, use `weights or SIGNAL_WEIGHTS` in place of the bare `SIGNAL_WEIGHTS` reference at lines 286-289 (keep the module constant `SIGNAL_WEIGHTS` as the fallback default — do not delete it, existing tests/behavior with no artifact must be unchanged).
  - Add a `signal_weights: dict[str, float] = field(default_factory=lambda: dict(SIGNAL_WEIGHTS))` field (plus `signal_weights_meta: dict[str, Any] = field(default_factory=dict)` for the source/model_version) to the `Indexes` dataclass (`cards.py:315-323`) — same pattern as the existing `enrichment_loaded`/`enrichment` fields.
  - At the two call sites (`cards.py:605`, `cards.py:780`), change `signal_score(side, sdata, mv, matched)` to `signal_score(side, sdata, mv, matched, weights=idx.signal_weights)`.
  - In `build_cards_payload` (`cards.py:969`), before `build_indexes(...)` is called, load the artifact and resolve weights, then set them on the constructed `idx`:
    ```python
    artifact = signal_weights.load_weight_artifact(weights_path)
    resolved = signal_weights.resolve_signal_weights(artifact)
    idx.signal_weights = resolved["weights"]
    idx.signal_weights_meta = {"source": resolved["source"], "model_version": resolved["model_version"]}
    ```
  - Add a `weights_path: Path = signal_weights.DEFAULT_WEIGHTS_PATH` parameter to `build_cards_payload(league: str, weights_path: Path = ...)` and thread it from `export_cards_for_league` and `parse_args`/`main` (`cards.py:1204-1214`), mirroring `pack.py`'s `--blend-weights` CLI arg exactly (name it `--signal-weights`).
  - Add `"signal_weights": idx.signal_weights_meta` (or equivalent) to the top-level payload dict built in `build_cards_payload` (`cards.py:1015-1045`), next to `"coverage"`.
- **MIRROR**: ARTIFACT_LOADING_AT_CLI_LEVEL pattern above.
- **IMPORTS**: `from outlier_scrapers import signal_weights` (new); `Mapping` already partially used elsewhere in the file via `typing` — confirm it's imported, add if not.
- **GOTCHA**: `cards.py` is in `pyproject.toml`'s `mypy` exclude list (`exclude = '(normalizer|api|line_movement|refresh|cards)\.py'`) — you will not get mypy feedback on this file's changes; be extra careful with `Optional`/`None` handling here since the type checker won't catch mistakes. Also: **do not** add per-row artifact freshness/cutoff logic here — v1 loads the artifact once per league build (see Task 1's note that `resolve_signal_weights` has no cutoff check), consistent with the "global weights, no segmentation" scope decision.
- **VALIDATE**: `python -m pytest tests/test_cards.py -v` and manually run `python -m outlier_scrapers.cards --league WNBA` against existing fixture data (if `tests/test_cards.py` has fixture-driven integration coverage, check it still passes with no artifact present — i.e., `weights_path` pointing at a nonexistent file, which must produce identical Board B rankings to today).

### Task 4: Add `fit_signal_weights()` + CLI to `feedback.py`
- **ACTION**: Offline training entrypoint, mirroring `fit_blend_weights()`.
- **IMPLEMENT**:
  ```python
  def fit_signal_weights(
      db_path: Path = DEFAULT_DB_PATH,
      output_path: Path = signal_weights.DEFAULT_WEIGHTS_PATH,
      *,
      min_samples: int = 30,
      prior_strength: float = 30.0,
  ) -> dict[str, Any]:
      """Fit a versioned Board B composite-weight artifact from settled snapshots."""
      with _connect(Path(db_path)) as conn:
          rows = _joined_rows(conn)  # already selects the 4 component columns + win_loss_push
      training_pairs = []
      for row in rows:
          result = str(row.get("win_loss_push") or "").strip().upper()
          if result not in {"W", "L"}:
              continue
          components = {
              "hit": _float(row.get("hit_rate_component")),
              "insight": _float(row.get("insight_component")),
              "movement": _float(row.get("movement_component")),
              "orf": _float(row.get("orf_component")),
          }
          if any(v is None for v in components.values()):
              continue
          training_pairs.append((components, 1.0 if result == "W" else 0.0))
      artifact = signal_weights.fit_weight_artifact(
          training_pairs, min_samples=min_samples, prior_strength=prior_strength
      )
      signal_weights.write_weight_artifact(artifact, Path(output_path))
      return artifact
  ```
  Note the design choice from the "NOT Building" section: this trains on **all** settled rows with complete signal components, regardless of `board` (A vs B) — the composite is computed for every side in `cards.py` unconditionally, and restricting training to Board-B-labeled rows would introduce selection bias (Board B is defined as "not Board A", not a random sample) and needlessly shrink the training set.
  - Add `from outlier_scrapers import signal_weights` import to `feedback.py`.
  - Add a `fit-signal-weights` subcommand mirroring `fit-blend` exactly (CLI_SUBCOMMAND_PATTERN above): `--output` (default `signal_weights.DEFAULT_WEIGHTS_PATH`), `--min-samples` (default 30), `--prior-strength` (default 30.0), dispatch prints `{"output":..., "status":..., "model_version":..., "eligible_samples":...}`.
- **MIRROR**: `fit_blend_weights` (`feedback.py:1514-1529`), CLI_SUBCOMMAND_PATTERN.
- **IMPORTS**: `signal_weights` module (new).
- **GOTCHA**: `_joined_rows()` already does the settlement/decision/snapshot join — do not write a new SQL query. Confirm the existing query at `feedback.py:1480-1509` still selects the 4 component columns before relying on it (it does, per lines 1502-1504, but re-verify against current `feedback.py` at implementation time in case of drift).
- **VALIDATE**: `python -m outlier_scrapers.feedback fit-signal-weights --db path/to/test.sqlite3 --output /tmp/signal_weights.json` runs cleanly against a populated feedback DB (or the test DB used in `tests/test_feedback.py` fixtures, if one exists — check during implementation).

### Task 5: Extend `tests/test_cards.py`
- **ACTION**: Cover the new `weights` parameter and payload metadata without duplicating `tests/test_signal_weights.py`'s numeric-fitting tests (those belong in Task 2).
- **IMPLEMENT**:
  - `test_signal_score_accepts_custom_weights`: call `signal_score(side, sdata, mv, matched, weights={"hit": 1.0, "insight": 0.0, "movement": 0.0, "orf": 0.0})` and assert the composite equals the `hit_component` value alone (isolates that the parameter is actually wired through, not just accepted and ignored).
  - `test_signal_score_defaults_to_module_constant_when_no_weights_passed`: existing call-site behavior (no `weights` arg) still uses `SIGNAL_WEIGHTS` — regression guard for backward compatibility.
  - `test_build_cards_payload_falls_back_to_default_weights_without_artifact`: point `weights_path` at a nonexistent file, assert `payload["signal_weights"]["source"]` indicates the fallback and Board B ranking is byte-identical to before this change (use existing fixture data if `test_cards.py` has any `build_cards_payload` integration test to extend; otherwise construct a minimal one).
- **MIRROR**: Existing `test_cards.py` conventions (check the file's current fixture-setup style before writing — it wasn't fully read during planning; read it fully at implementation time).
- **IMPORTS**: whatever `test_cards.py` currently imports, plus `signal_weights` if directly exercising `resolve_signal_weights`.
- **GOTCHA**: `tests/test_pack.py:1925` has a regression-comment referencing `signal_score()`'s `hit_component` default-to-50.0 behavior — re-read that comment before touching `signal_score`'s defaulting logic, to make sure the new `weights` parameter doesn't interact badly with the existing "no Outlier recency data" default path.
- **VALIDATE**: `python -m pytest tests/test_cards.py tests/test_pack.py -v` — no regressions in either file.

### Task 6: Docs / operator runbook touch-up (optional, low-priority)
- **ACTION**: If `AI-research-desk-runbook.md` or `docs/feedback-loop.md` documents the `fit-blend` operator workflow (grep for it at implementation time — as of this plan's writing, neither file mentions `fit-blend` yet, so there may be nothing to mirror), add a one-line equivalent for `fit-signal-weights`. Skip entirely if there's no existing "how to retrain weights" doc section to extend — don't invent new documentation structure for this.
- **VALIDATE**: N/A (docs-only).

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_fit_signal_weights_converges_to_dominant_predictor` | `hit` perfectly separates W/L, others constant at 50 | `weights["hit"] > 0.9`, `brier_score ≈ 0` | No |
| `test_fit_weight_artifact_shrinks_toward_default_below_min_samples` | `n < min_samples` | `status == "insufficient_history"`, `weights == DEFAULT_WEIGHTS` | Yes — small sample |
| `test_fit_weight_artifact_shrinks_toward_default_with_prior` | `n` moderate, `prior_strength` large | weights close to `DEFAULT_WEIGHTS` | Yes — shrinkage dominance |
| `test_weight_artifact_round_trip` | fit → write → load | identical dict | No |
| `test_resolve_signal_weights_falls_back_when_artifact_missing` | `artifact=None` | `DEFAULT_WEIGHTS`, `source="prior_default_missing_artifact"` | Yes — missing file |
| `test_signal_score_accepts_custom_weights` | `weights={"hit":1.0,...:0.0}` | composite == hit_component | No |
| `test_signal_score_defaults_to_module_constant_when_no_weights_passed` | no `weights` arg | unchanged from pre-change behavior | Regression guard |
| `test_build_cards_payload_falls_back_to_default_weights_without_artifact` | nonexistent `weights_path` | Board B ranking unchanged; payload records fallback source | Yes — missing artifact end-to-end |

### Edge Cases Checklist
- [x] Empty input (`rows=[]` → `fit_weight_artifact` → `insufficient_history`)
- [x] Maximum/degenerate input (all 4 components identical for every row → gradient is zero, projected weights stay at `DEFAULT_WEIGHTS` initialization — verify this doesn't produce a divide-by-zero in the shrink/renormalize step)
- [x] Invalid types (`None`/missing component values excluded from training, mirrors `_training_pair`'s `None`-check pattern in `probability_blend.py:124-137`)
- [ ] Concurrent access — N/A, `fit-signal-weights` is a manual, offline, single-process CLI invocation like `fit-blend`.
- [ ] Network failure — N/A, no network calls in this feature.
- [x] Permission denied on artifact write — inherited from `write_weight_artifact`'s existing atomic-write behavior (will raise `OSError`, uncaught — matches `probability_blend.py`'s existing behavior, not a regression to fix here).

---

## Validation Commands

### Static Analysis
```bash
python -m ruff check outlier_scrapers/signal_weights.py outlier_scrapers/cards.py outlier_scrapers/feedback.py tests/test_signal_weights.py tests/test_cards.py
python -m mypy outlier_scrapers/signal_weights.py outlier_scrapers/feedback.py
```
EXPECT: Zero lint errors. Zero mypy errors (note: `cards.py` is mypy-excluded per `pyproject.toml`, so it won't be checked — Task 3's GOTCHA applies).

### Unit Tests
```bash
python -m pytest tests/test_signal_weights.py tests/test_cards.py tests/test_pack.py tests/test_probability_blend.py -v
```
EXPECT: All pass, including the pre-existing `test_probability_blend.py` suite (regression guard that this change didn't disturb the sibling blend-weight module).

### Full Test Suite
```bash
pytest
```
EXPECT: No regressions anywhere in the suite.

### Manual Validation
- [ ] Run `python -m outlier_scrapers.cards --league WNBA` with no `calibration/signal_weights.json` present — confirm output is byte-identical to the pre-change baseline (checkout the file on `master` before this branch, diff `cards_latest.json`).
- [ ] Populate a small test `feedback.sqlite3` with synthetic settled rows favoring one component, run `python -m outlier_scrapers.feedback fit-signal-weights --db <test.sqlite3> --output <tmp>/signal_weights.json`, confirm the artifact's `weights` shift in the expected direction.
- [ ] Re-run `python -m outlier_scrapers.cards --league WNBA --signal-weights <tmp>/signal_weights.json` and confirm Board B rankings shift accordingly (no crash, plausible ordering change).

---

## Acceptance Criteria
- [ ] All 6 tasks completed
- [ ] All validation commands pass
- [ ] Tests written and passing (Tasks 2, 5)
- [ ] No type errors (on the files mypy actually checks)
- [ ] No lint errors
- [ ] Board B ranking is unchanged when no artifact is present (backward-compat guarantee, explicitly tested)

## Completion Checklist
- [ ] Code follows discovered patterns (mirrors `probability_blend.py` and `pack.py`'s artifact-loading conventions)
- [ ] Error handling matches codebase style (fail-open to defaults, no raised exceptions on missing/corrupt artifacts)
- [ ] No logging added (codebase convention: diagnostics via return-value fields)
- [ ] Tests follow `test_probability_blend.py`'s structure
- [ ] No hardcoded values beyond the existing `DEFAULT_WEIGHTS` prior (which is intentionally the current hand-tuned composite, not a magic number — document why in the module docstring)
- [ ] Documentation updated only if an existing "how to retrain" doc section exists to extend (Task 6, low priority)
- [ ] No unnecessary scope additions — ORF/RLM/divergence/liquidity/per-player-Bayes/segmentation/DB-schema-migration/daily_job-scheduling all explicitly deferred per "NOT Building"
- [ ] Self-contained — no questions needed during implementation, except: (a) re-verify `_joined_rows()`'s exact column list hasn't drifted since this plan was written (Task 4 GOTCHA), and (b) read `tests/test_cards.py`'s current fixture conventions in full before writing Task 5's tests (not fully read during planning due to file size).

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Pure-Python projected-gradient-descent simplex fit has a subtle convergence bug (wrong step size, projection error) that silently produces bad weights | Medium | Medium — Board B is descriptive/internal, not customer-facing EV, so impact is a ranking quality regression, not a financial/correctness bug | Task 2's dominant-predictor test with a hand-verifiable expected outcome catches gross errors; the shrinkage-toward-default fallback bounds how far a bad fit can drift when `prior_strength` is nonzero |
| Not enough settled rows with all 4 components present yet (feature just shipped alongside PR #52 today) to produce a meaningful `fit-signal-weights` run | High (near-certain on first attempt) | Low — `insufficient_history` status is the designed fail-open path, board keeps using `DEFAULT_WEIGHTS` until enough data accumulates | No mitigation needed; this is expected and handled by design |
| `cards.py` being outside mypy's checked files means a `None`-handling bug in Task 3 goes undetected by static analysis | Medium | Low-Medium | Extra manual review of Task 3's changes; rely on the byte-identical-output manual validation step to catch behavioral regressions |

## Notes
- The user explicitly chose "mirror PR #52 for Board B weights" over the full Gemini-doc XL vision (Beta-Binomial per-player shrinkage, ORF training, RLM/divergence/liquidity ingestion, shadow deployment) during plan scoping on 2026-07-20. If a follow-on PRP is wanted for those larger pieces, they should each get their own plan — this one intentionally stays a single, mirrorable, Medium-complexity slice.
- `probability_blend.py` and this new `signal_weights.py` are siblings solving the same shrinkage problem at two different points in the pipeline (market/model blend weight vs. Board B composite weight). Consider, in a later cleanup pass (not this plan), whether the shared shrinkage-formula logic (`(n*raw + k*prior)/(n+k)`) is worth extracting into a common helper — deliberately not done here to avoid coupling two independently-evolving modules during their first parallel iteration.

> Next step: Run `/prp-implement .claude/PRPs/plans/board-b-empirical-signal-weights.plan.md` to execute this plan.
