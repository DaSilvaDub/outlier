"""Replay captured slates under different Upgrade B gate settings and grade the picks.

Two questions, both answered from the exact inputs each live card was traced from
(``nfl_best_bets_inputs.json.gz`` in the run bundle, see ``trace_capture``):

``ablation``
    Which gates earn their place? Scores the VALIDATED picks of
    * ``legacy``: no Upgrade B (the pre-PR-#382 ranking),
    * ``only:<gate>``: legacy plus that one gate,
    * ``all_but:<gate>``: Upgrade B without that one gate,
    * ``upgrade_b``: everything on.
    A gate is worth keeping when ``only:<gate>`` beats ``legacy`` and
    ``all_but:<gate>`` does worse than ``upgrade_b``, in units and in CLV.

``walk-forward``
    Were the thresholds fitted to noise? Picks the best threshold set on slates
    before ``--split-date`` only, then scores slates on or after it once, next to
    the merged Upgrade B defaults and the legacy ranking. Nothing is tuned on the
    test slates.

Each bet is graded once (box scores + optional verified book close) and the
result reused by every configuration, so arms differ only in which picks they
VALIDATE. Pick selection mirrors the live trial: the latest pre-kickoff verdict
per bet (``trial_scorecard.collect_picks``).

Usage (on the machine that holds data/NFL)::

    python -m outlier_nfl.gate_backtest ablation \\
        --captures "data/NFL/runs/*/nfl_best_bets_inputs.json.gz" \\
        --provider nflverse --season 2026 --weeks 12 13 14 15 \\
        --out-json reports/NFL/upgrade_b_ablation.json --out-md reports/NFL/upgrade_b_ablation.md

    python -m outlier_nfl.gate_backtest walk-forward --split-date 2026-12-07 ...
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from outlier_nfl.best_bets import (
    UPGRADE_B_GATES,
    VALIDATED,
    GateConfig,
    TraceInputs,
    build_best_bets,
)
from outlier_nfl.boxscore import NflBoxScoreEvent
from outlier_nfl.enrich_close import load_book_close_feed
from outlier_nfl.trace_capture import load_trace_inputs
from outlier_nfl.trial_scorecard import (
    add_event_args,
    arm_metrics,
    collect_picks,
    events_from_args,
    pick_identity,
    settle_picks,
    write_outputs,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slate:
    path: Path
    inputs: TraceInputs
    meta: Mapping[str, Any]

    @property
    def run_date(self) -> str:
        return str(self.inputs.run_date or self.meta.get("date") or "")


def load_slates(patterns: Iterable[str], *, latest_per_slate: bool = True) -> list[Slate]:
    """Load captures; by default keep only the last capture of each slate (date + window)."""
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern)) or ([pattern] if Path(pattern).is_file() else [])
        paths.extend(Path(m) for m in matched)
    slates = []
    for path in dict.fromkeys(paths):
        inputs, meta = load_trace_inputs(path)
        slates.append(Slate(path, inputs, meta))
    if latest_per_slate:
        latest: dict[str, Slate] = {}
        for s in slates:
            key = str(s.meta.get("suffix") or f"{s.meta.get('date')}|{s.meta.get('window')}")
            if key not in latest or str(s.meta.get("updated_at") or "") >= str(latest[key].meta.get("updated_at") or ""):
                latest[key] = s
        slates = list(latest.values())
    return sorted(slates, key=lambda s: (s.run_date, str(s.meta.get("updated_at") or "")))


def replay(slates: Sequence[Slate], cfg: GateConfig) -> list[tuple[Path, dict[str, Any]]]:
    cards = []
    for s in slates:
        card = build_best_bets(replace(s.inputs, gate_config=cfg))
        card["updated_at"] = s.meta.get("updated_at")
        cards.append((s.path, card))
    return cards


class OutcomeCache:
    """Grade each bet once; every configuration reuses the same W/L/P and CLV."""

    def __init__(
        self,
        events: Sequence[NflBoxScoreEvent],
        close_index: Mapping[tuple[Any, ...], Mapping[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.close_index = close_index
        self._cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    def outcomes(self, picks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        todo = [p for p in picks if pick_identity(p) not in self._cache]
        if todo:
            for pick, outcome in zip(todo, settle_picks(todo, self.events, close_index=self.close_index)):
                self._cache[pick_identity(pick)] = outcome
        return [self._cache[pick_identity(p)] for p in picks]


def score(slates: Sequence[Slate], cfg: GateConfig, cache: OutcomeCache) -> dict[str, Any]:
    """Flat-stake metrics of the VALIDATED picks this configuration would have made."""
    picks = [p for p in collect_picks(replay(slates, cfg)) if p.get("verdict") == VALIDATED]
    rows = [{**p, **o} for p, o in zip(picks, cache.outcomes(picks))]
    return arm_metrics(rows)


def ablation_configs() -> list[tuple[str, GateConfig]]:
    """legacy, each gate alone on legacy, Upgrade B minus each gate, and Upgrade B."""
    full, legacy = GateConfig(), GateConfig.legacy()
    out = [("legacy", legacy)]
    out += [(f"only:{g}", legacy.with_gates({g})) for g in UPGRADE_B_GATES]
    out += [(f"all_but:{g}", replace(full, disabled=frozenset({g}))) for g in UPGRADE_B_GATES]
    out.append(("upgrade_b", full))
    return out


def run_ablation(slates: Sequence[Slate], cache: OutcomeCache) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for name, cfg in ablation_configs():
        logger.info("ablation: %s", name)
        rows[name] = {"config": cfg.to_dict(), **score(slates, cfg, cache)}
    legacy, full = rows["legacy"], rows["upgrade_b"]
    for name, m in rows.items():
        m["units_vs_legacy"] = round(m["flat_units"] - legacy["flat_units"], 3)
        m["units_vs_upgrade_b"] = round(m["flat_units"] - full["flat_units"], 3)
    verdicts = {}
    for g in UPGRADE_B_GATES:
        alone, without = rows[f"only:{g}"], rows[f"all_but:{g}"]
        helps_alone = alone["flat_units"] > legacy["flat_units"]
        needed_in_full = without["flat_units"] < full["flat_units"]
        verdicts[g] = ("keep" if helps_alone and needed_in_full
                       else "drop_candidate" if not helps_alone and not needed_in_full
                       else "mixed")
    return {"mode": "ablation", "slates": [str(s.path) for s in slates], "arms": rows, "gate_verdicts": verdicts}


GRIDS: dict[str, dict[str, Sequence[Any]]] = {
    "small": {
        "l10_min": (0.5, 0.6, 0.7),
        "season_min": (0.5, 0.6, 0.7),
        "max_odds": (135, 250),
        "kappa_standard": (8.0, 12.0, 20.0),
    },
    "full": {
        "l5_min": (0.5, 0.6),
        "l10_min": (0.5, 0.6, 0.7),
        "season_min": (0.5, 0.6, 0.7),
        "min_odds": (-200, -155),
        "max_odds": (135, 250),
        "kappa_standard": (8.0, 12.0, 20.0),
    },
}


def grid_configs(grid: Mapping[str, Sequence[Any]], base: GateConfig | None = None) -> list[GateConfig]:
    base = base or GateConfig()
    keys = sorted(grid)
    return [replace(base, **dict(zip(keys, values))) for values in itertools.product(*(grid[k] for k in keys))]


def run_walk_forward(
    slates: Sequence[Slate],
    cache: OutcomeCache,
    *,
    split_date: str,
    grid: Mapping[str, Sequence[Any]],
    min_train_picks: int = 30,
) -> dict[str, Any]:
    train = [s for s in slates if s.run_date < split_date]
    test = [s for s in slates if s.run_date >= split_date]
    if not train or not test:
        raise SystemExit(f"split {split_date} leaves train={len(train)} test={len(test)} slates; need both")
    scored = []
    for i, cfg in enumerate(grid_configs(grid), 1):
        logger.info("walk-forward train %d", i)
        m = score(train, cfg, cache)
        scored.append((cfg, m))
    eligible = [(c, m) for c, m in scored if m["graded"] >= min_train_picks and m["flat_roi"] is not None]
    if not eligible:
        raise SystemExit(f"no grid config has {min_train_picks}+ graded train picks; lower --min-train-picks")
    eligible.sort(key=lambda cm: (cm[1]["flat_roi"], cm[1]["graded"]), reverse=True)
    chosen_cfg, chosen_train = eligible[0]
    result = {
        "mode": "walk_forward",
        "split_date": split_date,
        "train_slates": [str(s.path) for s in train],
        "test_slates": [str(s.path) for s in test],
        "grid": {k: list(v) for k, v in grid.items()},
        "min_train_picks": min_train_picks,
        "grid_size": len(scored),
        "chosen": {"config": chosen_cfg.to_dict(), "train": chosen_train, "test": score(test, chosen_cfg, cache)},
        "upgrade_b_defaults": {"train": score(train, GateConfig(), cache), "test": score(test, GateConfig(), cache)},
        "legacy": {"train": score(train, GateConfig.legacy(), cache), "test": score(test, GateConfig.legacy(), cache)},
        # How flat is the top of the train ranking? If many configs are close, the choice is noise.
        "train_top5": [{"config": {k: getattr(c, k) for k in grid}, "flat_roi": m["flat_roi"], "graded": m["graded"]}
                       for c, m in eligible[:5]],
    }
    return result


def _fmt(value: Any, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _metric_row(name: str, m: Mapping[str, Any]) -> str:
    return (f"| {name} | {m['graded']} | {m['wins']}-{m['losses']}-{m['pushes']} | {_fmt(m['win_rate'], '.1%')} | "
            f"{m['flat_units']:+.2f} | {_fmt(m['flat_roi'], '+.1%')} | {m['clv_n']} | {_fmt(m['clv_mean_pts'], '+.2f')} |")


_HEADER = ["| Arm | Graded | W-L-P | Win % | Units | ROI | CLV n | CLV mean |", "|---|---|---|---|---|---|---|---|"]


def render_markdown(result: Mapping[str, Any]) -> str:
    if result["mode"] == "ablation":
        lines = ["# Upgrade B gate ablation", "", f"Slates: {len(result['slates'])} (flat 1u per VALIDATED pick)", "",
                 *_HEADER]
        lines += [_metric_row(n, m) for n, m in result["arms"].items()]
        lines += ["", "## Gate verdicts", "",
                  "keep = helps alone and is missed when removed; drop_candidate = neither; mixed = one of the two.", ""]
        lines += [f"- {g}: **{v}**" for g, v in result["gate_verdicts"].items()]
        return "\n".join(lines) + "\n"
    lines = ["# Upgrade B walk-forward", "",
             (f"Train: {len(result['train_slates'])} slates before {result['split_date']} | "
             f"test: {len(result['test_slates'])} slates | grid: {result['grid_size']} configs, "
             f"min {result['min_train_picks']} train picks"), "",
             "## Test slates (scored once, no tuning)", "", *_HEADER,
             _metric_row("chosen on train", result["chosen"]["test"]),
             _metric_row("Upgrade B defaults", result["upgrade_b_defaults"]["test"]),
             _metric_row("legacy", result["legacy"]["test"]),
             "", "## Train slates", "", *_HEADER,
             _metric_row("chosen on train", result["chosen"]["train"]),
             _metric_row("Upgrade B defaults", result["upgrade_b_defaults"]["train"]),
             _metric_row("legacy", result["legacy"]["train"]),
             "", "## Chosen thresholds", "", "```json",
             json.dumps({k: result["chosen"]["config"][k] for k in result["grid"]}, indent=2), "```", "",
             "## Train top 5 (if these are close, the choice is noise)", ""]
    lines += [f"- ROI {_fmt(t['flat_roi'], '+.1%')} on {t['graded']}: {t['config']}" for t in result["train_top5"]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay captured slates to test Upgrade B gates.")
    parser.add_argument("mode", choices=("ablation", "walk-forward"))
    parser.add_argument("--captures", nargs="+", required=True, help="nfl_best_bets_inputs.json.gz paths or globs.")
    parser.add_argument("--all-captures", action="store_true",
                        help="Replay every capture, not only the last one per slate.")
    add_event_args(parser)
    parser.add_argument("--close-feed", type=Path, action="append", default=[],
                        help="Book-close feed for CLV (repeatable).")
    parser.add_argument("--split-date", help="walk-forward: slates before this Eastern date train, the rest test.")
    parser.add_argument("--grid", choices=sorted(GRIDS), default="small")
    parser.add_argument("--min-train-picks", type=int, default=30)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    slates = load_slates(args.captures, latest_per_slate=not args.all_captures)
    if not slates:
        raise SystemExit("no captures matched --captures")
    close_index: dict[tuple[Any, ...], Any] | None = None
    for feed in args.close_feed:
        close_index = {**(close_index or {}), **load_book_close_feed(feed)}
    cache = OutcomeCache(events_from_args(args), close_index)
    if args.mode == "ablation":
        result = run_ablation(slates, cache)
    else:
        if not args.split_date:
            raise SystemExit("walk-forward needs --split-date")
        result = run_walk_forward(slates, cache, split_date=args.split_date, grid=GRIDS[args.grid],
                                  min_train_picks=args.min_train_picks)
    md = render_markdown(result)
    code = write_outputs([(args.out_json, json.dumps(result, indent=2, default=str)), (args.out_md, md)],
                         overwrite=args.overwrite)
    if code == 0 and args.out_md is None:
        print(md)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
