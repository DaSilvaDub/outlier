"""Attach optional close / model_p fields onto NFL prediction snapshots.

Honest contract:
- ``best_odds`` / ``line`` / ``implied_probability`` at emit time are a *pregame
  snapshot*, not a true book closing line.
- Mode ``explicit`` only keeps close_* already present on the row.
- Mode ``snapshot_best`` copies line/best_odds/implied into close_* and labels
  ``close_source="pregame_snapshot_best_odds"`` so CLV is measurable but not
  claimed as Pinnacle/book close.
- Mode ``book_close`` merges a real close feed (JSON) and stamps
  ``close_source="book_close"``. Refuses to copy snapshot odds as book close.
- ``model_p`` / ``p_model`` are never copied from ``implied_probability``.
  Attach modes: pass | empirical_hit_rate | empirical_hit_rate_laplace |
  empirical_hit_rate_beta | projection_nflverse_rate | hierarchy.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from outlier_nfl.calibration import (
    DEFAULT_LAPLACE_ALPHA,
    MODEL_P_SOURCE_EMPIRICAL_HIT_RATE,
    MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_BETA,
    MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE,
    attach_empirical_model_p_record,
)
from outlier_nfl.projection import (
    MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE,
    attach_model_p_hierarchy_record,
    attach_projection_model_p_record,
    load_week_stats_index,
)
from outlier_nfl.utils import safe_read_json, safe_write_json

CLOSE_SOURCE_SNAPSHOT = "pregame_snapshot_best_odds"
CLOSE_SOURCE_BOOK = "book_close"

ATTACH_MODEL_P_MODES = (
    "pass",
    "empirical_hit_rate",
    "empirical_hit_rate_laplace",
    "empirical_hit_rate_beta",
    "projection_nflverse_rate",
    "hierarchy",
)

CLOSE_MODES = ("explicit", "snapshot_best", "book_close")


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


def _row_match_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("player_name") or "").strip().upper(),
        str(row.get("market") or "").strip().upper(),
        _as_float(row.get("line")),
        str(row.get("position") or "").strip().upper(),
        str(row.get("matchup") or "").strip().upper(),
        str(row.get("event_id") or "").strip(),
    )


def load_book_close_feed(path: Path) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Load a book-close JSON feed keyed for join onto prediction rows.

    Accepted shapes:
    - ``{"records": [ {player_name, market, line, position, ..., close_line,
      close_odds, close_implied}, ... ]}``
    - a bare list of the same row objects
    """
    payload = safe_read_json(path)
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, Mapping):
        records = payload.get("records") or payload.get("closes") or []
    else:
        raise ValueError(f"Unsupported book-close feed shape: {path}")
    if not isinstance(records, list):
        raise ValueError(f"Book-close feed missing records list: {path}")
    index: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        close_line = _as_float(raw.get("close_line", raw.get("line")))
        close_odds = _as_int(raw.get("close_odds", raw.get("odds", raw.get("price"))))
        close_implied = _as_float(
            raw.get("close_implied", raw.get("implied_probability", raw.get("implied")))
        )
        if close_line is None and close_odds is None and close_implied is None:
            continue
        index[_row_match_key(raw)] = {
            "close_line": close_line,
            "close_odds": close_odds,
            "close_implied": close_implied,
            "close_source": CLOSE_SOURCE_BOOK,
        }
    return index


