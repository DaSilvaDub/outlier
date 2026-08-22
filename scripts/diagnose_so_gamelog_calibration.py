"""Diagnose settled gamelog SO independent probs vs market/outcomes."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

DB = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3")
GAMELOG = "so-starter-gamelog-v1"


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


def _is_so(selection: str, market_type: str) -> bool:
    market = (market_type or "").upper()
    text = (selection or "").upper()
    return market in {"SO", "STRIKEOUTS", "K", "PITCHER_STRIKEOUTS", "PLAYER_PROP"} and (
        "STRIKEOUT" in text or market in {"SO", "STRIKEOUTS", "K", "PITCHER_STRIKEOUTS"}
    )


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)")}
    hash_expr = (
        "s.projection_feature_hash"
        if "projection_feature_hash" in cols
        else "NULL AS projection_feature_hash"
    )
    flag_expr = (
        "s.projection_quality_flags"
        if "projection_quality_flags" in cols
        else "NULL AS projection_quality_flags"
    )
    rows = conn.execute(
        f"""
        SELECT
            s.selection, s.market_type, s.line, s.price, s.decimal_price,
            s.market_consensus_prob, s.independent_model_prob, s.implied_prob,
            s.edge, s.model_prob_source, s.signal_flags, s.board,
            {hash_expr}, {flag_expr},
            s.captured_at, s.event_starts_at, s.pack_path,
            x.win_loss_push, x.actual_result, x.closing_line, x.clv_line, x.pnl,
            d.pipeline_verdict, d.units, d.final_verdict
        FROM market_snapshots s
        JOIN settlements x ON x.snapshot_id = s.snapshot_id
        LEFT JOIN decisions d ON d.snapshot_id = s.snapshot_id
        WHERE x.win_loss_push IN ('W','L','PUSH')
        """
    ).fetchall()
    conn.close()

    cases = []
    for r in rows:
        if not _is_so(r["selection"], r["market_type"]):
            continue
        market = r["market_consensus_prob"]
        indep = r["independent_model_prob"]
        if market is None or indep is None:
            continue
        digest = str(r["projection_feature_hash"] or "")
        if digest != GAMELOG:
            continue
        result = str(r["win_loss_push"] or "").upper()
        if result == "PUSH":
            continue
        y = 1.0 if result == "W" else 0.0
        cases.append(
            {
                "selection": r["selection"],
                "line": r["line"],
                "price": r["price"],
                "decimal_price": r["decimal_price"],
                "market": float(market),
                "independent": float(indep),
                "implied": float(r["implied_prob"]) if r["implied_prob"] is not None else None,
                "edge": r["edge"],
                "result": result,
                "y": y,
                "actual_result": r["actual_result"],
                "closing_line": r["closing_line"],
                "market_brier": _brier(float(market), y),
                "indep_brier": _brier(float(indep), y),
                "market_correct": (float(market) >= 0.5) == (y == 1.0),
                "indep_correct": (float(indep) >= 0.5) == (y == 1.0),
                "indep_overconfident": abs(float(indep) - 0.5) > abs(float(market) - 0.5),
                "side": "OVER" if "OVER" in str(r["selection"]).upper() else "UNDER",
                "signal_flags": r["signal_flags"],
                "board": r["board"],
                "units": r["units"],
                "pipeline_verdict": r["pipeline_verdict"],
                "captured_at": r["captured_at"],
                "flags": r["projection_quality_flags"],
            }
        )

    print(f"gamelog_settled_n={len(cases)}")
    if not cases:
        return

    # Sort by independent brier worst first
    cases.sort(key=lambda c: c["indep_brier"], reverse=True)
    print("\n=== ROW DETAIL (worst independent Brier first) ===")
    for c in cases:
        delta = c["independent"] - c["market"]
        print(
            f"{c['result']:4} {c['selection']}\n"
            f"  market={c['market']:.3f} indep={c['independent']:.3f} delta={delta:+.3f} "
            f"implied={c['implied']} line={c['line']} close={c['closing_line']}\n"
            f"  brier m={c['market_brier']:.3f} i={c['indep_brier']:.3f} "
            f"m_ok={c['market_correct']} i_ok={c['indep_correct']} "
            f"indep_more_extreme={c['indep_overconfident']}\n"
            f"  signals={c['signal_flags']} board={c['board']} units={c['units']} "
            f"verdict={c['pipeline_verdict']} actual={c['actual_result']}"
        )

    n = len(cases)
    print("\n=== AGGREGATES ===")
    print(
        "market_brier",
        round(sum(c["market_brier"] for c in cases) / n, 4),
        "indep_brier",
        round(sum(c["indep_brier"] for c in cases) / n, 4),
    )
    print(
        "market_hit",
        sum(c["market_correct"] for c in cases),
        "/",
        n,
        "indep_hit",
        sum(c["indep_correct"] for c in cases),
        "/",
        n,
    )
    print(
        "indep_more_extreme_than_market",
        sum(c["indep_overconfident"] for c in cases),
        "/",
        n,
    )
    # Reliability-style buckets by independent probability
    buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    print("\n=== INDEPENDENT RELIABILITY (by predicted win prob) ===")
    for lo, hi in buckets:
        subset = [c for c in cases if lo <= c["independent"] < hi]
        if not subset:
            continue
        avg_p = sum(c["independent"] for c in subset) / len(subset)
        avg_y = sum(c["y"] for c in subset) / len(subset)
        print(
            f"[{lo:.1f},{hi:.1f}) n={len(subset)} mean_p={avg_p:.3f} "
            f"actual_rate={avg_y:.3f} gap={avg_p - avg_y:+.3f}"
        )

    # Side split
    print("\n=== BY SIDE ===")
    for side in ("OVER", "UNDER"):
        subset = [c for c in cases if c["side"] == side]
        if not subset:
            continue
        print(
            side,
            "n",
            len(subset),
            "indep_hit",
            sum(c["indep_correct"] for c in subset),
            "mean_indep",
            round(sum(c["independent"] for c in subset) / len(subset), 3),
            "actual",
            round(sum(c["y"] for c in subset) / len(subset), 3),
        )

    # Where independent disagreed with market on side (>=0.5)
    disagree = [
        c
        for c in cases
        if (c["independent"] >= 0.5) != (c["market"] >= 0.5)
    ]
    print("\n=== MARKET vs INDEP DIRECTION DISAGREE ===")
    print("n_disagree", len(disagree))
    for c in disagree:
        print(
            c["result"],
            c["selection"],
            f"m={c['market']:.3f}",
            f"i={c['independent']:.3f}",
            "winner=market" if c["market_correct"] and not c["indep_correct"] else "winner=indep/tie",
        )

    out = Path(__file__).resolve().parents[1] / "docs" / "plans" / "2026-08-22-so-gamelog-calibration-diagnosis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"n": n, "cases": cases}, indent=2), encoding="utf-8")
    print("\nWrote", out)


if __name__ == "__main__":
    main()
