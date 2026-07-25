"""Drawdown-based automatic stake reduction (Track C3).

Drawdown is derived only from settled **placed** wagers in the authoritative
ledger. Unsettled recommendations and hypothetical shadow stakes never update
equity. ``drawdown_multiplier`` is always in [0, 1] and monotone with respect
to worse drawdown tiers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from outlier_scrapers import paths

SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = paths.PROJECT_ROOT / "calibration" / "drawdown_state.json"

# Provisional tiers — product must sign off thresholds before enforce activation.
DEFAULT_TIERS: tuple[dict[str, Any], ...] = (
    {"name": "neutral", "max_drawdown_pct": 0.05, "multiplier": 1.0},
    {"name": "reduced", "max_drawdown_pct": 0.10, "multiplier": 0.70},
    {"name": "strongly_reduced", "max_drawdown_pct": 0.15, "multiplier": 0.40},
    {"name": "stand_down", "max_drawdown_pct": 1.0, "multiplier": 0.0},
)


def _float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class DrawdownState:
    equity: float
    high_water_mark: float
    drawdown_pct: float
    as_of: str
    included_decision_cutoff: str
    tier: str
    drawdown_multiplier: float
    sample_count: int
    status: str
    reason: str | None = None
    tiers_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "equity": self.equity,
            "high_water_mark": self.high_water_mark,
            "drawdown_pct": self.drawdown_pct,
            "as_of": self.as_of,
            "included_decision_cutoff": self.included_decision_cutoff,
            "tier": self.tier,
            "drawdown_multiplier": self.drawdown_multiplier,
            "sample_count": self.sample_count,
            "status": self.status,
            "reason": self.reason,
            "tiers_fingerprint": self.tiers_fingerprint,
        }


def tiers_fingerprint(tiers: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(list(tiers), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def validate_tiers(tiers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Require monotone max_drawdown_pct ascending and multipliers non-increasing."""
    if not tiers:
        raise ValueError("at least one drawdown tier is required")
    cleaned: list[dict[str, Any]] = []
    prev_dd = -1.0
    prev_mult = 2.0
    for raw in tiers:
        name = str(raw.get("name") or "").strip()
        max_dd = _float(raw.get("max_drawdown_pct"))
        mult = _float(raw.get("multiplier"))
        if not name or max_dd is None or mult is None:
            raise ValueError("each tier needs name, max_drawdown_pct, multiplier")
        if max_dd < 0 or max_dd > 1.0:
            raise ValueError("max_drawdown_pct must be in [0, 1]")
        if mult < 0 or mult > 1.0:
            raise ValueError("multiplier must be in [0, 1]")
        if max_dd < prev_dd:
            raise ValueError("tiers must be ordered by non-decreasing max_drawdown_pct")
        if mult > prev_mult + 1e-15:
            raise ValueError("tier multipliers must be non-increasing as drawdown worsens")
        cleaned.append(
            {"name": name, "max_drawdown_pct": max_dd, "multiplier": mult}
        )
        prev_dd = max_dd
        prev_mult = mult
    return cleaned


def resolve_tier(
    drawdown_pct: float, tiers: Sequence[Mapping[str, Any]]
) -> tuple[str, float]:
    """Return (tier_name, multiplier) for the current drawdown percentage."""
    cleaned = validate_tiers(tiers)
    dd = max(0.0, float(drawdown_pct))
    for tier in cleaned:
        if dd <= float(tier["max_drawdown_pct"]) + 1e-15:
            return str(tier["name"]), float(tier["multiplier"])
    last = cleaned[-1]
    return str(last["name"]), float(last["multiplier"])


def _execution_sort_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    settled = str(row.get("settled_at") or row.get("placed_at") or row.get("as_of") or "")
    decision = str(row.get("decision_id") or "")
    snapshot = str(row.get("snapshot_id") or "")
    return settled, decision, snapshot


def _is_settled_placed(row: Mapping[str, Any]) -> bool:
    status = str(row.get("execution_status") or row.get("status") or "").strip().upper()
    # Accept explicit PLACED→SETTLED chain or settlement rows with PnL when
    # execution_status is already SETTLED. PROPOSED / CANCELLED never count.
    if status in {"PROPOSED", "CANCELLED", "PLACED"}:
        return False
    if status == "SETTLED":
        return True
    # Settlement-only rows from feedback (no execution table) with realized pnl.
    result = str(row.get("win_loss_push") or "").strip().upper()
    if result in {"W", "L", "PUSH"} and row.get("pnl") not in (None, ""):
        # Require placed_units when present; if absent, allow only when
        # units > 0 as a historical stand-in for placed stake.
        placed = _float(row.get("placed_units"))
        if placed is not None:
            return placed > 0
        units = _float(row.get("units"))
        return units is not None and units > 0
    return False


