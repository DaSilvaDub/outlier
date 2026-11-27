"""Save and reload the exact ``TraceInputs`` a best-bets card was built from.

The pipeline writes ``nfl_best_bets_inputs.json.gz`` into each run bundle, where
``RunWriter.commit`` seals it with a sha256 in the manifest. A backtest
(``outlier_nfl.gate_backtest``) can then re-trace the same slate under any gate
configuration and get the exact counterfactual card, with no network and no
degraded pillars (``rebuild`` refuses NGS/PBP, so it cannot do this).

The gate and stake configuration are recorded as metadata only: a replay
chooses its own.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from outlier_nfl.best_bets import TraceInputs

CAPTURE_NAME = "nfl_best_bets_inputs.json.gz"
CAPTURE_SCHEMA = 1

_PLAIN_FIELDS = (
    "props", "run_date", "scripts", "external_metrics", "usage_players", "weather",
    "inactive_by_team", "questionable_by_team", "tapes", "as_of_utc", "before_week",
)


def trace_inputs_to_dict(inputs: TraceInputs, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"schema": CAPTURE_SCHEMA}
    for name in _PLAIN_FIELDS:
        out[name] = getattr(inputs, name)
    # Movement is keyed by prop_key tuples; JSON needs lists.
    out["movement"] = [[list(key), value] for key, value in inputs.movement.items()]
    out["recorded_gate_config"] = inputs.gate_config.to_dict()
    out["recorded_stake_policy"] = inputs.stake_policy.to_dict()
    out["meta"] = dict(meta or {})
    return out


def trace_inputs_from_dict(raw: dict[str, Any]) -> TraceInputs:
    if raw.get("schema") != CAPTURE_SCHEMA:
        raise ValueError(f"unsupported trace capture schema: {raw.get('schema')!r}")
    before_week = raw.get("before_week")
    return TraceInputs(
        props=list(raw.get("props") or []),
        run_date=str(raw.get("run_date") or ""),
        scripts=list(raw.get("scripts") or []),
        external_metrics=list(raw.get("external_metrics") or []),
        usage_players=list(raw.get("usage_players") or []),
        weather=list(raw.get("weather") or []),
        inactive_by_team=raw.get("inactive_by_team"),  # None stays None: report not loaded
        questionable_by_team=dict(raw.get("questionable_by_team") or {}),
        tapes=dict(raw.get("tapes") or {}),
        movement={tuple(key): value for key, value in raw.get("movement") or []},
        as_of_utc=raw.get("as_of_utc"),
        before_week=int(before_week) if before_week is not None else None,
    )


def save_trace_inputs(path: Path | str, inputs: TraceInputs, *, meta: dict[str, Any] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(trace_inputs_to_dict(inputs, meta=meta), default=str, separators=(",", ":"))
    # mtime=0 and no embedded filename: the same inputs always give the same bytes,
    # so two rebuilds of one slate stay byte-identical.
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(data.encode("utf-8"))
    return path


def load_trace_inputs(path: Path | str) -> tuple[TraceInputs, dict[str, Any]]:
    """Return the inputs and the capture's metadata (date, window, suffix, updated_at)."""
    with gzip.open(Path(path), "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    return trace_inputs_from_dict(raw), dict(raw.get("meta") or {})
