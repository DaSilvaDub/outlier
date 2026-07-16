# Historical Edge Percentage Implementation Plan (corrected)

> **For the executing agent:** Follow this plan task-by-task, in order, exactly as written.
> If a step's expected output does not match reality, STOP and report — do not improvise.
> (Claude executors: use the superpowers:executing-plans skill.)

**Goal:** Add a `historical_edge_pct` column to `candidates.csv` that shows, per candidate, the edge implied by the raw recency hit rate alone — for manual eyeballing only, never for sizing.

**Architecture:** One pure function in `outlier_scrapers/sizing.py` (next to `compute_sizing`, which owns the edge convention), wired into `build_row` in `outlier_scrapers/pack.py`. No database changes: `feedback.py` ingestion maps fields explicitly by name, so it ignores new CSV columns, and the value stays derivable from columns the DB already stores. The canonical CSV retains the descriptive field for human review, while `runner_common.filter_candidates_for_ai` removes it from AI-facing candidate payloads, including standalone manual prompt exports.

**Tech Stack:** Python 3, pytest. Repo commands: `pytest`, `python -m ruff check`, `python -m mypy outlier_scrapers`.

## Final implementation alignment

Post-review hardening standardized `compute_historical_edge` on the same 0–1 probability scale used by `compute_sizing`. `pack.py` converts raw `signal["hit_pct"]` values from 0–100 at the call boundary. Missing, invalid, NaN, and infinite inputs return `None`. The human-facing CSV keeps `historical_edge_pct`; every AI-facing candidate projection strips it.

---

## Why this plan differs from the original draft

The original draft (Antigravity brain `a0ac8609.../implementation_plan.md`) was reviewed against the code on 2026-07-15 and had these problems, all fixed here:

1. **Fabricated edges (HIGH):** it computed the edge from `hit_rate_component`, but `cards.py:278` (`signal_score`) substitutes a neutral **50.0 when there is no recency data at all**. A no-data candidate at +120 would have shown a fake +10% "historical edge". Fix: `signal_score` already returns the raw nullable value as `hit_pct` (`cards.py:292`) — use that, and emit blank when it is `None`.
2. **Unnecessary DB work:** `feedback.py` builds its snapshot dict explicitly per field (`feedback.py:911`), so a new CSV column cannot "crash the database". The schema/migration/insert changes are dropped entirely (YAGNI).
3. **Wrong identifier names:** `CSV_HEADERS` → `CANDIDATES_HEADER` (pack.py:34); `SNAPSHOT_COLUMNS` → `MARKET_SNAPSHOT_FIELDS`; `SNAPSHOT_COLUMN_DEFINITIONS` → `MARKET_SNAPSHOT_COLUMN_DEFINITIONS` (moot now, but noted).
4. **Formula consistency:** the canonical edge (`sizing.py:85`) is `p_win * b - p_lose`, a **fraction** (despite the `_pct` name) and **push-aware**. The new column uses the identical convention so the two edge columns are directly comparable.
5. **Known, accepted side effect:** `runner_common.py:252` raises `RunnerError` when a `candidates.csv` header differs from `pack.CANDIDATES_HEADER`. After this change, packs generated **before** it will fail that check. Accepted: packs are daily artifacts. Do NOT relax the check. Mention this in the final commit/PR body.

