"""Read-only calibration reports and ledger exports."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from outlier_scrapers import totals_model
from outlier_scrapers.calibration import (
    _positive_unit_recommendation_rows,
    learned_multiplier_promotion_signal,
)
from outlier_scrapers.feedback_db import (
    DEFAULT_DB_PATH,
    DECISION_FIELDS,
    MARKET_SNAPSHOT_FIELDS,
    PACK_MEMBERSHIP_FIELDS,
    PROBABILITY_COLUMNS,
    SETTLEMENT_FIELDS,
    _connect,
    _float,
    _joined_rows,
    _mean,
    _normal_result,
    _probability,
    _text,
    _utc_now,
)
from outlier_scrapers.feedback_settlement import _is_play
from outlier_scrapers.portfolio import PortfolioPolicy, allocate_portfolio_risk
from outlier_scrapers.utils import _write_csv

DEFAULT_REPORT_DIR = Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\reports\latest")

def _flat_pnl(
    row: dict[str, Any], result_field: str = "win_loss_push", *, invert: bool = False
) -> float | None:
    result = _text(row.get(result_field)).upper()
    if invert:
        if result == "W":
            result = "L"
        elif result == "L":
            result = "W"
    if result == "L":
        return -1.0
    if result == "PUSH":
        return 0.0
    if result != "W":
        return None
    decimal_price = _float(row.get("decimal_price"), field="decimal_price")
    return decimal_price - 1.0 if decimal_price is not None else None


def _group_metrics(rows: list[dict[str, Any]], label: str, value: str) -> dict[str, Any]:
    wins = sum(_text(row.get("win_loss_push")).upper() == "W" for row in rows)
    losses = sum(_text(row.get("win_loss_push")).upper() == "L" for row in rows)
    pushes = sum(_text(row.get("win_loss_push")).upper() == "PUSH" for row in rows)
    plays = [
        row
        for row in rows
        if _is_play(row.get("final_verdict"), row.get("pipeline_verdict"), row.get("units"))
    ]
    stand_downs = len(rows) - len(plays)
    units = sum(_float(row.get("units"), field="units") or 0.0 for row in plays)
    profit = sum(_float(row.get("pnl"), field="pnl") or 0.0 for row in plays)
    flat_values = [_flat_pnl(row, "would_have_result") for row in rows]
    flat_pnl = sum(value for value in flat_values if value is not None)
    return {
        label: value or "UNKNOWN",
        "n": len(rows),
        "plays": len(plays),
        "stand_downs": stand_downs,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": wins / (wins + losses) if wins + losses else None,
        "profit": profit,
        "units": units,
        "roi": profit / units if units else None,
        "avg_clv_line": _mean(_float(row.get("clv_line"), field="clv_line") for row in rows),
        "avg_clv_price": _mean(_float(row.get("clv_price"), field="clv_price") for row in rows),
        "would_have_flat_pnl": flat_pnl,
    }


GROUP_METRIC_FIELDS = [
    "n",
    "plays",
    "stand_downs",
    "wins",
    "losses",
    "pushes",
    "hit_rate",
    "profit",
    "units",
    "roi",
    "avg_clv_line",
    "avg_clv_price",
    "would_have_flat_pnl",
]


def _grouped(rows: list[dict[str, Any]], field: str, label: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_text(row.get(field)) or "UNKNOWN"].append(row)
    return [_group_metrics(groups[key], label, key) for key in sorted(groups)]


def _scoring_probability(row: dict[str, Any], column: str) -> float | None:
    """Return P(win | not push), the binary probability used for W/L scoring.

    A blank push probability means the row's binary basis is unknown, so it is
    excluded rather than silently assuming no push. No-push markets explicitly
    carry 0.0.
    """

    probability = _probability(row.get(column), field=column)
    push_prob = _probability(row.get("push_prob"), field="push_prob")
    if probability is None or push_prob is None or push_prob >= 1.0:
        return None
    conditional = probability / (1.0 - push_prob)
    if not 0.0 <= conditional <= 1.0:
        return None
    return conditional


def _probability_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source, column in PROBABILITY_COLUMNS.items():
        pairs: list[tuple[float, float]] = []
        for row in rows:
            result = _text(row.get("win_loss_push")).upper()
            probability = _scoring_probability(row, column)
            if probability is None or result == "PUSH":
                continue
            pairs.append((probability, 1.0 if result == "W" else 0.0))
        brier = _mean((probability - actual) ** 2 for probability, actual in pairs)
        log_loss = _mean(
            -(
                actual * math.log(min(max(probability, 1e-15), 1 - 1e-15))
                + (1 - actual) * math.log(1 - min(max(probability, 1e-15), 1 - 1e-15))
            )
            for probability, actual in pairs
        )
        expected = _mean(probability for probability, _actual in pairs)
        actual = _mean(actual for _probability_value, actual in pairs)
        output.append(
            {
                "probability_source": source,
                "n": len(pairs),
                "brier_score": brier,
                "log_loss": log_loss,
                "expected_hit_rate": expected,
                "actual_hit_rate": actual,
                "calibration_gap": (actual - expected)
                if expected is not None and actual is not None
                else None,
            }
        )
    return output


def _calibration_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source, column in PROBABILITY_COLUMNS.items():
        bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for row in rows:
            result = _text(row.get("win_loss_push")).upper()
            probability = _scoring_probability(row, column)
            if probability is None or result == "PUSH":
                continue
            bucket = min(9, int(probability * 10))
            bins[bucket].append((probability, 1.0 if result == "W" else 0.0))
        for bucket in range(10):
            pairs = bins[bucket]
            output.append(
                {
                    "probability_source": source,
                    "bucket": f"{bucket * 10}-{(bucket + 1) * 10}%",
                    "n": len(pairs),
                    "mean_predicted_prob": _mean(probability for probability, _actual in pairs),
                    "actual_hit_rate": _mean(actual for _probability, actual in pairs),
                }
            )
    return output


def _edge_bucket(value: Any) -> str:
    edge = _float(value, field="edge")
    if edge is None:
        return "missing"
    if edge < 0:
        return "<0%"
    if edge < 0.02:
        return "0-2%"
    if edge < 0.05:
        return "2-5%"
    if edge < 0.10:
        return "5-10%"
    return "10%+"


def _odds_bucket(value: Any) -> str:
    price = _float(value, field="price")
    if price is None:
        return "missing"
    if price <= -200:
        return "<=-200"
    if price <= -121:
        return "-199 to -121"
    if price <= -101:
        return "-120 to -101"
    if price <= 100:
        return "-100 to +100"
    if price <= 149:
        return "+101 to +149"
    return "+150+"


def _bucketed(
    rows: list[dict[str, Any]], field: str, label: str, bucket_fn: Any
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[bucket_fn(row.get(field))].append(row)
    return [_group_metrics(groups[key], label, key) for key in sorted(groups)]


def _signal_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw = _text(row.get("signal_flags") or row.get("data_quality_flags"))
        flags = [flag.strip() for flag in raw.replace(",", ";").split(";") if flag.strip()]
        for flag in flags or ["NO_SIGNAL_FLAG"]:
            groups[flag].append(row)
    return [_group_metrics(groups[key], "signal_flag", key) for key in sorted(groups)]


def _play_vs_stand_down(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group = (
            "PLAY"
            if _is_play(row.get("final_verdict"), row.get("pipeline_verdict"), row.get("units"))
            else "STAND_DOWN"
        )
        groups[group].append(row)
    return [_group_metrics(groups[key], "decision_class", key) for key in sorted(groups)]


DECISION_COVERAGE_FIELDS = [
    "decision_class",
    "total_decisions",
    "settled_decisions",
    "unsettled_decisions",
    "settlement_rate",
    "missing_event_start",
    "total_units",
    "settled_units",
]


def _decision_coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Report publication-level decision coverage separately from settled ROI."""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = conn.execute(
        """
        SELECT m.pipeline_verdict, m.units, s.event_starts_at,
               CASE WHEN EXISTS (
                   SELECT 1 FROM settlements t WHERE t.snapshot_id = m.snapshot_id
               ) THEN 1 ELSE 0 END AS settled
        FROM pack_snapshot_memberships m
        JOIN market_snapshots s ON s.snapshot_id = m.snapshot_id
        """
    )
    for raw in rows:
        row = dict(raw)
        decision_class = (
            "PLAY"
            if _is_play("", row.get("pipeline_verdict"), row.get("units"))
            else "STAND_DOWN"
        )
        groups[decision_class].append(row)

    output: list[dict[str, Any]] = []
    for decision_class in sorted(groups):
        group = groups[decision_class]
        settled = [row for row in group if bool(row.get("settled"))]
        total_units = sum(_float(row.get("units"), field="units") or 0.0 for row in group)
        settled_units = sum(_float(row.get("units"), field="units") or 0.0 for row in settled)
        output.append(
            {
                "decision_class": decision_class,
                "total_decisions": len(group),
                "settled_decisions": len(settled),
                "unsettled_decisions": len(group) - len(settled),
                "settlement_rate": len(settled) / len(group) if group else None,
                "missing_event_start": sum(
                    not _text(row.get("event_starts_at")) for row in group
                ),
                "total_units": total_units,
                "settled_units": settled_units,
            }
        )
    return output


