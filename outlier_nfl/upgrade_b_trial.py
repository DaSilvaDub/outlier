"""Upgrade B live trial: gate / stake configuration and pre-registered stop criteria.

Upgrade B (PR #382) was merged on a backtest whose thresholds were chosen on the
same weeks that scored them. Until it earns trust on live slates, the pipeline
reads ``config/nfl_upgrade_b_trial.json`` for

* ``gates``: which Upgrade B gates are on, and their thresholds (``GateConfig``);
* ``stake``: how VALIDATED picks are staked during the trial (``StakePolicy``),
  flat or paper instead of quarter-Kelly;
* ``stop_criteria``: the review rules, written down before any result came in.
  ``outlier_nfl.trial_scorecard`` evaluates them mechanically.

A missing file means no trial: Upgrade B gates as merged, quarter-Kelly stakes.
A malformed file raises, so a typo can never quietly put money back on Kelly.
The plan and the reasoning behind each number: docs/plans/2026-11-23-nfl-upgrade-b-live-trial.md
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from outlier_nfl.best_bets import GateConfig, StakePolicy

DEFAULT_TRIAL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "nfl_upgrade_b_trial.json"
TRIAL_CONFIG_ENV = "OUTLIER_NFL_TRIAL_CONFIG"


@dataclass(frozen=True)
class StopCriteria:
    """Pre-registered review rules for the trial (see the plan doc for the reasoning)."""

    # Win rate: judged only once enough picks have settled to mean anything.
    win_rate_min_picks: int = 100
    win_rate_floor: float = 0.53
    # CLV converges faster than win rate, so it is the early signal.
    clv_min_picks: int = 40
    clv_mean_floor_pts: float = 0.0
    # Hard stop on flat-stake losses, in units of one flat stake.
    max_drawdown_units: float = 15.0
    # Evidence that the gates are real: enough picks and a win-rate lower bound above breakeven.
    success_min_picks: int = 200
    breakeven_win_rate: float = 0.524

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> StopCriteria:
        raw = dict(raw or {})
        extra = set(raw) - set(cls.__dataclass_fields__)
        if extra:
            raise ValueError(f"unknown stop_criteria key(s): {sorted(extra)}")
        return cls(**raw)


@dataclass(frozen=True)
class TrialConfig:
    active: bool
    gate_config: GateConfig = field(default_factory=GateConfig)
    stake_policy: StakePolicy = field(default_factory=StakePolicy)
    stop_criteria: StopCriteria = field(default_factory=StopCriteria)
    name: str | None = None
    started: str | None = None
    review_after: str | None = None
    weeks: tuple[int, ...] = ()
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "name": self.name,
            "started": self.started,
            "review_after": self.review_after,
            "weeks": list(self.weeks),
            "source": self.source,
            "gates": self.gate_config.to_dict(),
            "stake": self.stake_policy.to_dict(),
            "stop_criteria": {k: getattr(self.stop_criteria, k) for k in StopCriteria.__dataclass_fields__},
        }


_TOP_LEVEL_KEYS = {"schema", "name", "started", "review_after", "weeks", "gates", "stake",
                   "stop_criteria", "note"}


def trial_config_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(TRIAL_CONFIG_ENV)
    return Path(env) if env else DEFAULT_TRIAL_CONFIG_PATH


def parse_trial_config(raw: Mapping[str, Any], *, source: str | None = None) -> TrialConfig:
    if not isinstance(raw, Mapping):
        raise TypeError("trial config must be a JSON object")
    extra = set(raw) - _TOP_LEVEL_KEYS
    if extra:
        raise ValueError(f"unknown trial config key(s): {sorted(extra)}")
    if raw.get("schema") != 1:
        raise ValueError(f"unsupported trial config schema: {raw.get('schema')!r}")
    stake_raw = dict(raw.get("stake") or {})
    stake_extra = set(stake_raw) - {"mode", "flat_fraction", "label"}
    if stake_extra:
        raise ValueError(f"unknown stake key(s): {sorted(stake_extra)}")
    stake = StakePolicy(
        mode=str(stake_raw.get("mode") or "kelly"),
        flat_fraction=float(stake_raw.get("flat_fraction") or 0.0),
        label=stake_raw.get("label") or raw.get("name"),
    )
    return TrialConfig(
        active=True,
        gate_config=GateConfig.from_dict(raw.get("gates")),
        stake_policy=stake,
        stop_criteria=StopCriteria.from_dict(raw.get("stop_criteria")),
        name=raw.get("name"),
        started=raw.get("started"),
        review_after=raw.get("review_after"),
        weeks=tuple(int(w) for w in raw.get("weeks") or ()),
        source=source,
    )


def load_trial_config(path: Path | str | None = None) -> TrialConfig:
    """Read the trial config; absent file -> inactive (merged defaults, quarter-Kelly)."""
    target = trial_config_path(path)
    if not target.is_file():
        return TrialConfig(active=False, source=str(target))
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"trial config is not valid JSON: {target}: {exc}") from exc
    return parse_trial_config(raw, source=str(target))
