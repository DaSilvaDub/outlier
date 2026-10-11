"""Shadow settlement for NFL Tier-1 / matchup-tagged prediction snapshots.

Loads pipeline artifacts (nfl_high_prob_props_*.json / nfl_matchup_props_*.json),
joins them to box-score finals by team pair + slate date + player name, and emits
an OOS scorecard. This is measurement only — no promotion gates.

Honest gaps (marked in the report, never invented):
- Model Brier/logloss only when ``model_p`` / ``p_model`` is present; otherwise
  book-implied metrics are labeled as market-only.
- CLV only when ``close_line`` / ``close_odds`` / ``close_implied`` present;
  ``best_odds`` alone is not treated as close.
- Live ESPN may be unavailable; prefer ``--provider nflverse`` or ``--boxscores``.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from outlier_nfl.boxscore import (
    BoxScoreError,
    NflBoxScoreEvent,
    grade_side,
    is_unsupported_settle_market,
    parse_simplified_events,
    player_actual,
    resolve_player_stats,
)
from outlier_nfl.utils import (
    nfl_season_for_date,
    safe_read_json,
    safe_write_json,
    to_eastern_date,
)

PREDICTION_SOURCES = (
    "tier1",
    "matchup",
    "calibrated",
    "auto",
)

BLOCKERS = {
    "clv": (
        "No close_line/close_odds/close_implied on prediction snapshots; "
        "best_odds alone is not treated as book close. Use enrich_close "
        "(--mode snapshot_best) or a real close feed."
    ),
    "model_prob": (
        "No model_p / p_model on snapshots; Brier/logloss below are book "
        "implied_probability only (labeled market)."
    ),
    "live_espn": (
        "Live ESPN NFL scoreboard is optional and may return 403 from sandboxed "
        "egress; prefer --provider nflverse or --boxscores fixtures."
    ),
    "event_id_join": (
        "Outlier event_id does not match ESPN/nflverse provider ids; join is "
        "team-pair + Eastern slate date + player name."
    ),
}


@dataclass(frozen=True)
class PredictionSnap:
    """Joinable prediction row extracted from a pipeline artifact."""

    source: str
    event_id: str
    event_starts_at: str | None
    slate_date: str | None
    matchup: str
    team: str | None
    opponent: str | None
    player_name: str
    player_id: str | None
    market: str
    position: str
    line: float
    implied_probability: float | None
    confidence_tier: str | None
    calibration_tags: tuple[str, ...]
    best_odds: int | None
    window: str | None = None
    close_line: float | None = None
    close_odds: int | None = None
    close_implied: float | None = None
    close_source: str | None = None
    close_status: str | None = None  # "verified" when the close passed the timing policy
    model_p: float | None = None
    scope: str | None = None  # None: the artifact predates scope (treated as full game)

    @property
    def team_codes(self) -> frozenset[str]:
        codes = {c for c in (_team_code(self.team), _team_code(self.opponent)) if c}
        if len(codes) < 2 and self.matchup:
            parts = [p.strip() for p in self.matchup.replace("vs", "@").split("@")]
            codes = {c for c in (_team_code(p) for p in parts) if c}
        return frozenset(codes)


@dataclass
class SettleRow:
    prediction: PredictionSnap
    status: str  # settled | skipped
    skip_reason: str | None = None
    provider_event_id: str | None = None
    actual: float | None = None
    result: str | None = None  # W | L | P
    prob: float | None = None  # 0-1 book-implied used for market scoring
    prob_source: str | None = None  # market | model | none
    brier: float | None = None
    logloss: float | None = None
    model_prob: float | None = None
    brier_model: float | None = None
    logloss_model: float | None = None
    clv_implied_pts: float | None = None
    close_line_move: float | None = None  # close_line - line, reported apart from CLV

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["prediction"] = asdict(self.prediction)
        payload["prediction"]["calibration_tags"] = list(self.prediction.calibration_tags)
        return payload


@dataclass
class SettleReport:
    n_predictions: int = 0
    n_settled: int = 0
    n_skipped: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_pushes: int = 0
    hit_rate: float | None = None
    brier: float | None = None
    logloss: float | None = None
    brier_label: str = "market_implied"
    logloss_label: str = "market_implied"
    n_scored_prob: int = 0
    brier_model: float | None = None
    logloss_model: float | None = None
    n_scored_model: int = 0
    clv: dict[str, Any] = field(default_factory=dict)
    blockers: dict[str, str] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    by_tier: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_model_p_bucket: dict[str, dict[str, Any]] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _team_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    compact = "".join(ch for ch in text if ch.isalnum())
    if len(compact) <= 4:
        return compact
    from outlier_nfl.config import normalize_team

    return normalize_team(text) or compact[:4]


def _slate_date_from_prediction(record: Mapping[str, Any], fallback_date: str | None) -> str | None:
    starts = record.get("event_starts_at")
    if starts:
        eastern = to_eastern_date(str(starts))
        if eastern:
            return eastern
        raw = str(starts)
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    return fallback_date


def _optional_float(value: Any) -> float | None:
    """A finite float, or None: NaN and +/-inf are not usable numbers (F30)."""
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    """An int, or None for missing, unparseable or non-finite values (F30)."""
    try:
        if value is None or value == "":
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def load_prediction_snapshot(
    path: Path | str,
    *,
    source: str = "auto",
    require_tier1_or_matchup: bool = True,
) -> list[PredictionSnap]:
    """Load prediction rows from a pipeline JSON artifact."""
    path = Path(path)
    payload = safe_read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Prediction snapshot must be an object: {path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"Prediction snapshot missing records list: {path}")
    artifact_date = payload.get("date")
    window = payload.get("window")
    inferred = source
    if source == "auto":
        name = path.name.lower()
        if "high_prob" in name:
            inferred = "tier1"
        elif "matchup_props" in name:
            inferred = "matchup"
        else:
            inferred = "calibrated"

    snaps: list[PredictionSnap] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        tier = raw.get("confidence_tier")
        tags = tuple(str(t) for t in (raw.get("calibration_tags") or ()))
        is_tier1 = tier == "TIER_1_ANCHOR"
        is_matchup = any(tag.startswith("MATCHUP_") for tag in tags)
        if require_tier1_or_matchup and inferred == "calibrated" and not (is_tier1 or is_matchup):
            continue
        if inferred == "tier1" and not is_tier1 and require_tier1_or_matchup:
            pass
        line = raw.get("line")
        if line is None:
            continue
        line_f = _optional_float(line)
        if line_f is None:  # unparseable or non-finite: a NaN line must not grade P (F30)
            continue
        player_name = str(raw.get("player_name") or "").strip()
        market = str(raw.get("market") or "").strip()
        position = str(raw.get("position") or "").strip().upper()
        if not player_name or not market or not position:
            continue
        ip = raw.get("implied_probability")
        ip_f = _optional_float(ip)
        model_raw = raw.get("model_p")
        if model_raw is None:
            model_raw = raw.get("p_model")
        snaps.append(
            PredictionSnap(
                source=inferred,
                event_id=str(raw.get("event_id") or ""),
                event_starts_at=str(raw["event_starts_at"]) if raw.get("event_starts_at") else None,
                slate_date=_slate_date_from_prediction(
                    raw, str(artifact_date) if artifact_date else None
                ),
                matchup=str(raw.get("matchup") or ""),
                team=str(raw["team"]) if raw.get("team") else None,
                opponent=str(raw["opponent"]) if raw.get("opponent") else None,
                player_name=player_name,
                player_id=str(raw["player_id"]) if raw.get("player_id") else None,
                market=market,
                position=position,
                line=line_f,
                implied_probability=ip_f,
                confidence_tier=str(tier) if tier else None,
                calibration_tags=tags,
                best_odds=_optional_int(raw.get("best_odds")),
                window=str(window) if window else None,
                close_line=_optional_float(raw.get("close_line")),
                close_odds=_optional_int(raw.get("close_odds")),
                close_implied=_optional_float(raw.get("close_implied")),
                close_source=str(raw["close_source"]) if raw.get("close_source") else None,
                close_status=str(raw["close_status"]) if raw.get("close_status") else None,
                model_p=_optional_float(model_raw),
                scope=str(raw["scope"]) if raw.get("scope") else None,
            )
        )
    return snaps


def load_boxscores(path: Path | str) -> list[NflBoxScoreEvent]:
    """Load simplified box-score events from disk."""
    payload = safe_read_json(path)
    if not isinstance(payload, (dict, list)):
        raise BoxScoreError(f"Box-score file must be object or list: {path}")
    return parse_simplified_events(payload)


def _match_event(
    snap: PredictionSnap, events: Sequence[NflBoxScoreEvent]
) -> tuple[NflBoxScoreEvent | None, str | None]:
    codes = snap.team_codes
    if len(codes) < 2:
        return None, "missing_team_pair"
    candidates = [e for e in events if e.team_codes == codes]
    if snap.slate_date:
        dated = [e for e in candidates if e.event_date.isoformat() == snap.slate_date]
        if dated:
            candidates = dated
        elif candidates:
            return None, "date_mismatch"
    if not candidates:
        return None, "event_not_found"
    if len(candidates) > 1:
        return None, "ambiguous_event_match"
    return candidates[0], None


def _prob_01(raw: float | None) -> float | None:
    """Normalize a probability to [0, 1].

    Accepts fractions or 0–100 percentages. Extremes 0 and 1 are valid for
    Brier; ``_logloss`` clips internally. Rejects values outside [0, 100].
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= value <= 1.0:
        return value
    if 1.0 < value <= 100.0:
        return value / 100.0
    return None