def attach_close_fields(
    record: MutableMapping[str, Any],
    *,
    mode: str = "explicit",
    attach_model_p: str | None = None,
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
    week_index: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    book_close_index: Mapping[tuple[Any, ...], Mapping[str, Any]] | None = None,
    overwrite_model_p: bool = False,
) -> dict[str, Any]:
    """Mutate and return a prop record with optional close_* / model_p fields."""
    if mode not in CLOSE_MODES:
        raise ValueError(f"Unsupported close attach mode: {mode}")
    model_mode = attach_model_p or "pass"
    if model_mode not in ATTACH_MODEL_P_MODES:
        raise ValueError(f"Unsupported attach_model_p mode: {attach_model_p}")

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
        # Never invent book_close when source is missing.
        if has_close and not record.get("close_source"):
            record.setdefault("close_source", None)

    elif mode == "snapshot_best":
        if not has_close:
            record["close_line"] = _as_float(record.get("line"))
            record["close_odds"] = _as_int(record.get("best_odds"))
            record["close_implied"] = _as_float(record.get("implied_probability"))
            record["close_source"] = CLOSE_SOURCE_SNAPSHOT
        elif not record.get("close_source"):
            # Existing close_* without a source stay unlabeled — do not claim book_close.
            record["close_source"] = None

    elif mode == "book_close":
        if book_close_index is None:
            raise ValueError(
                "book_close mode requires a close feed index; refusing to label "
                "snapshot odds as book_close."
            )
        feed_row = book_close_index.get(_row_match_key(record))
        if feed_row:
            record["close_line"] = feed_row.get("close_line")
            record["close_odds"] = feed_row.get("close_odds")
            record["close_implied"] = feed_row.get("close_implied")
            record["close_source"] = CLOSE_SOURCE_BOOK
        else:
            # Do not fall back to snapshot under a book_close label.
            if record.get("close_source") == CLOSE_SOURCE_SNAPSHOT or not has_close:
                record["close_line"] = None
                record["close_odds"] = None
                record["close_implied"] = None
                record["close_source"] = None
            elif has_close and record.get("close_source") != CLOSE_SOURCE_BOOK:
                # Preserve pre-existing non-book closes only if already stamped.
                pass

    if model_mode == "pass":
        record.setdefault("model_p", record.get("model_p"))
        record.setdefault("model_p_source", record.get("model_p_source"))
    elif model_mode == "empirical_hit_rate":
        attach_empirical_model_p_record(
            record, overwrite=overwrite_model_p, method="raw"  # type: ignore[arg-type]
        )
    elif model_mode == "empirical_hit_rate_laplace":
        attach_empirical_model_p_record(
            record,
            overwrite=overwrite_model_p,
            method="laplace",
            alpha=alpha,
            beta=None,
        )
    elif model_mode == "empirical_hit_rate_beta":
        attach_empirical_model_p_record(
            record,
            overwrite=overwrite_model_p,
            method="beta",
            alpha=alpha,
            beta=beta,
        )
    elif model_mode == "projection_nflverse_rate":
        if week_index is None:
            raise ValueError(
                "projection_nflverse_rate requires --nflverse-week-stats "
                "(and --before-week); will not invent projection p."
            )
        # Clear stale empirical so projection can fill; fall back handled by caller hierarchy.
        if overwrite_model_p or record.get("model_p_source") in {
            None,
            MODEL_P_SOURCE_EMPIRICAL_HIT_RATE,
            MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE,
            MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_BETA,
        }:
            record.pop("model_p", None)
            record.pop("model_p_source", None)
            record.pop("p_model", None)
        attach_projection_model_p_record(
            record, week_index=week_index, overwrite=True, alpha=alpha
        )
        if record.get("model_p") is None:
            # Honest: leave empty rather than silently using market.
            record.setdefault("model_p", None)
            record.setdefault("model_p_source", None)
    elif model_mode == "hierarchy":
        attach_model_p_hierarchy_record(
            record,
            week_index=week_index,
            overwrite=True,
            alpha=alpha,
        )

    return dict(record)


def _model_p_note(model_mode: str, alpha: float, beta: float | None) -> str:
    if model_mode == "empirical_hit_rate":
        return (
            f"model_p from L10/L20/L5/season hit rates "
            f"(source={MODEL_P_SOURCE_EMPIRICAL_HIT_RATE}); never copied from "
            "implied_probability."
        )
    if model_mode == "empirical_hit_rate_laplace":
        return (
            f"Laplace/add-α shrink of empirical hit rates (α={alpha}, "
            f"source={MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE}); never market."
        )
    if model_mode == "empirical_hit_rate_beta":
        return (
            f"Beta(α,β) shrink of empirical hit rates (α={alpha}, β={beta}, "
            f"source={MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_BETA}); never market."
        )
    if model_mode == "projection_nflverse_rate":
        return (
            f"nflverse prior-week gamelog rate vs line "
            f"(source={MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE}); no market copy; "
            "rows without prior weeks left empty."
        )
    if model_mode == "hierarchy":
        return (
            "hierarchy: projection_nflverse_rate → empirical_hit_rate_laplace → "
            "empirical_hit_rate; never copies implied_probability."
        )
    return "pass-through only; model_p not derived from hit rates."


