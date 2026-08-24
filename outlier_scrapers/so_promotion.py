"""Gate independent-SO Kelly promotion on settled eval + config.

Default remains off. Force with OUTLIER_PROMOTE_INDEPENDENT_SO=1, or enable
auto mode (OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO=1 / config.auto_promote) which
only clears when so_eval on gamelog-settled rows prefers independent (or
market-tempered sizing) over market Brier.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from outlier_scrapers.paths import CONFIG_DIR, PROJECT_ROOT
from outlier_scrapers.so_sizing_blend import temper_independent_prob

DEFAULT_CONFIG_PATH = CONFIG_DIR / "so_promotion.json"
DEFAULT_DB = PROJECT_ROOT / "calibration" / "feedback.sqlite3"

_DEFAULTS: dict[str, Any] = {
    "auto_promote": False,
    "min_settled_gamelog": 20,
    "require_v2_hash": True,
    "require_prefer_independent": True,
    "prefer_tempered_over_market": True,
    "temper_independent_weight": 0.55,
    "soft_reliability_fallback": 0.45,
    "max_units": 2.0,
}

# Re-export for callers that imported temper from this module.
__all__ = [
    "DEFAULT_CONFIG_PATH",
    "auto_promote_env_enabled",
    "clear_promotion_cache",
    "evaluate_promotion_gate",
    "force_promote_enabled",
    "independent_so_sizing_enabled",
    "load_so_promotion_config",
    "promotion_readiness",
    "temper_independent_prob",
]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
    return default


def force_promote_enabled() -> bool:
    """Operator override: size from independent SO regardless of ledger."""
    return _env_truthy("OUTLIER_PROMOTE_INDEPENDENT_SO")


def auto_promote_env_enabled() -> bool:
    return _env_truthy("OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO")


def load_so_promotion_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or DEFAULT_CONFIG_PATH
    payload = dict(_DEFAULTS)
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            for key, default in _DEFAULTS.items():
                if key not in raw:
                    continue
                value = raw[key]
                if isinstance(default, bool):
                    payload[key] = _as_bool(value, default)
                elif isinstance(default, int) and not isinstance(default, bool):
                    try:
                        payload[key] = int(value)
                    except (TypeError, ValueError):
                        pass
                elif isinstance(default, float):
                    try:
                        payload[key] = float(value)
                    except (TypeError, ValueError):
                        pass
                else:
                    payload[key] = value
    return payload


def promotion_readiness(
    report: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interpret an so_eval report against promotion thresholds."""
    cfg = config or load_so_promotion_config()
    min_n = int(cfg.get("min_settled_gamelog") or 20)
    require_v2 = bool(cfg.get("require_v2_hash", True))
    require_prefer = bool(cfg.get("require_prefer_independent", True))
    prefer_tempered = bool(cfg.get("prefer_tempered_over_market", True))

    status = str(report.get("status") or "")
    n = int(report.get("n") or 0)
    v2 = int(report.get("gamelog_v2_rows") or 0)
    prefer_raw = bool(report.get("prefer_independent"))
    prefer_soft = bool(report.get("prefer_soft_independent", False))
    prefer_temp = bool(report.get("prefer_tempered_independent", False))
    # Gate must match live Kelly: temper(raw_indep, market). Soft is diagnostic only.
    prefer_ok = prefer_raw or (prefer_tempered and prefer_temp)

    ready = (
        status == "ok"
        and n >= min_n
        and (not require_v2 or v2 >= min_n)
        and (not require_prefer or prefer_ok)
    )
    reasons: list[str] = []
    if status != "ok":
        reasons.append(f"status={status}")
    if n < min_n:
        reasons.append(f"n={n}<{min_n}")
    if require_v2 and v2 < min_n:
        reasons.append(f"gamelog_v2_rows={v2}<{min_n}")
    if require_prefer and not prefer_ok:
        reasons.append("independent_not_preferred_over_market")
    if prefer_soft and not prefer_temp and not prefer_raw:
        reasons.append("soft_only_prefer_insufficient_for_live_temper")
    return {
        "ready": ready,
        "n": n,
        "gamelog_v2_rows": v2,
        "prefer_independent": prefer_raw,
        "prefer_soft_independent": prefer_soft,
        "prefer_tempered_independent": prefer_temp,
        "min_settled_gamelog": min_n,
        "require_v2_hash": require_v2,
        "reasons": reasons,
    }


@lru_cache(maxsize=8)
def _cached_readiness(
    db_key: str,
    config_path_key: str,
    config_mtime: float,
    db_mtime: float,
) -> dict[str, Any]:
    from outlier_scrapers.so_eval import evaluate_so_probs

    del config_mtime, db_mtime  # cache keys only
    cfg = load_so_promotion_config(Path(config_path_key) if config_path_key else None)
    require_v2 = bool(cfg.get("require_v2_hash", True))
    if not db_key:
        report = {
            "status": "missing_db",
            "n": 0,
            "gamelog_v2_rows": 0,
            "prefer_independent": False,
        }
    else:
        report = evaluate_so_probs(
            Path(db_key),
            require_gamelog_hash=True,
            # Prefer metrics must score the same hash family live Kelly uses.
            require_v2_hash=require_v2,
            include_tempered=True,
            soft_reliability=float(cfg.get("soft_reliability_fallback") or 0.45),
            temper_independent_weight=float(cfg.get("temper_independent_weight") or 0.55),
        )
    readiness = promotion_readiness(report, cfg)
    readiness["report_status"] = report.get("status")
    readiness["market_brier"] = report.get("market_brier")
    readiness["independent_brier"] = report.get("independent_brier")
    readiness["tempered_brier"] = report.get("tempered_brier")
    return readiness


def evaluate_promotion_gate(
    *,
    db_path: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Fresh (cached per db/config mtime) promotion readiness snapshot."""
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    db = db_path or DEFAULT_DB
    cfg_mtime = cfg_path.stat().st_mtime if cfg_path.exists() else 0.0
    db_key = str(db.resolve()) if db.exists() else ""
    db_mtime = db.stat().st_mtime if db.exists() else 0.0
    return dict(
        _cached_readiness(
            db_key,
            str(cfg_path.resolve()) if cfg_path.exists() else "",
            cfg_mtime,
            db_mtime,
        )
    )


def independent_so_sizing_enabled(
    *,
    db_path: Path | None = None,
) -> bool:
    """True when force-env is set, or auto mode clears the ledger gate."""
    if force_promote_enabled():
        return True
    cfg = load_so_promotion_config()
    if not (auto_promote_env_enabled() or bool(cfg.get("auto_promote"))):
        return False
    gate = evaluate_promotion_gate(db_path=db_path)
    return bool(gate.get("ready"))


def clear_promotion_cache() -> None:
    _cached_readiness.cache_clear()