def _brier(prob: float, won: bool) -> float:
    y = 1.0 if won else 0.0
    return (prob - y) ** 2


def _logloss(prob: float, won: bool) -> float:
    y = 1.0 if won else 0.0
    p = min(max(prob, 1e-15), 1.0 - 1e-15)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def settle_predictions(
    predictions: Sequence[PredictionSnap],
    events: Sequence[NflBoxScoreEvent],
) -> SettleReport:
    """Join predictions to box scores and compute shadow OOS metrics."""
    has_close = any(
        p.close_implied is not None or p.close_odds is not None or p.close_line is not None
        for p in predictions
    )
    has_model = any(p.model_p is not None for p in predictions)
    blockers = dict(BLOCKERS)
    if has_close:
        blockers.pop("clv", None)
    if has_model:
        blockers.pop("model_prob", None)

    report = SettleReport(
        n_predictions=len(predictions),
        blockers=blockers,
        clv={
            "status": "ok" if has_close else "blocked",
            "reason": None
            if has_close
            else BLOCKERS["clv"],
            "n": 0,
            "mean_clv_implied_pts": None,
            "n_excluded_line_mismatch": 0,
            "n_excluded_close_line_unknown": 0,
            "n_line_moved": 0,
            "mean_line_move": None,
            # book_close rows carrying no timing at all: trusted at their label.
            "n_untimed_supplied_close": sum(
                1
                for p in predictions
                if p.close_source == "book_close" and p.close_status is None
            ),
            "note": (
                "CLV implied pts = close_implied - bet implied_probability, both "
                "converted to percent, only when close_line equals the bet line "
                "(same market/side/scope row); a moved line is reported as "
                "close_line_move, not as price CLV. Positive ⇒ close was a worse price than the bet. "
                "Interpret close_source: pregame_snapshot_best_odds is NOT book close."
            ),
        },
        brier_label="market_implied",
        logloss_label="market_implied",
    )
    briers: list[float] = []
    loglosses: list[float] = []
    model_briers: list[float] = []
    model_loglosses: list[float] = []
    clv_vals: list[float] = []
    line_moves: list[float] = []
    tier_buckets: dict[str, dict[str, int]] = {}
    source_buckets: dict[str, dict[str, int]] = {}
    model_p_buckets: dict[str, dict[str, Any]] = {}

    def _bump(bucket: dict[str, dict[str, int]], key: str, field_name: str) -> None:
        stats = bucket.setdefault(key, {"n": 0, "wins": 0, "losses": 0, "pushes": 0, "skipped": 0})
        stats[field_name] = stats.get(field_name, 0) + 1

    rows: list[SettleRow] = []
    for snap in predictions:
        # A quarter/half/overtime (or unknown-period) prediction cannot be graded
        # with full-game box-score stats (F10).
        if snap.scope not in (None, "full_game"):
            rows.append(SettleRow(prediction=snap, status="skipped",
                                  skip_reason="partial_period_scope"))
            report.skip_reasons["partial_period_scope"] = (
                report.skip_reasons.get("partial_period_scope", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue
        event, event_reason = _match_event(snap, events)
        if event is None:
            row = SettleRow(prediction=snap, status="skipped", skip_reason=event_reason)
            rows.append(row)
            report.skip_reasons[event_reason or "unknown"] = (
                report.skip_reasons.get(event_reason or "unknown", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue

        stats, player_reason = resolve_player_stats(event, snap.player_name, snap.team)
        if stats is None:
            row = SettleRow(
                prediction=snap,
                status="skipped",
                skip_reason=player_reason,
                provider_event_id=event.provider_event_id or None,
            )
            rows.append(row)
            report.skip_reasons[player_reason or "unknown"] = (
                report.skip_reasons.get(player_reason or "unknown", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue

        if is_unsupported_settle_market(snap.market):
            rows.append(SettleRow(
                prediction=snap,
                status="skipped",
                skip_reason="unsupported_market",
                provider_event_id=event.provider_event_id or None,
            ))
            report.skip_reasons["unsupported_market"] = (
                report.skip_reasons.get("unsupported_market", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue

        actual = player_actual(snap.market, stats)
        if actual is None:
            row = SettleRow(
                prediction=snap,
                status="skipped",
                skip_reason="unsupported_or_missing_stat",
                provider_event_id=event.provider_event_id or None,
            )
            rows.append(row)
            report.skip_reasons["unsupported_or_missing_stat"] = (
                report.skip_reasons.get("unsupported_or_missing_stat", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue

        try:
            result = grade_side(actual, snap.line, snap.position)
        except BoxScoreError:
            row = SettleRow(
                prediction=snap,
                status="skipped",
                skip_reason="unsupported_position",
                provider_event_id=event.provider_event_id or None,
                actual=actual,
            )
            rows.append(row)
            report.skip_reasons["unsupported_position"] = (
                report.skip_reasons.get("unsupported_position", 0) + 1
            )
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "skipped")
            _bump(source_buckets, snap.source, "skipped")
            continue

        market_prob = _prob_01(snap.implied_probability)
        model_prob = _prob_01(snap.model_p)
        brier = logloss = None
        brier_m = logloss_m = None
        if result in {"W", "L"}:
            won = result == "W"
            if market_prob is not None:
                brier = _brier(market_prob, won)
                logloss = _logloss(market_prob, won)
                briers.append(brier)
                loglosses.append(logloss)
            if model_prob is not None:
                brier_m = _brier(model_prob, won)
                logloss_m = _logloss(model_prob, won)
                model_briers.append(brier_m)
                model_loglosses.append(logloss_m)

        clv_pts = None
        line_move = None
        if snap.close_line is not None:
            line_move = float(snap.close_line) - float(snap.line)
            if line_move != 0.0:
                line_moves.append(line_move)
        close_p = _prob_01(snap.close_implied)
        if close_p is not None and market_prob is not None:
            # Price CLV only at the bet's own threshold; 87.5 vs 187.5 is not a price move.
            if snap.close_line is None:
                report.clv["n_excluded_close_line_unknown"] += 1
            elif line_move != 0.0:
                report.clv["n_excluded_line_mismatch"] += 1
            else:
                clv_pts = (close_p - market_prob) * 100.0
                clv_vals.append(clv_pts)

        row = SettleRow(
            prediction=snap,
            status="settled",
            provider_event_id=event.provider_event_id or None,
            actual=actual,
            result=result,
            prob=market_prob,
            prob_source="market" if market_prob is not None else "none",
            brier=brier,
            logloss=logloss,
            model_prob=model_prob,
            brier_model=brier_m,
            logloss_model=logloss_m,
            clv_implied_pts=clv_pts,
            close_line_move=line_move,
        )
        rows.append(row)
        _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "n")
        _bump(source_buckets, snap.source, "n")
        if result == "W":
            report.n_wins += 1
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "wins")
            _bump(source_buckets, snap.source, "wins")
        elif result == "L":
            report.n_losses += 1
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "losses")
            _bump(source_buckets, snap.source, "losses")
        else:
            report.n_pushes += 1
            _bump(tier_buckets, snap.confidence_tier or "UNKNOWN", "pushes")
            _bump(source_buckets, snap.source, "pushes")

        if model_prob is not None and result in {"W", "L", "P"}:
            bkey = f"{round(model_prob, 6):.6f}"
            mb = model_p_buckets.setdefault(
                bkey,
                {
                    "model_p": round(model_prob, 6),
                    "n": 0,
                    "wins": 0,
                    "losses": 0,
                    "pushes": 0,
                    "sum_model_p": 0.0,
                    "sum_model_p_decided": 0.0,
                },
            )
            mb["n"] += 1
            mb["sum_model_p"] += float(model_prob)
            if result == "W":
                mb["wins"] += 1
                mb["sum_model_p_decided"] += float(model_prob)
            elif result == "L":
                mb["losses"] += 1
                mb["sum_model_p_decided"] += float(model_prob)
            else:
                mb["pushes"] += 1

    report.n_settled = sum(1 for r in rows if r.status == "settled")
    report.n_skipped = sum(1 for r in rows if r.status == "skipped")
    decided = report.n_wins + report.n_losses
    report.hit_rate = (report.n_wins / decided) if decided else None
    report.n_scored_prob = len(briers)
    report.brier = (sum(briers) / len(briers)) if briers else None
    report.logloss = (sum(loglosses) / len(loglosses)) if loglosses else None
    report.n_scored_model = len(model_briers)
    report.brier_model = (sum(model_briers) / len(model_briers)) if model_briers else None
    report.logloss_model = (
        (sum(model_loglosses) / len(model_loglosses)) if model_loglosses else None
    )
    if line_moves:
        report.clv["n_line_moved"] = len(line_moves)
        report.clv["mean_line_move"] = sum(line_moves) / len(line_moves)
    if clv_vals:
        report.clv["status"] = "ok"
        report.clv["n"] = len(clv_vals)
        report.clv["mean_clv_implied_pts"] = sum(clv_vals) / len(clv_vals)
        sources = sorted(
            {
                p.close_source
                for p in predictions
                if p.close_source and (
                    p.close_implied is not None
                    or p.close_odds is not None
                    or p.close_line is not None
                )
            }
        )
        report.clv["close_sources"] = sources
    report.by_tier = {
        key: {
            **vals,
            "hit_rate": (
                vals["wins"] / (vals["wins"] + vals["losses"])
                if (vals["wins"] + vals["losses"])
                else None
            ),
        }
        for key, vals in tier_buckets.items()
    }
    report.by_source = {
        key: {
            **vals,
            "hit_rate": (
                vals["wins"] / (vals["wins"] + vals["losses"])
                if (vals["wins"] + vals["losses"])
                else None
            ),
        }
        for key, vals in source_buckets.items()
    }
    report.by_model_p_bucket = {}
    for key, vals in sorted(model_p_buckets.items(), key=lambda kv: -kv[1]["n"]):
        decided_b = vals["wins"] + vals["losses"]
        n_b = vals["n"]
        report.by_model_p_bucket[key] = {
            "model_p": vals["model_p"],
            "n": n_b,
            "wins": vals["wins"],
            "losses": vals["losses"],
            "pushes": vals["pushes"],
            # Predicted mean uses W+L only — pushes out of the denom (match actual).
            "predicted_hit_rate": (
                (vals["sum_model_p_decided"] / decided_b) if decided_b else None
            ),
            "actual_hit_rate": (vals["wins"] / decided_b) if decided_b else None,
        }
    report.rows = [r.to_dict() for r in rows]
    return report


def render_markdown(report: SettleReport, *, title: str = "NFL shadow settle") -> str:
    """Render a short human-readable settle scorecard."""
    clv_status = report.clv.get("status")
    clv_mean = report.clv.get("mean_clv_implied_pts")
    clv_line = f"- CLV: **{clv_status}**"
    if clv_status == "ok":
        clv_line += (
            f" — n={report.clv.get('n')} mean_implied_pts={clv_mean} "
            f"sources={report.clv.get('close_sources')} "
            f"excluded_line_mismatch={report.clv.get('n_excluded_line_mismatch')} "
            f"excluded_close_line_unknown={report.clv.get('n_excluded_close_line_unknown')} "
            f"line_moved={report.clv.get('n_line_moved')} "
            f"untimed_supplied_close={report.clv.get('n_untimed_supplied_close')}"
        )
    else:
        clv_line += f" — {report.clv.get('reason')}"

    model_brier = (
        f"**{report.brier_model}** (n={report.n_scored_model})"
        if report.brier_model is not None
        else "n/a"
    )
    model_ll = (
        f"**{report.logloss_model}** (n={report.n_scored_model})"
        if report.logloss_model is not None
        else "n/a"
    )

    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- predictions: **{report.n_predictions}**",
        f"- settled: **{report.n_settled}** (W {report.n_wins} / L {report.n_losses} / P {report.n_pushes})",
        f"- skipped: **{report.n_skipped}**",
        f"- hit rate (W/(W+L)): **{report.hit_rate if report.hit_rate is not None else 'n/a'}**",
        f"- Brier ({report.brier_label}): **{report.brier if report.brier is not None else 'n/a'}** "
        f"(n={report.n_scored_prob})",
        f"- logloss ({report.logloss_label}): **{report.logloss if report.logloss is not None else 'n/a'}** "
        f"(n={report.n_scored_prob})",
        f"- Brier (model_p): {model_brier}",
        f"- logloss (model_p): {model_ll}",
        clv_line,
        "",
        "## Blockers",
        "",
    ]
    if report.blockers:
        for key, reason in report.blockers.items():
            lines.append(f"- `{key}`: {reason}")
    else:
        lines.append("- (none)")
    if report.skip_reasons:
        lines.extend(["", "## Skip reasons", ""])
        for reason, count in sorted(report.skip_reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## By tier", ""])
    for tier, stats in sorted(report.by_tier.items()):
        lines.append(
            f"- `{tier}`: settled={stats.get('n', 0)} "
            f"W/L/P={stats.get('wins', 0)}/{stats.get('losses', 0)}/{stats.get('pushes', 0)} "
            f"hit_rate={stats.get('hit_rate')} skipped={stats.get('skipped', 0)}"
        )
    lines.extend(["", "## Calibration by model_p bucket", ""])
    lines.append(
        "Predicted = mean `model_p` over W+L (pushes excluded from denom); "
        "actual = W/(W+L). Units: probability on [0, 1]."
    )
    lines.append("")
    if report.by_model_p_bucket:
        for _key, stats in report.by_model_p_bucket.items():
            lines.append(
                f"- `model_p={stats.get('model_p')}`: n={stats.get('n', 0)} "
                f"W/L/P={stats.get('wins', 0)}/{stats.get('losses', 0)}/{stats.get('pushes', 0)} "
                f"predicted={stats.get('predicted_hit_rate')} "
                f"actual={stats.get('actual_hit_rate')}"
            )
    else:
        lines.append("- (no settled rows with model_p)")
    lines.append("")
    return "\n".join(lines)


def _load_events_from_args(args: argparse.Namespace) -> list[NflBoxScoreEvent]:
    if args.boxscores:
        return load_boxscores(args.boxscores)
    if args.provider == "nflverse":
        from outlier_nfl.boxscore_nflverse import (
            fetch_nflverse_boxscores_for_date,
            load_nflverse_events,
        )

        if args.slate_date:
            slate = date.fromisoformat(args.slate_date)
            return fetch_nflverse_boxscores_for_date(
                slate,
                season=args.season or nfl_season_for_date(slate),
                cache_dir=Path(args.nflverse_cache) if args.nflverse_cache else None,
                refresh=args.nflverse_refresh,
                allow_shrink=True if args.nflverse_allow_shrink else None,
            )
        if args.season is None or args.week is None:
            raise SystemExit("nflverse provider requires --slate-date or --season and --week")
        return load_nflverse_events(
            season=args.season,
            week=args.week,
            cache_dir=Path(args.nflverse_cache) if args.nflverse_cache else None,
            refresh=args.nflverse_refresh,
            allow_shrink=True if args.nflverse_allow_shrink else None,
        )
    raise SystemExit("Provide --boxscores and/or --provider nflverse")


# Exit code when an --out-* path already exists and --overwrite was not given.
EXIT_OUTPUT_EXISTS = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-settle NFL Tier-1 / matchup prediction snapshots against box scores."
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        required=True,
        help="Path to nfl_high_prob_props_*.json / nfl_matchup_props_*.json / calibrated props.",
    )
    parser.add_argument(
        "--boxscores",
        type=Path,
        help="Path to simplified box-score JSON (see docs/nfl/shadow-settle.md).",
    )
    parser.add_argument(
        "--provider",
        choices=("nflverse",),
        help="Optional live/offline-downloadable box provider (nflverse GitHub CSV).",
    )
    parser.add_argument("--slate-date", help="Eastern slate date YYYY-MM-DD for provider fetch.")
    parser.add_argument("--season", type=int, help="NFL season year for nflverse week stats.")
    parser.add_argument("--week", type=int, help="NFL week for nflverse week stats.")
    parser.add_argument(
        "--nflverse-cache",
        type=Path,
        help="Cache directory for nflverse CSV downloads (default ~/.cache/outlier_nflverse).",
    )
    parser.add_argument(
        "--nflverse-refresh",
        action="store_true",
        help="Re-download nflverse CSVs even if the cache is within its max age.",
    )
    parser.add_argument(
        "--nflverse-allow-shrink",
        action="store_true",
        help="Accept an nflverse refresh with fewer rows than the cached copy (upstream removal).",
    )
    parser.add_argument(
        "--source",
        choices=PREDICTION_SOURCES,
        default="auto",
        help="Prediction artifact kind (default: infer from filename).",
    )
    parser.add_argument(
        "--all-calibrated",
        action="store_true",
        help="When loading calibrated props, settle all rows (not only Tier-1 / matchup-tagged).",
    )
    parser.add_argument("--out-json", type=Path, help="Write full settle report JSON.")
    parser.add_argument("--out-md", type=Path, help="Write markdown scorecard.")
    parser.add_argument(
        "--write-boxscores",
        type=Path,
        help="When using --provider, also write the simplified box-score bundle to this path.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing --out-json / --out-md / --write-boxscores files "
        "(refused by default so a rerun never silently replaces a graded report).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    existing = [
        str(p) for p in (args.out_json, args.out_md, args.write_boxscores)
        if p is not None and Path(p).exists()
    ]
    if existing and not args.overwrite:
        # No-clobber (F27 output safety): refuse before any work, keep the files.
        print(
            "error: output already exists: " + ", ".join(existing)
            + "\nRerun with --overwrite to replace it, or choose a new --out-* path.",
            file=sys.stderr,
        )
        return EXIT_OUTPUT_EXISTS

    predictions = load_prediction_snapshot(
        args.predictions,
        source=args.source,
        require_tier1_or_matchup=not args.all_calibrated,
    )
    events = _load_events_from_args(args)
    cache_events: list[dict[str, Any]] = []
    if args.provider == "nflverse" and not args.boxscores:
        from outlier_nfl.boxscore_nflverse import drain_cache_events

        cache_events = drain_cache_events()
    if args.write_boxscores and args.provider == "nflverse":
        from outlier_nfl.boxscore_nflverse import events_to_simplified_payload

        safe_write_json(args.write_boxscores, events_to_simplified_payload(events))

    report = settle_predictions(predictions, events)
    payload = report.to_dict()
    payload["generated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    payload["predictions_path"] = str(args.predictions)
    payload["boxscores_path"] = str(args.boxscores) if args.boxscores else None
    payload["provider"] = args.provider
    payload["slate_date"] = args.slate_date
    payload["season"] = args.season
    payload["week"] = args.week
    if cache_events:
        payload["nflverse_cache"] = cache_events

    markdown = _cache_warning_lines(cache_events) + render_markdown(report)
    if args.out_json:
        safe_write_json(args.out_json, payload)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(markdown, encoding="utf-8")

    print(markdown)
    return 0


def _cache_warning_lines(cache_events: Sequence[Mapping[str, Any]]) -> str:
    """Markdown warnings for nflverse cache fallbacks / accepted shrinkage."""
    lines = []
    for event in cache_events:
        status = event.get("status")
        if status == "fallback":
            lines.append(
                f"> WARNING: nflverse `{event.get('file')}` refresh failed; settled against cached "
                f"copy fetched {event.get('fetched_at') or 'at unknown time'} ({event.get('reason')})"
            )
        elif status == "shrunk":
            lines.append(f"> WARNING: nflverse `{event.get('file')}`: {event.get('reason')}")
    return ("\n".join(lines) + "\n\n") if lines else ""


if __name__ == "__main__":
    raise SystemExit(main())