def enrich_prediction_payload(
    payload: Mapping[str, Any],
    *,
    mode: str = "snapshot_best",
    attach_model_p: str | None = "empirical_hit_rate_laplace",
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
    week_index: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    book_close_index: Mapping[tuple[Any, ...], Mapping[str, Any]] | None = None,
    overwrite_model_p: bool = False,
) -> dict[str, Any]:
    """Return a shallow-copied artifact with close / model_p fields attached."""
    records_in = payload.get("records")
    if not isinstance(records_in, list):
        raise ValueError("Prediction payload missing records list")
    if mode == "book_close" and book_close_index is None:
        raise ValueError(
            "book_close mode requires a close feed; refusing to label snapshot "
            "odds as book_close."
        )
    model_mode = attach_model_p or "pass"
    records_out: list[dict[str, Any]] = []
    n_model = 0
    n_book = 0
    sources: dict[str, int] = {}
    for raw in records_in:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        attach_close_fields(
            row,
            mode=mode,
            attach_model_p=model_mode,
            alpha=alpha,
            beta=beta,
            week_index=week_index,
            book_close_index=book_close_index,
            overwrite_model_p=overwrite_model_p,
        )
        if row.get("model_p") is not None:
            n_model += 1
            src = str(row.get("model_p_source") or "unknown")
            sources[src] = sources.get(src, 0) + 1
        if row.get("close_source") == CLOSE_SOURCE_BOOK:
            n_book += 1
        records_out.append(row)
    out = dict(payload)
    out["records"] = records_out
    close_note = {
        "snapshot_best": (
            "snapshot_best copies line/best_odds/implied into close_* labeled "
            f"{CLOSE_SOURCE_SNAPSHOT}; this is not a true book closing line."
        ),
        "explicit": "explicit mode does not copy best_odds into close_*.",
        "book_close": (
            f"book_close merges an external close feed and stamps "
            f"{CLOSE_SOURCE_BOOK}; unmatched rows have close_* cleared rather "
            "than silently using snapshot odds."
        ),
    }[mode]
    out["close_enrichment"] = {
        "mode": mode,
        "n_book_close": n_book,
        "note": close_note,
    }
    out["model_p_enrichment"] = {
        "mode": model_mode,
        "alpha": alpha,
        "beta": beta,
        "n_with_model_p": n_model,
        "by_source": sources,
        "note": _model_p_note(model_mode, alpha, beta),
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
        choices=CLOSE_MODES,
        default="snapshot_best",
        help=(
            "snapshot_best copies emit-time odds into close_* with an honest "
            "source label; book_close requires --close-feed and never labels "
            "snapshot as book_close."
        ),
    )
    parser.add_argument(
        "--close-feed",
        type=Path,
        default=None,
        help="JSON feed of real book closes (required for --mode book_close).",
    )
    parser.add_argument(
        "--attach-model-p",
        choices=ATTACH_MODEL_P_MODES,
        default="empirical_hit_rate_laplace",
        help=(
            "empirical_hit_rate_laplace (default α=2) shrinks L10/L20/L5/season; "
            "hierarchy prefers nflverse projection then Laplace then raw; "
            "never from implied_probability."
        ),
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_LAPLACE_ALPHA,
        help=f"Laplace/Beta α prior strength (default {DEFAULT_LAPLACE_ALPHA}).",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=None,
        help="Beta prior β (defaults to α when omitted; only used for beta mode).",
    )
    parser.add_argument(
        "--nflverse-week-stats",
        type=Path,
        default=None,
        help="stats_player_week CSV for projection / hierarchy modes.",
    )
    parser.add_argument(
        "--before-week",
        type=int,
        default=None,
        help="Exclusive upper week bound for projection (slate week).",
    )
    parser.add_argument(
        "--overwrite-model-p",
        action="store_true",
        help="Replace an existing model_p on the rows.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.mode == "book_close" and args.close_feed is None:
        from outlier_nfl.close_feed import close_feed_blocker_message

        raise SystemExit(
            "error: --mode book_close requires --close-feed PATH. "
            "Refusing to label snapshot odds as book_close. "
            + close_feed_blocker_message()
        )

    book_index = None
    if args.close_feed is not None:
        book_index = load_book_close_feed(args.close_feed)

    week_index = None
    if args.nflverse_week_stats is not None:
        if args.before_week is None or args.before_week < 2:
            raise SystemExit(
                "error: --nflverse-week-stats requires --before-week >= 2 "
                "(slate week) so prior weeks do not leak."
            )
        week_index = load_week_stats_index(
            args.nflverse_week_stats, before_week=args.before_week
        )

    if args.attach_model_p in {"projection_nflverse_rate", "hierarchy"} and week_index is None:
        if args.attach_model_p == "projection_nflverse_rate":
            raise SystemExit(
                "error: projection_nflverse_rate requires --nflverse-week-stats "
                "and --before-week; will not invent projection p."
            )
        # hierarchy may still run Laplace/raw without week stats
        pass

    payload = safe_read_json(args.predictions)
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected object JSON: {args.predictions}")
    enriched = enrich_prediction_payload(
        payload,
        mode=args.mode,
        attach_model_p=args.attach_model_p,
        alpha=args.alpha,
        beta=args.beta,
        week_index=week_index,
        book_close_index=book_index,
        overwrite_model_p=args.overwrite_model_p
        or args.attach_model_p
        in {
            "empirical_hit_rate_laplace",
            "empirical_hit_rate_beta",
            "projection_nflverse_rate",
            "hierarchy",
        },
    )
    safe_write_json(args.out, enriched)
    mp = enriched.get("model_p_enrichment") or {}
    ce = enriched.get("close_enrichment") or {}
    print(
        f"Wrote {args.out} records={enriched.get('count') or len(enriched.get('records') or [])} "
        f"mode={args.mode} attach_model_p={args.attach_model_p} "
        f"n_model_p={mp.get('n_with_model_p')} by_source={mp.get('by_source')} "
        f"n_book_close={ce.get('n_book_close')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
