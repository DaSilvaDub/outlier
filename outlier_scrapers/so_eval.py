"""Offline walk-forward eval for starter-SO independent probs vs market.

Reads the local feedback ledger only. No network and no desk/reasoning.
Does not mutate pack sizing config or portfolio risk settings.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from outlier_scrapers.paths import PROJECT_ROOT
from outlier_scrapers.so_sizing_blend import temper_independent_prob

DEFAULT_DB = PROJECT_ROOT / "calibration" / "feedback.sqlite3"
GAMELOG_HASH = "so-starter-gamelog-v2"
GAMELOG_HASHES = frozenset({"so-starter-gamelog-v1", "so-starter-gamelog-v2"})
DEFAULT_SOFT_RELIABILITY = 0.45
DEFAULT_TEMPER_WEIGHT = 0.55


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _brier(prob: float, outcome: float) -> float:
    return (prob - outcome) ** 2


def _is_so_selection(selection: str, market_type: str) -> bool:
    market = str(market_type or "").upper()
    text = str(selection or "").upper()
    return market in {"SO", "STRIKEOUTS", "K", "PITCHER_STRIKEOUTS", "PLAYER_PROP"} and (
        "STRIKEOUT" in text or market in {"SO", "STRIKEOUTS", "K", "PITCHER_STRIKEOUTS"}
    )


def _soften_prob(independent: float, reliability: float) -> float:
    from outlier_scrapers.projections import soften_so_win_probability

    soft = soften_so_win_probability(
        {"win_prob": independent, "push_prob": 0.0, "loss_prob": 1.0 - independent},
        reliability=reliability,
    )
    win_prob: Any = soft["win_prob"]
    return float(win_prob)


def evaluate_so_probs(
    db_path: Path = DEFAULT_DB,
    *,
    require_gamelog_hash: bool = False,
    require_v2_hash: bool = False,
    include_tempered: bool = False,
    soft_reliability: float = DEFAULT_SOFT_RELIABILITY,
    temper_independent_weight: float = DEFAULT_TEMPER_WEIGHT,
) -> dict[str, Any]:
    """Compare independent vs market Brier on settled SO rows.

    When ``require_v2_hash`` is set, only ``so-starter-gamelog-v2`` rows enter
    the prefer/Brier metrics (promotion gate must not unlock on v1 wins).
    Soft Brier is diagnostic only; tempered matches live Kelly
    (``temper(raw_indep, market)``).
    """
    if not db_path.exists():
        return {"status": "missing_db", "db_path": str(db_path), "n": 0}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()
    }
    hash_expr = (
        "s.projection_feature_hash"
        if "projection_feature_hash" in columns
        else "NULL AS projection_feature_hash"
    )
    source_expr = (
        "s.model_prob_source" if "model_prob_source" in columns else "NULL AS model_prob_source"
    )
    # Settlements key to snapshots via snapshot_id (preferred) or decision_id.
    snap_cols = columns
    try:
        if "snapshot_id" in snap_cols:
            rows = conn.execute(
                f"""
                SELECT
                    s.selection,
                    s.market_type,
                    s.market_consensus_prob,
                    s.independent_model_prob,
                    {hash_expr},
                    {source_expr},
                    x.win_loss_push
                FROM market_snapshots s
                JOIN settlements x ON x.snapshot_id = s.snapshot_id
                WHERE x.win_loss_push IN ('W', 'L', 'PUSH')
                """
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT
                    s.selection,
                    s.market_type,
                    s.market_consensus_prob,
                    s.independent_model_prob,
                    {hash_expr},
                    {source_expr},
                    x.win_loss_push
                FROM market_snapshots s
                JOIN decisions d ON d.snapshot_id = s.snapshot_id
                JOIN settlements x ON x.decision_id = d.decision_id
                WHERE x.win_loss_push IN ('W', 'L', 'PUSH')
                """
            ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return {
            "status": "schema_mismatch",
            "db_path": str(db_path),
            "n": 0,
            "error": str(exc),
        }
    conn.close()

    paired: list[dict[str, Any]] = []
    for row in rows:
        if not _is_so_selection(str(row["selection"] or ""), str(row["market_type"] or "")):
            continue
        market = _float(row["market_consensus_prob"])
        independent = _float(row["independent_model_prob"])
        result = str(row["win_loss_push"] or "").upper()
        if market is None or independent is None or result == "PUSH":
            continue
        digest = str(row["projection_feature_hash"] or "")
        if require_v2_hash and digest != GAMELOG_HASH:
            continue
        if require_gamelog_hash and digest not in GAMELOG_HASHES:
            continue
        # Prefer reporting latest-hash count separately in aggregates below.

        outcome = 1.0 if result == "W" else 0.0
        item: dict[str, Any] = {
            "selection": row["selection"],
            "market": market,
            "independent": independent,
            "outcome": outcome,
            "feature_hash": digest,
            "market_brier": _brier(market, outcome),
            "independent_brier": _brier(independent, outcome),
        }
        if include_tempered:
            soft = _soften_prob(independent, soft_reliability)
            # Live Kelly temper uses raw independent, not soft — keep gate aligned.
            tempered = temper_independent_prob(
                independent, market, independent_weight=temper_independent_weight
            )
            item["soft"] = soft
            item["tempered"] = tempered
            item["soft_brier"] = _brier(soft, outcome)
            item["tempered_brier"] = _brier(tempered, outcome)
        paired.append(item)

    n = len(paired)
    if n == 0:
        return {
            "status": "insufficient_settled_so",
            "db_path": str(db_path),
            "n": 0,
            "note": "Need settled SO rows with both market_consensus_prob and independent_model_prob.",
        }

    market_brier = sum(item["market_brier"] for item in paired) / n
    independent_brier = sum(item["independent_brier"] for item in paired) / n
    market_hits = sum(1 for item in paired if (item["market"] >= 0.5) == (item["outcome"] == 1.0))
    independent_hits = sum(
        1 for item in paired if (item["independent"] >= 0.5) == (item["outcome"] == 1.0)
    )
    report: dict[str, Any] = {
        "status": "ok",
        "db_path": str(db_path),
        "n": n,
        "market_brier": round(market_brier, 6),
        "independent_brier": round(independent_brier, 6),
        "brier_improvement": round(market_brier - independent_brier, 6),
        "market_hit_rate": round(market_hits / n, 4),
        "independent_hit_rate": round(independent_hits / n, 4),
        "prefer_independent": independent_brier < market_brier,
        "gamelog_rows": sum(1 for item in paired if item["feature_hash"] in GAMELOG_HASHES),
        "gamelog_v2_rows": sum(1 for item in paired if item["feature_hash"] == GAMELOG_HASH),
    }
    if include_tempered:
        soft_brier = sum(item["soft_brier"] for item in paired) / n
        tempered_brier = sum(item["tempered_brier"] for item in paired) / n
        report.update(
            {
                "soft_brier": round(soft_brier, 6),
                "tempered_brier": round(tempered_brier, 6),
                "temper_independent_weight": temper_independent_weight,
                "soft_reliability": soft_reliability,
                "prefer_soft_independent": soft_brier < market_brier,
                "prefer_tempered_independent": tempered_brier < market_brier,
            }
        )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate SO independent probs vs market")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--require-gamelog-hash", action="store_true")
    parser.add_argument(
        "--include-tempered",
        action="store_true",
        help="Also report soft/tempered sizing Brier (v1 replay proxy for v2).",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--promotion-gate",
        action="store_true",
        help="Print so_promotion readiness using config/so_promotion.json.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    include_tempered = bool(args.include_tempered or args.promotion_gate)
    report = evaluate_so_probs(
        args.db,
        require_gamelog_hash=args.require_gamelog_hash or args.promotion_gate,
        require_v2_hash=bool(args.promotion_gate),
        include_tempered=include_tempered,
    )
    if args.promotion_gate:
        from outlier_scrapers.so_promotion import promotion_readiness

        report = {
            "eval": report,
            "promotion": promotion_readiness(report),
        }
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    ok_statuses = {"ok", "insufficient_settled_so", "missing_db"}
    status = report.get("status")
    if status is None and isinstance(report.get("eval"), dict):
        status = report["eval"].get("status")
    return 0 if status in ok_statuses else 1


if __name__ == "__main__":
    raise SystemExit(main())