def _model_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for model, field in (
        ("A", "A_verdict"),
        ("B", "B_verdict"),
        ("C", "C_verdict"),
        ("D", "D_verdict"),
    ):
        for row in rows:
            verdict = _text(row.get(field)).upper()
            if verdict:
                groups[(model, verdict)].append(row)

    output: list[dict[str, Any]] = []
    positive = {"BET", "PLAY", "LEAN", "CONFIRMS"}
    negative = {"FADE", "CONTRADICTS"}
    for (model, verdict), group in sorted(groups.items()):
        wins = sum(_text(row.get("win_loss_push")).upper() == "W" for row in group)
        losses = sum(_text(row.get("win_loss_push")).upper() == "L" for row in group)
        pushes = sum(_text(row.get("win_loss_push")).upper() == "PUSH" for row in group)
        directional_n = wins + losses if verdict in positive | negative else 0
        successes = wins if verdict in positive else (losses if verdict in negative else 0)
        flat_values = [_flat_pnl(row, invert=(verdict in negative)) for row in group]
        output.append(
            {
                "model": model,
                "verdict": verdict,
                "n": len(group),
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "selected_side_hit_rate": wins / (wins + losses) if wins + losses else None,
                "recommendation_accuracy": successes / directional_n if directional_n else None,
                "would_have_flat_pnl": sum(value for value in flat_values if value is not None),
            }
        )
    return output