def compute_drawdown_state(
    settled_executions: Iterable[Mapping[str, Any]],
    *,
    as_of: datetime,
    tiers: Sequence[Mapping[str, Any]] = DEFAULT_TIERS,
    starting_equity: float = 0.0,
) -> DrawdownState:
    """Build chronological equity from settled placed PnL and resolve the tier.

    Equity curve is cumulative realized PnL in units from ``starting_equity``.
    High-water mark is the running maximum equity. Drawdown percentage is
    (HWM - equity) / HWM when HWM > 0, else 0.
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)
    cleaned_tiers = validate_tiers(tiers)
    fp = tiers_fingerprint(cleaned_tiers)

    rows = [dict(row) for row in settled_executions if _is_settled_placed(row)]
    usable: list[Mapping[str, Any]] = []
    for row in rows:
        settled = _parse_timestamp(
            row.get("settled_at") or row.get("placed_at") or row.get("as_of")
        )
        if settled is None:
            continue
        if settled > as_of:
            continue  # reject future-dated
        pnl = _float(row.get("pnl"))
        if pnl is None:
            continue
        usable.append(row)

    usable.sort(key=_execution_sort_key)

    equity = float(starting_equity)
    hwm = float(starting_equity)
    cutoff = ""
    for row in usable:
        pnl = _float(row.get("pnl")) or 0.0
        equity = round(equity + pnl, 10)
        if equity > hwm:
            hwm = equity
        cutoff = str(
            row.get("settled_at")
            or row.get("decision_id")
            or row.get("snapshot_id")
            or cutoff
        )

    if hwm > 0:
        drawdown_pct = max(0.0, (hwm - equity) / hwm)
    else:
        drawdown_pct = 0.0 if equity >= hwm else 1.0

    if not usable:
        return DrawdownState(
            equity=round(equity, 10),
            high_water_mark=round(hwm, 10),
            drawdown_pct=0.0,
            as_of=as_of.isoformat(),
            included_decision_cutoff="",
            tier="neutral",
            drawdown_multiplier=1.0,
            sample_count=0,
            status="missing",
            reason="no_settled_placed_executions",
            tiers_fingerprint=fp,
        )

    tier_name, multiplier = resolve_tier(drawdown_pct, cleaned_tiers)
    reason = "drawdown_stop" if multiplier <= 0.0 else None
    return DrawdownState(
        equity=round(equity, 10),
        high_water_mark=round(hwm, 10),
        drawdown_pct=round(drawdown_pct, 10),
        as_of=as_of.isoformat(),
        included_decision_cutoff=cutoff,
        tier=tier_name,
        drawdown_multiplier=_clamp01(multiplier),
        sample_count=len(usable),
        status="active",
        reason=reason,
        tiers_fingerprint=fp,
    )


def write_drawdown_state(state: DrawdownState, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)
    return output_path


def load_drawdown_state(path: Path = DEFAULT_STATE_PATH) -> DrawdownState | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        return DrawdownState(
            equity=float(payload["equity"]),
            high_water_mark=float(payload["high_water_mark"]),
            drawdown_pct=float(payload["drawdown_pct"]),
            as_of=str(payload["as_of"]),
            included_decision_cutoff=str(payload.get("included_decision_cutoff") or ""),
            tier=str(payload.get("tier") or "neutral"),
            drawdown_multiplier=_clamp01(float(payload.get("drawdown_multiplier", 1.0))),
            sample_count=int(payload.get("sample_count") or 0),
            status=str(payload.get("status") or "missing"),
            reason=payload.get("reason"),
            tiers_fingerprint=str(payload.get("tiers_fingerprint") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def resolve_drawdown_multiplier(
    state: DrawdownState | None,
    *,
    as_of: datetime | None = None,
    neutral_on_failure: bool = True,
    max_age_hours: float | None = None,
) -> tuple[float, str]:
    """Return (multiplier, reason). Missing/stale/future state is neutral in shadow."""
    if state is None:
        return (1.0 if neutral_on_failure else 0.0), "missing_drawdown_state"
    if state.status not in {"active"}:
        return (1.0 if neutral_on_failure else 0.0), state.reason or state.status

    state_ts = _parse_timestamp(state.as_of)
    if state_ts is None:
        return (1.0 if neutral_on_failure else 0.0), "ambiguous_drawdown_timestamp"

    if as_of is not None:
        as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        as_of_utc = as_of_utc.astimezone(timezone.utc)
        if state_ts > as_of_utc:
            return (1.0 if neutral_on_failure else 0.0), "future_dated_drawdown_state"
        if max_age_hours is not None:
            age_h = (as_of_utc - state_ts).total_seconds() / 3600.0
            if age_h > max_age_hours:
                return (1.0 if neutral_on_failure else 0.0), "stale_drawdown_state"

    return _clamp01(state.drawdown_multiplier), state.tier