**Hard constraint (from review):** `historical_edge_pct` is descriptive only. It must never feed `compute_sizing`, `recommended_units_pre_news`, `actionable`, or board selection. Recent hit rates are selection-biased (candidates surface *because* they're extreme) and regress to the mean, so this number systematically overestimates true edge.

---

### Task 1: Pure helper `compute_historical_edge` in sizing.py

**Files:**
- Modify: `outlier_scrapers/sizing.py` (append at end of file)
- Test: `tests/test_sizing.py` (append at end of file)

**Step 1: Write the failing tests**

Append to `tests/test_sizing.py` (check the top of the file: if `pytest` is not already imported, add `import pytest`; extend the existing `from outlier_scrapers.sizing import ...` import with `compute_historical_edge`):

```python
def test_historical_edge_known_value():
    # 60% hit rate at even money: 0.6 * 1.0 - 0.4 = +0.20 (fraction, like edge_pct)
    assert compute_historical_edge(0.60, 2.0) == pytest.approx(0.2)


def test_historical_edge_push_aware():
    # Push mass shrinks p_lose: 0.6 * 1.0 - (1 - 0.6 - 0.1) = +0.30
    assert compute_historical_edge(0.60, 2.0, push_prob=0.1) == pytest.approx(0.3)


def test_historical_edge_missing_hit_rate_is_none():
    # Regression guard: missing recency data must yield None, never a
    # neutral-50 fabricated edge.
    assert compute_historical_edge(None, 2.0) is None


def test_historical_edge_missing_price_is_none():
    assert compute_historical_edge(0.60, None) is None


def test_historical_edge_degenerate_price_is_none():
    assert compute_historical_edge(0.60, 1.0) is None
    assert compute_historical_edge(0.60, 0.5) is None


def test_historical_edge_inconsistent_push_is_none():
    # p_win + push > 1 is an invalid partition (same rule as compute_sizing).
    assert compute_historical_edge(0.95, 2.0, push_prob=0.10) is None


def test_historical_edge_out_of_range_hit_is_none():
    assert compute_historical_edge(1.20, 2.0) is None
    assert compute_historical_edge(-0.05, 2.0) is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sizing.py -v -k historical_edge`
Expected: FAIL / ERROR with `ImportError: cannot import name 'compute_historical_edge'`

**Step 3: Write the implementation**

Add `from math import isfinite` with the imports, then append to `outlier_scrapers/sizing.py`:

```python
def compute_historical_edge(
    hit_rate_prob: float | None,
    decimal_price: float | None,
    push_prob: float | None = None,
) -> float | None:
    """Edge implied by the raw recency hit rate alone. Descriptive only.

    Same convention as compute_sizing: probability inputs are fractions
    and the result is a fraction (p_win * b - p_lose). Returns None when
    inputs are missing, non-finite, or form an invalid partition.

    Callers must convert the raw nullable hit rate (signal["hit_pct"],
    expressed from 0 to 100) to a probability before calling. Never use
    hit_rate_component: its 50.0 default stands in for missing data and
    would fabricate an edge. This value must never feed sizing.
    """
    if hit_rate_prob is None or decimal_price is None:
        return None
    push = push_prob if push_prob is not None else 0.0
    if not all(isfinite(value) for value in (hit_rate_prob, decimal_price, push)):
        return None
    if decimal_price <= 1.0:
        return None
    p_win = hit_rate_prob
    if p_win < 0.0 or p_win > 1.0:
        return None
    p_lose = 1.0 - p_win - push
    if push < 0.0 or p_lose < 0.0:
        return None
    return p_win * (decimal_price - 1.0) - p_lose
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sizing.py -v`
Expected: all PASS (new tests and all pre-existing sizing tests).

**Step 5: Commit**

```bash
git add outlier_scrapers/sizing.py tests/test_sizing.py
git commit -m "feat(sizing): add compute_historical_edge helper

Push-aware, fraction-scaled (matches edge_pct convention). Returns None
for missing/invalid inputs instead of defaulting - guards against the
hit_rate_component neutral-50 missing-data sentinel."
```

---

### Task 2: Wire the column into pack.py

**Files:**
- Modify: `outlier_scrapers/pack.py` — `CANDIDATES_HEADER` (line ~63) and `build_row` (line ~666)
- Test: `tests/test_pack.py` (append; reuses existing `make_row` / `ev_card` helpers defined near the top of the file)

**Step 1: Write the failing tests**

Append to `tests/test_pack.py`. Also extend the existing `from outlier_scrapers.pack import (...)` block — no new names needed from pack — and add this import line after it:

```python
from outlier_scrapers.sizing import compute_historical_edge
```

```python
# historical_edge_pct: descriptive edge from the raw recency hit rate.
def test_historical_edge_pct_column_position():
    # Sits right after edge_pct so the two are adjacent when eyeballing the CSV.
    assert (
        CANDIDATES_HEADER[CANDIDATES_HEADER.index("edge_pct") + 1]
        == "historical_edge_pct"
    )
    # Must not displace the pinned last column.
    assert CANDIDATES_HEADER[-1] == "source_timestamps"


def test_historical_edge_pct_blank_when_hit_data_missing():
    # Regression: signal_score() defaults hit_component to 50.0 when Outlier
    # has no recency data. That sentinel must NOT leak into historical_edge_pct.
    card = ev_card()
    card["sides"]["OVER"]["signal"] = {"hit_component": 50.0, "hit_pct": None}
    row = make_row(card, [])
    assert row is not None
    assert row["historical_edge_pct"] == ""


def test_historical_edge_pct_populated_from_raw_hit_pct():
    card = ev_card()
    card["sides"]["OVER"]["signal"] = {"hit_component": 62.0, "hit_pct": 62.0}
    row = make_row(card, [])
    assert row is not None
    dec = float(row["decimal_price"])
    push = float(row["push_prob"]) if row["push_prob"] not in ("", None) else 0.0
    expected = compute_historical_edge(0.62, dec, push)
    assert expected is not None
    assert row["historical_edge_pct"] != ""
    assert float(row["historical_edge_pct"]) == pytest.approx(expected, abs=1e-4)
```

Note: the populated test derives `expected` from the row's own `decimal_price`/`push_prob` on purpose — it verifies the *plumbing* (that `build_row` feeds the row's values into the helper); the *math* is pinned by Task 1's unit tests.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pack.py -v -k historical_edge`
Expected: 3 FAIL — the position test with an `IndexError`/assertion on the missing column, the other two with `KeyError: 'historical_edge_pct'` or `''` mismatches.