ULTIMATE_ALT_SHADOW_FIELDS = [
    "alt_type",
    "n",
    "wins",
    "losses",
    "pushes",
    "hit_rate",
    "mean_implied_prob",
    "mean_conservative_prob",
    "calibration_gap",
    "flat_profit",
    "flat_roi",
    "clv_coverage",
    "mean_price_clv",
]


def ultimate_alt_shadow_release(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate, but never auto-promote, the unified alternate shadow lane."""
    shadow_rows = [
        row
        for row in rows
        if str(row.get("board") or "").upper() == "ALT_SHADOW_QUALIFIED"
        and "ultimate_alt:" in str(row.get("signal_flags") or "")
        and _normal_result(row.get("win_loss_push") or "") in {"W", "L", "PUSH"}
    ]

    def alt_type(row: dict[str, Any]) -> str:
        for flag in str(row.get("signal_flags") or "").split(";"):
            if flag.startswith("ultimate_alt:"):
                return flag.split(":", 1)[1] or "UNKNOWN"
        return "UNKNOWN"

    def metrics(label: str, group: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(_normal_result(row.get("win_loss_push") or "") == "W" for row in group)
        losses = sum(_normal_result(row.get("win_loss_push") or "") == "L" for row in group)
        pushes = len(group) - wins - losses
        graded = wins + losses
        implied = [_float(row.get("implied_prob")) for row in group]
        implied = [value for value in implied if value is not None]
        conservative = [_float(row.get("final_blended_prob")) for row in group]
        conservative = [value for value in conservative if value is not None]
        profit = 0.0
        for row in group:
            result = _normal_result(row.get("win_loss_push") or "")
            decimal = _float(row.get("decimal_price"))
            if result == "W" and decimal is not None:
                profit += decimal - 1.0
            elif result == "L":
                profit -= 1.0
        clv = [_float(row.get("clv_price")) for row in group]
        clv = [value for value in clv if value is not None]
        hit_rate = wins / graded if graded else None
        mean_conservative = _mean(conservative)
        return {
            "alt_type": label,
            "n": len(group),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "hit_rate": hit_rate,
            "mean_implied_prob": _mean(implied),
            "mean_conservative_prob": mean_conservative,
            "calibration_gap": (
                hit_rate - mean_conservative
                if hit_rate is not None and mean_conservative is not None
                else None
            ),
            "flat_profit": profit,
            "flat_roi": profit / graded if graded else None,
            "clv_coverage": len(clv) / len(group) if group else 0.0,
            "mean_price_clv": _mean(clv),
        }

    types = sorted({alt_type(row) for row in shadow_rows})
    by_type = [
        metrics(token, [row for row in shadow_rows if alt_type(row) == token]) for token in types
    ]
    overall = metrics("ALL", shadow_rows)
    shadow_days = len({str(row.get("captured_at") or "")[:10] for row in shadow_rows})
    type_counts = {row["alt_type"]: row["n"] for row in by_type}
    gates = {
        "minimum_200_settled": overall["n"] >= 200,
        "minimum_30_shadow_days": shadow_days >= 30,
        "minimum_40_each_type": all(
            type_counts.get(token, 0) >= 40 for token in ("SPREAD", "TOTAL", "PLAYER_PROP")
        ),
        "positive_flat_roi": (overall["flat_roi"] or 0.0) > 0.0,
        "calibration_gap_within_5pct": (
            overall["calibration_gap"] is not None and abs(overall["calibration_gap"]) <= 0.05
        ),
        "minimum_80pct_clv_coverage": overall["clv_coverage"] >= 0.80,
        "nonnegative_mean_price_clv": (
            overall["mean_price_clv"] is not None and overall["mean_price_clv"] >= 0.0
        ),
    }
    return {
        "mode": "shadow",
        "auto_promotion": False,
        "ready_for_manual_promotion_review": all(gates.values()),
        "shadow_days": shadow_days,
        "gates": gates,
        "overall": overall,
        "by_type": by_type,
    }

def export_ledgers(db_path: Path, output_dir: Path) -> dict[str, int]:
    output_dir = Path(output_dir)
    with _connect(Path(db_path)) as conn:
        snapshot_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT snapshot_id, captured_at, sport, event_id, market_id,
                       outcome_id, player_id, selection, line, price, book,
                       market_consensus_prob, independent_model_prob,
                       final_blended_prob, blend_market_weight, blend_model_weight,
                       blend_weight_source, blend_model_version, blend_segment,
                       push_prob, edge, data_quality_flags, data_quality_tier,
                       event_starts_at, hours_before_game, odds_range,
                       time_before_game, market_type, model_prob_source,
                       decimal_price, implied_prob,
                       board, selected, signal_flags, hit_rate_component,
                       insight_component, movement_component, orf_component, pack_path
                FROM market_snapshots
                ORDER BY captured_at, snapshot_id
                """
            )
        ]
        decision_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT decision_id, snapshot_id, pipeline_verdict,
                       A_verdict, B_verdict, C_verdict, D_verdict, final_verdict,
                       units, kill_reason, news_override
                FROM decisions
                ORDER BY decision_id
                """
            )
        ]
        settlement_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT settlement_id, decision_id, snapshot_id, outcome_id,
                       event_id, market_id, actual_result, win_loss_push,
                       closing_line, closing_price, clv_line, clv_price, pnl,
                       would_have_result
                FROM settlements
                ORDER BY settled_at, settlement_id
                """
            )
        ]
        membership_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT pack_capture_id, snapshot_id, pack_path, pack_timestamp,
                       source, pipeline_verdict, units, selected, actionable, created_at
                FROM pack_snapshot_memberships
                ORDER BY pack_timestamp, pack_capture_id, snapshot_id
                """
            )
        ]
    _write_csv(output_dir / "market_snapshots.csv", MARKET_SNAPSHOT_FIELDS, snapshot_rows)
    _write_csv(output_dir / "decisions.csv", DECISION_FIELDS, decision_rows)
    _write_csv(output_dir / "settlements.csv", SETTLEMENT_FIELDS, settlement_rows)
    _write_csv(
        output_dir / "pack_snapshot_memberships.csv",
        PACK_MEMBERSHIP_FIELDS,
        membership_rows,
    )
    return {
        "market_snapshots": len(snapshot_rows),
        "decisions": len(decision_rows),
        "settlements": len(settlement_rows),
        "pack_snapshot_memberships": len(membership_rows),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _report_markdown(
    coverage: dict[str, int],
    decision_coverage: list[dict[str, Any]],
    probability_metrics: list[dict[str, Any]],
    market_type: list[dict[str, Any]],
    learned_multiplier_signal: dict[str, Any],
) -> str:
    play_coverage = next(
        (row for row in decision_coverage if row["decision_class"] == "PLAY"), {}
    )
    lines = [
        "# Feedback-loop calibration report",
        "",
        f"Generated: {_utc_now()}",
        "",
        "## Coverage",
        "",
        f"- Market snapshots: {coverage['market_snapshots']}",
        f"- Decisions: {coverage['decisions']}",
        f"- Pack captures: {coverage['pack_captures']}",
        f"- Pack decision memberships: {coverage['pack_decision_memberships']}",
        f"- Settlements: {coverage['settlements']}",
        f"- Graded and linked decisions: {coverage['graded_and_linked']}",
        f"- Unlinked settlements: {coverage['unlinked_settlements']}",
        f"- Independent-model probabilities: {coverage['independent_probabilities']}",
        f"- Published PLAY decisions: {play_coverage.get('total_decisions', 0)}",
        f"- Settled PLAY decisions: {play_coverage.get('settled_decisions', 0)}",
        f"- Unsettled PLAY decisions: {play_coverage.get('unsettled_decisions', 0)}",
        f"- PLAY decisions missing event start: {play_coverage.get('missing_event_start', 0)}",
        "",
        "## Probability quality",
        "",
        "| Probability | N | Brier | Log loss | Expected hit | Actual hit | Gap |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in probability_metrics:
        lines.append(
            "| {probability_source} | {n} | {brier_score} | {log_loss} | "
            "{expected_hit_rate} | {actual_hit_rate} | {calibration_gap} |".format(
                **{key: _fmt(value) for key, value in row.items()}
            )
        )
    lines += [
        "",
        "## ROI, profit, and CLV by market type",
        "",
        "| Market type | N | Plays | Profit | Units | ROI | Avg line CLV | Avg price CLV |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in market_type:
        lines.append(
            f"| {row['market_type']} | {row['n']} | {row['plays']} | {_fmt(row['profit'])} | "
            f"{_fmt(row['units'])} | {_fmt(row['roi'])} | {_fmt(row['avg_clv_line'])} | "
            f"{_fmt(row['avg_clv_price'])} |"
        )
    promotion_population = learned_multiplier_signal.get("population") or {}
    failed_gates = learned_multiplier_signal.get("failed_gates") or []
    lines += [
        "",
        "## Learned-multiplier promotion signal",
        "",
        f"- Status: {learned_multiplier_signal.get('status', 'NOT_READY')}",
        "- Auto-promotion: disabled (manual policy change is always required)",
        f"- Exact positive-unit recommendations: "
        f"{promotion_population.get('total_recommendations', 0)}",
        f"- Settled positive-unit recommendations: "
        f"{promotion_population.get('settled_recommendations', 0)}",
        f"- Observation days: {promotion_population.get('observation_days', 0)}",
        f"- Failed gates: {', '.join(failed_gates) if failed_gates else 'none'}",
        "",
        "## Interpretation guardrail",
        "",
        "ROI, profit, hit-rate, and market tables include settled decisions only. "
        "Use decision_coverage.csv to verify how much of the published card is still unsettled or "
        "missing an event start before interpreting performance.",
        "",
        "Market-consensus and final-blended metrics will be identical while the independent-model "
        "column is empty. This is intentional: the report exposes the current market-derived "
        "baseline instead of relabeling it as an independent model.",
        "",
    ]
    return "\n".join(lines)


def _missing_edge_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Explain absent edges without manufacturing probability inputs."""
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    missing = 0
    for row in rows:
        edge = row.get("edge")
        if edge is not None and str(edge).strip() != "":
            continue
        missing += 1
        market_type = _text(row.get("market_type"))
        model_source = _text(row.get("model_prob_source"))
        if market_type.upper() == "GAMELINE" and not model_source:
            reason = "gameline_missing_model_prob_source"
        elif not model_source:
            reason = "missing_model_prob_source"
        elif row.get("market_consensus_prob") in (None, ""):
            reason = "missing_market_consensus_prob"
        elif row.get("decimal_price") in (None, ""):
            reason = "missing_decimal_price"
        else:
            reason = "missing_edge_unclassified"
        counts[(_text(row.get("sport")), market_type, reason)] += 1
    return {
        "settled_rows": len(rows),
        "missing_edge_rows": missing,
        "missing_edge_rate": (missing / len(rows)) if rows else 0.0,
        "breakdown": [
            {"sport": sport, "market_type": market_type, "reason": reason, "n": count}
            for (sport, market_type, reason), count in sorted(counts.items())
        ],
    }


