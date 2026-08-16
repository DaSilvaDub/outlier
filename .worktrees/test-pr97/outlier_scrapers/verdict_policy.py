"""Load config/verdict_policy.json with the same defensive pattern as portfolio."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DeskProhibitedMarket:
    market: str
    scope: str


@dataclass(frozen=True)
class VerdictPolicy:
    mode: str = "shadow"
    repair_attempts: int = 1
    reject_fail_ratio: float = 0.5
    prohibited_variance_markets: tuple[str, ...] = (
        "3PM",
        "THREES",
        "THREE_POINTERS_MADE",
        "HA",
        "HITS_ALLOWED",
        "TO",
        "TURNOVERS",
    )
    desk_prohibited_markets: tuple[DeskProhibitedMarket, ...] = (
        DeskProhibitedMarket("HR", "any"),
        DeskProhibitedMarket("WALKS_ALLOWED", "any"),
        DeskProhibitedMarket("HRR", "any"),
        DeskProhibitedMarket("BB", "PLAYER_PROP"),
    )
    enforce_mlb_whitelist: bool = True
    external_evidence_max_age_h: float = 24.0


REQUIRED_POLICY_KEYS = {
    "mode",
    "repair_attempts",
    "reject_fail_ratio",
    "prohibited_variance_markets",
    "desk_prohibited_markets",
    "enforce_mlb_whitelist",
    "external_evidence_max_age_h",
}


def _parse_desk_markets(raw: Any) -> tuple[DeskProhibitedMarket, ...]:
    if not isinstance(raw, list):
        raise ValueError("desk_prohibited_markets must be a list of {market, scope} objects")
    parsed: list[DeskProhibitedMarket] = []
    for item in raw:
        if not isinstance(item, dict) or "market" not in item or "scope" not in item:
            raise ValueError(f"desk_prohibited_markets entry must have market and scope, got {item!r}")
        extra = set(item) - {"market", "scope"}
        if extra:
            raise ValueError(f"Unknown keys in desk_prohibited_markets entry: {extra}")
        parsed.append(DeskProhibitedMarket(market=str(item["market"]), scope=str(item["scope"])))
    return tuple(parsed)


def load_verdict_policy(path: Path | str | None = None) -> VerdictPolicy:
    """Missing file yields defaults. Unknown top-level keys raise."""
    if path is None:
        from outlier_scrapers import paths

        path = paths.PROJECT_ROOT / "config" / "verdict_policy.json"
    else:
        path = Path(path)
    if not path.exists():
        return VerdictPolicy()

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("verdict_policy.json must be an object")

    extra = set(data) - REQUIRED_POLICY_KEYS
    if extra:
        raise ValueError(f"Unknown keys in verdict policy schema: {extra}")

    mode = data.get("mode", "shadow")
    if mode not in ("shadow", "enforce"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'shadow' or 'enforce'.")
    attempts = data.get("repair_attempts", 1)
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        raise ValueError(f"repair_attempts must be a non-negative int, got {attempts!r}")
    ratio = data.get("reject_fail_ratio", 0.5)
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= float(ratio) <= 1:
        raise ValueError(f"reject_fail_ratio must be in [0, 1], got {ratio!r}")
    variance = data.get("prohibited_variance_markets")
    if variance is None:
        variance_tuple = VerdictPolicy().prohibited_variance_markets
    else:
        if not isinstance(variance, list) or not all(isinstance(item, str) for item in variance):
            raise ValueError("prohibited_variance_markets must be a list of strings")
        variance_tuple = tuple(variance)
    markets = data.get("desk_prohibited_markets")
    desk_tuple = (
        VerdictPolicy().desk_prohibited_markets if markets is None else _parse_desk_markets(markets)
    )
    whitelist = data.get("enforce_mlb_whitelist", True)
    if not isinstance(whitelist, bool):
        raise ValueError("enforce_mlb_whitelist must be a boolean")
    age = data.get("external_evidence_max_age_h", 24.0)
    if not isinstance(age, (int, float)) or isinstance(age, bool) or float(age) <= 0:
        raise ValueError(f"external_evidence_max_age_h must be positive, got {age!r}")
    return VerdictPolicy(
        mode=mode,
        repair_attempts=attempts,
        reject_fail_ratio=float(ratio),
        prohibited_variance_markets=variance_tuple,
        desk_prohibited_markets=desk_tuple,
        enforce_mlb_whitelist=whitelist,
        external_evidence_max_age_h=float(age),
    )