**Step 3: Implement**

3a. In `CANDIDATES_HEADER` (`outlier_scrapers/pack.py:34`), insert `"historical_edge_pct",` immediately after the `"edge_pct",` entry (line ~63):

```python
    "implied_prob",
    "edge_pct",
    "historical_edge_pct",
    "kelly_025_units",
```

3b. Extend the existing sizing import near the top of pack.py (it currently imports `compute_sizing`; match the file's import style) to also import `compute_historical_edge`.

3c. In `build_row`, inside the signal block, immediately after the `row["orf_component"] = signal.get("orf_component", "")` line (~pack.py:666), add:

```python
    # Descriptive-only: edge implied by the raw recency hit rate. Reads
    # signal["hit_pct"] (None when Outlier had no recency data), NOT
    # hit_rate_component, whose 50.0 no-data default would fabricate an edge.
    hit_rate_pct = _to_float(signal.get("hit_pct"))
    hist_edge = compute_historical_edge(
        hit_rate_prob=hit_rate_pct / 100.0 if hit_rate_pct is not None else None,
        decimal_price=_to_float(row.get("decimal_price")),
        push_prob=_to_float(row.get("push_prob")),
    )
    row["historical_edge_pct"] = round(hist_edge, 4) if hist_edge is not None else ""
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pack.py tests/test_sizing.py tests/test_runner_common.py tests/test_feedback.py -v`
Expected: all PASS. (runner_common and feedback fixtures build their CSVs from `pack.CANDIDATES_HEADER` programmatically, so they absorb the new column — if any of them fail, STOP and report rather than editing the strict header check in `runner_common.py:252`.)

**Step 5: Commit**

```bash
git add outlier_scrapers/pack.py tests/test_pack.py
git commit -m "feat(pack): add historical_edge_pct to candidates.csv

Descriptive-only edge from the raw recency hit rate (signal.hit_pct),
placed next to edge_pct. Blank when recency data is missing - the
hit_rate_component 50.0 sentinel is deliberately not used. Does not
feed sizing/actionable.

Note: candidates.csv packs generated before this change no longer pass
the strict header check in runner_common (accepted; packs are daily
artifacts)."
```

---

### Task 3: Keep descriptive data out of AI payloads

**Files:**
- Modify: `outlier_scrapers/runner_common.py`
- Modify: `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`
- Test: `tests/test_runner_common.py`

Add `historical_edge_pct` to an explicit AI-excluded field set and project candidate CSV bytes through `filter_candidates_for_ai` before building any automated or manual prompt. Test that the canonical human CSV retains the field, the AI projection removes both its header and values, malformed CSV still raises `RunnerError`, and automated/manual prompt paths use the filtered bytes.

---

### Task 4: Full verification suite

**Step 1:** Run: `python -m ruff check`
Expected: no new violations (fix trivial ones like import order if flagged; nothing else).

**Step 2:** Run: `python -m mypy outlier_scrapers`
Expected: clean (helper is fully annotated).

**Step 3:** Run: `pytest`
Expected: full suite PASS. All reasoning-desk tests in this repo are offline-mocked; do NOT set any provider API keys, and if any test attempts a live network call to OpenAI/Anthropic/Gemini, STOP and report (house rule: no reasoning providers).

**Step 4 (optional manual, only if today's scraped feeds exist locally):**
Run: `python -m outlier_scrapers.pack --date 2026-07-15`
Then open the generated `candidates.csv`: rows with recency data show a small fraction (e.g. `0.1836`) in `historical_edge_pct`; rows without recency data show blank. If most rows are blank, that is expected — it means Outlier had no L5/L10 data for them, which is precisely the honesty this column is for.

**Step 5:** Commit anything from steps 1–2 if files changed; otherwise done.

---

## Explicit non-goals (do not do these)

- No changes to `outlier_scrapers/feedback.py` (schema, migrations, inserts, views).
- No changes to `outlier_scrapers/cards.py` (`hit_pct` is already exposed).
- No relaxation of the `runner_common.py:252` header check.
- No use of `historical_edge_pct` in sizing, boards, `actionable`, or prompts; AI-facing exports must remove the field.
- No runs of the reasoning desk / `run_desk` / `daily_job --run-reasoning` / live provider calls.