TOTALS_MODEL_PROB_SOURCES = frozenset(
    {
        # totals_model.py's backfill path (rows Outlier itself never priced).
        totals_model.SOURCE_DEVIG,
        totals_model.SOURCE_BLEND,
        # The specialized totals board's own devig (game_totals.py -> pack.py
        # normalize_original): the canonical, actionable totals rows. Most
        # settled totals rows carry these, not the backfill-path sources
        # above — see game_totals.py:818-819 and pack.py:629.
        "book_median",
        "single_book",
        "totals_model",
    }
)


def _is_totals_row(row: dict[str, Any]) -> bool:
    """True for a settled row priced by the totals ladder (game/team totals)."""
    return _text(row.get("model_prob_source")) in TOTALS_MODEL_PROB_SOURCES


def _wilson_interval(wins: int, n: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a binomial hit rate; None when n == 0."""
    if n <= 0:
        return None
    phat = wins / n
    denominator = 1.0 + z * z / n
    center = phat + z * z / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n)
    return ((center - margin) / denominator, (center + margin) / denominator)


def totals_paired_oos_loss(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired Brier-loss comparison of the totals L10 signal against the market.

    Totals never derive ``independent_model_prob`` (see ``totals_model.py`` —
    it's explicitly blanked), so the generic market/independent comparison in
    ``_probability_metrics`` never sees totals rows. This is the totals-only
    equivalent: for each settled totals row with both a market consensus
    probability and a captured L10 hit rate, compute
    ``d_t = brier(l10) - brier(market)``. A negative mean d_t with a 95% CI
    entirely below zero means the L10 signal beat the market out-of-sample on
    this settled population; ``model_better``/``market_better`` are False
    whenever the CI straddles zero (i.e. no verdict yet).
    """
    diffs: list[float] = []
    for row in rows:
        if not _is_totals_row(row):
            continue
        result = _text(row.get("win_loss_push")).upper()
        if result not in {"W", "L"}:
            continue
        market_p = _scoring_probability(row, "market_consensus_prob")
        l10_p = _scoring_probability(row, "recency_hit_prob")
        if market_p is None or l10_p is None:
            continue
        actual = 1.0 if result == "W" else 0.0
        diffs.append((l10_p - actual) ** 2 - (market_p - actual) ** 2)

    n = len(diffs)
    mean_diff = _mean(diffs)
    ci95: tuple[float, float] | None = None
    if n >= 2 and mean_diff is not None:
        variance = sum((value - mean_diff) ** 2 for value in diffs) / (n - 1)
        margin = 1.96 * math.sqrt(variance / n) if variance > 0 else 0.0
        ci95 = (round(mean_diff - margin, 10), round(mean_diff + margin, 10))
    return {
        "n": n,
        "mean_paired_loss_diff": round(mean_diff, 10) if mean_diff is not None else None,
        "ci95": ci95,
        "model_better": ci95 is not None and ci95[1] < 0.0,
        "market_better": ci95 is not None and ci95[0] > 0.0,
    }


def _totals_edge_band(edge: Any) -> tuple[str, str] | None:
    """(side, band) for a signed totals edge, in percentage-point bands.

    ``side`` is ``above_market`` for edge >= 0 (the model favors the priced
    side more than the market) and ``below_market`` otherwise. Bands are the
    0-1/1-2/2-3/3-4/4+ percentage-point buckets from the totals audit.
    """
    value = _float(edge, field="edge")
    if value is None:
        return None
    magnitude_pp = abs(value) * 100.0
    if magnitude_pp < 1.0:
        band = "0-1pp"
    elif magnitude_pp < 2.0:
        band = "1-2pp"
    elif magnitude_pp < 3.0:
        band = "2-3pp"
    elif magnitude_pp < 4.0:
        band = "3-4pp"
    else:
        band = "4pp+"
    return ("above_market" if value >= 0.0 else "below_market", band)


TOTALS_EDGE_BAND_ORDER = ["0-1pp", "1-2pp", "2-3pp", "3-4pp", "4pp+"]


def totals_edge_bucket_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Bucket settled totals rows by signed edge and test hit-rate monotonicity.

    The current ``MIN_EDGE_TOTALS`` gate (game_totals.py) assumes a larger
    priced edge means a better bet. This buckets settled totals rows by
    signed edge band and reports the realized hit rate (with a Wilson 95% CI)
    per band, so that assumption can be checked against outcomes instead of
    asserted.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not _is_totals_row(row):
            continue
        result = _text(row.get("win_loss_push")).upper()
        if result not in {"W", "L"}:
            continue
        key = _totals_edge_band(row.get("edge"))
        if key is None:
            continue
        groups[key].append(row)

    buckets: list[dict[str, Any]] = []
    for side in ("above_market", "below_market"):
        for band in TOTALS_EDGE_BAND_ORDER:
            group = groups.get((side, band), [])
            wins = sum(1 for row in group if _text(row.get("win_loss_push")).upper() == "W")
            n = len(group)
            ci = _wilson_interval(wins, n)
            buckets.append(
                {
                    "side": side,
                    "band": band,
                    "n": n,
                    "wins": wins,
                    "hit_rate": wins / n if n else None,
                    "hit_rate_ci95": ci,
                    "mean_edge": _mean(_float(row.get("edge"), field="edge") for row in group),
                }
            )

    def _is_monotonic_nondecreasing(side: str, *, min_n: int = 20) -> bool | None:
        rates = [
            bucket["hit_rate"]
            for bucket in buckets
            if bucket["side"] == side and bucket["n"] >= min_n
        ]
        if len(rates) < 2:
            return None
        return all(later >= earlier for earlier, later in zip(rates, rates[1:]))

    return {
        "buckets": buckets,
        "monotonic_above_market": _is_monotonic_nondecreasing("above_market"),
        "monotonic_below_market": _is_monotonic_nondecreasing("below_market"),
    }


def generate_report(db_path: Path = DEFAULT_DB_PATH, output_dir: Path = DEFAULT_REPORT_DIR) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with _connect(Path(db_path)) as conn:
        rows = _joined_rows(conn)
        decision_coverage = _decision_coverage(conn)
        positive_unit_rows = _positive_unit_recommendation_rows(conn)
        coverage = {
            "market_snapshots": conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0],
            "decisions": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
            "pack_captures": conn.execute(
                "SELECT COUNT(DISTINCT pack_capture_id) FROM pack_snapshot_memberships"
            ).fetchone()[0],
            "pack_decision_memberships": conn.execute(
                "SELECT COUNT(*) FROM pack_snapshot_memberships"
            ).fetchone()[0],
            "settlements": conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0],
            "graded_and_linked": len(rows),
            "unlinked_settlements": conn.execute(
                "SELECT COUNT(*) FROM settlements WHERE decision_id IS NULL"
            ).fetchone()[0],
            "independent_probabilities": conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE independent_model_prob IS NOT NULL"
            ).fetchone()[0],
        }

    probability_metrics = _probability_metrics(rows)
    calibration = _calibration_curve(rows)
    expected_actual = [
        {
            key: row[key]
            for key in (
                "probability_source",
                "n",
                "expected_hit_rate",
                "actual_hit_rate",
                "calibration_gap",
            )
        }
        for row in probability_metrics
    ]
    market_type = _grouped(rows, "market_type", "market_type")
    edge_buckets = _bucketed(rows, "edge", "edge_bucket", _edge_bucket)
    odds_ranges = _bucketed(rows, "price", "odds_range", _odds_bucket)
    books = _grouped(rows, "book", "book")
    leagues = _grouped(rows, "sport", "league")
    signal_flags = _signal_results(rows)
    play_vs_stand_down = _play_vs_stand_down(rows)
    model_performance = _model_performance(rows)
    missing_edge_diagnostics = _missing_edge_diagnostics(rows)
    ultimate_alt_release = ultimate_alt_shadow_release(rows)
    learned_multiplier_signal = learned_multiplier_promotion_signal(positive_unit_rows)
    totals_paired_loss = totals_paired_oos_loss(rows)
    totals_edge_buckets = totals_edge_bucket_report(rows)

    metric_fields = [
        "probability_source",
        "n",
        "brier_score",
        "log_loss",
        "expected_hit_rate",
        "actual_hit_rate",
        "calibration_gap",
    ]
    _write_csv(output_dir / "probability_metrics.csv", metric_fields, probability_metrics)
    _write_csv(
        output_dir / "calibration_curves.csv",
        ["probability_source", "bucket", "n", "mean_predicted_prob", "actual_hit_rate"],
        calibration,
    )
    _write_csv(
        output_dir / "expected_vs_actual.csv",
        ["probability_source", "n", "expected_hit_rate", "actual_hit_rate", "calibration_gap"],
        expected_actual,
    )
    _write_csv(
        output_dir / "decision_coverage.csv",
        DECISION_COVERAGE_FIELDS,
        decision_coverage,
    )
    for filename, label, table in (
        ("market_type.csv", "market_type", market_type),
        ("edge_buckets.csv", "edge_bucket", edge_buckets),
        ("odds_ranges.csv", "odds_range", odds_ranges),
        ("books.csv", "book", books),
        ("leagues.csv", "league", leagues),
        ("signal_flags.csv", "signal_flag", signal_flags),
        ("play_vs_stand_down.csv", "decision_class", play_vs_stand_down),
    ):
        _write_csv(output_dir / filename, [label, *GROUP_METRIC_FIELDS], table)
    _write_csv(
        output_dir / "model_performance.csv",
        [
            "model",
            "verdict",
            "n",
            "wins",
            "losses",
            "pushes",
            "selected_side_hit_rate",
            "recommendation_accuracy",
            "would_have_flat_pnl",
        ],
        model_performance,
    )
    _write_csv(
        output_dir / "missing_edge_diagnostics.csv",
        ["sport", "market_type", "reason", "n"],
        missing_edge_diagnostics["breakdown"],
    )
    _write_csv(
        output_dir / "ultimate_alt_shadow.csv",
        ULTIMATE_ALT_SHADOW_FIELDS,
        [ultimate_alt_release["overall"], *ultimate_alt_release["by_type"]],
    )
    _write_csv(
        output_dir / "totals_edge_buckets.csv",
        ["side", "band", "n", "wins", "hit_rate", "hit_rate_ci95", "mean_edge"],
        totals_edge_buckets["buckets"],
    )
    (output_dir / "learned_multiplier_promotion.json").write_text(
        json.dumps(learned_multiplier_signal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "totals_oos_scoring.json").write_text(
        json.dumps(
            {
                "paired_loss": totals_paired_loss,
                "edge_buckets": totals_edge_buckets,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "coverage": coverage,
                "decision_coverage": decision_coverage,
                "probability_metrics": probability_metrics,
                "learned_multiplier_promotion": learned_multiplier_signal,
                "ultimate_alt_shadow_release": ultimate_alt_release,
                "missing_edge_diagnostics": missing_edge_diagnostics,
                "totals_oos_paired_loss": totals_paired_loss,
                "totals_oos_edge_monotonicity": {
                    "monotonic_above_market": totals_edge_buckets["monotonic_above_market"],
                    "monotonic_below_market": totals_edge_buckets["monotonic_below_market"],
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        _report_markdown(
            coverage,
            decision_coverage,
            probability_metrics,
            market_type,
            learned_multiplier_signal,
        ),
        encoding="utf-8",
    )
    export_ledgers(Path(db_path), output_dir / "ledgers")
    return output_dir


def replay_portfolio(
    db_conn_or_path: Path | str | sqlite3.Connection,
    start_date: str,
    end_date: str,
    policy_path: Path | str,
    as_of_strict: bool = True,
) -> dict[str, Any]:
    with open(policy_path, "r", encoding="utf-8") as f:
        policy_data = json.load(f)

    policy = PortfolioPolicy(
        stake_increment=policy_data.get("stake_increment", 0.1),
        max_wager_units=policy_data.get("max_wager_units", 1.0),
        max_daily_units=policy_data.get("max_daily_units", 10.0),
        max_event_units=policy_data.get("max_event_units", 3.0),
        max_player_units=policy_data.get("max_player_units", 1.0),
        max_team_units=policy_data.get("max_team_units", 2.0),
        max_market_type_units=policy_data.get("max_market_type_units", 5.0),
        max_correlated_cluster_units=policy_data.get("max_correlated_cluster_units", 2.0),
        max_book_units=policy_data.get("max_book_units", 5.0),
    )

    if isinstance(db_conn_or_path, (str, Path)):
        conn = sqlite3.connect(db_conn_or_path)
        conn.row_factory = sqlite3.Row
        close_conn = True
    else:
        conn = db_conn_or_path
        close_conn = False

    try:
        query = """
            SELECT s.*, d.units as legacy_units, d.decision_id
            FROM market_snapshots s
            JOIN decisions d ON s.snapshot_id = d.snapshot_id
            WHERE SUBSTR(s.captured_at, 1, 10) >= ? AND SUBSTR(s.captured_at, 1, 10) <= ?
            ORDER BY s.captured_at
        """
        rows = conn.execute(query, (start_date, end_date)).fetchall()

        # Group by date
        grouped_by_date = defaultdict(list)
        for row in rows:
            date_str = _text(row["captured_at"])[:10]
            grouped_by_date[date_str].append(dict(row))

        summary = {}
        for date_str, daily_rows in sorted(grouped_by_date.items()):
            # Map for allocate_portfolio_risk
            allocation_rows = []
            for r in daily_rows:
                allocation_rows.append(
                    {
                        "stable_wager_id": r["snapshot_id"],
                        "actionable": str(r.get("board") == "A").lower(),
                        "board": r.get("board"),
                        "units": r.get("legacy_units", 0.0)
                        if as_of_strict
                        else policy.max_wager_units,
                        "event_id": r.get("event_id"),
                        "player_id": r.get("player_id"),
                        "team": None,
                        "market_type": r.get("market_type"),
                        "cluster_id": None,
                        "sportsbook": r.get("book"),
                        "edge_pct": r.get("edge", 0.0),
                    }
                )

            result = allocate_portfolio_risk(allocation_rows, policy)

            legacy_total = sum(r.get("legacy_units") or 0.0 for r in daily_rows)
            replayed_total = sum(result.allocated_units.values())

            summary[date_str] = {
                "legacy_total_units": round(legacy_total, 4),
                "replayed_total_units": round(replayed_total, 4),
                "binding_constraints": result.binding_constraints,
                "utilization": result.utilization,
            }

            print(f"--- Replay Date: {date_str} ---")
            print(f"Legacy Units: {legacy_total:.4f} | Replayed Units: {replayed_total:.4f}")
            if result.binding_constraints:
                print(f"Binding constraints: {', '.join(result.binding_constraints)}")
            print()

        return summary
    finally:
        if close_conn:
            conn.close()
