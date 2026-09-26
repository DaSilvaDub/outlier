"""Attach optional close / model_p fields onto NFL prediction snapshots.

Honest contract:
- ``best_odds`` / ``line`` / ``implied_probability`` at emit time are a *pregame
  snapshot*, not a true book closing line.
- Mode ``explicit`` only keeps close_* already present on the row.
- Mode ``snapshot_best`` copies line/best_odds/implied into close_* and labels
  ``close_source="pregame_snapshot_best_odds"`` so CLV is measurable but not
  claimed as Pinnacle/book close.
- ``model_p`` / ``p_model`` are never invented; pass-through only.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from outlier_nfl.utils import safe_read_json, safe_write_json

CLOSE_SOURCE_SNAPSHOT = "pregame_snapshot_best_odds"
CLOSE_SOURCE_BOOK = "book_close"


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def attach_close_fields(
    record: MutableMapping[str, Any],
    *,
    mode: str = "explicit",
) -> dict[str, Any]:
    """Mutate and return a prop record with optional close_* fields.

    mode:
      - explicit: leave close_* as-is (or None); never copy from best_odds
      - snapshot_best: if close_* missing, copy line/best_odds/implied and tag source
    """
    if mode not in {"explicit", "snapshot_best"}:
        raise ValueError(f"Unsupported close attach mode: {mode}")

    # Pass-through aliases for model probability without inventing.
    if record.get("model_p") is None and record.get("p_model") is not None:
        record["model_p"] = record.get("p_model")

    has_close = any(
        record.get(key) is not None for key in ("close_line", "close_odds", "close_implied")
    )
    if mode == "explicit":
        if not has_close:
            record.setdefault("close_line", None)
            record.setdefault("close_odds", None)
            record.setdefault("close_implied", None)
            record.setdefault("close_source", None)
        record.setdefault("model_p", record.get("model_p"))
        return dict(record)

    # snapshot_best
    if not has_close:
        record["close_line"] = _as_float(record.get("line"))
        record["close_odds"] = _as_int(record.get("best_odds"))
        record["close_implied"] = _as_float(record.get("implied_probability"))
        record["close_source"] = CLOSE_SOURCE_SNAPSHOT
    else:
        record.setdefault("close_source", record.get("close_source") or CLOSE_SOURCE_BOOK)
    record.setdefault("model_p", record.get("model_p"))
    return dict(record)


def enrich_prediction_payload(
    payload: Mapping[str, Any],
    *,
    mode: str = "snapshot_best",
) -> dict[str, Any]:
    """Return a shallow-copied artifact with close fields attached per record."""
    records_in = payload.get("records")
    if not isinstance(records_in, list):
        raise ValueError("Prediction payload missing records list")
    records_out: list[dict[str, Any]] = []
    for raw in records_in:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        attach_close_fields(row, mode=mode)
        records_out.append(row)
    out = dict(payload)
    out["records"] = records_out
    out["close_enrichment"] = {
        "mode": mode,
        "note": (
            "snapshot_best copies line/best_odds/implied into close_* labeled "
            f"{CLOSE_SOURCE_SNAPSHOT}; this is not a true book closing line."
            if mode == "snapshot_best"
            else "explicit mode does not copy best_odds into close_*."
        ),
    }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Attach optional close_* / model_p fields onto NFL prediction JSON."
    )
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("explicit", "snapshot_best"),
        default="snapshot_best",
        help="snapshot_best copies emit-time odds into close_* with an honest source label.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    payload = safe_read_json(args.predictions)
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected object JSON: {args.predictions}")
    enriched = enrich_prediction_payload(payload, mode=args.mode)
    safe_write_json(args.out, enriched)
    print(
        f"Wrote {args.out} records={enriched.get('count') or len(enriched.get('records') or [])} "
        f"mode={args.mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
