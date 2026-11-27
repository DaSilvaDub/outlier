"""Gate backtest: replay captured slates, one-gate ablation, and walk-forward with no test tuning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_nfl_best_bets import _inputs, _prop

from outlier_nfl.best_bets import UPGRADE_B_GATES
from outlier_nfl.gate_backtest import (
    OutcomeCache,
    ablation_configs,
    load_slates,
    run_ablation,
    run_walk_forward,
)
from outlier_nfl.gate_backtest import (
    main as backtest_main,
)
from outlier_nfl.settle import load_boxscores
from outlier_nfl.snapshots import prop_key
from outlier_nfl.trace_capture import save_trace_inputs

# Two Sundays: A.J. Brown catches 7 (OVER 5.5 wins) on 10-04, then 4 (loses) on 10-11.
SLATES = (("2026-10-04", "E1", 7), ("2026-10-11", "E2", 4))


def _slate_inputs(day: str, event_id: str, *, l10: float, chase: bool):
    kickoff = f"{day}T17:00:00Z"
    over = _prop(event_id=event_id, event_starts_at=kickoff, l10_hit_rate=l10)
    under = _prop("UNDER", event_id=event_id, event_starts_at=kickoff)
    move = {"snapshots": 2, "open_taken_at": f"{day}T12:00:00Z", "last_taken_at": f"{day}T15:00:00Z",
            "open_line": 4.5 if chase else 5.5, "last_line": 5.5,
            "open_odds": -100, "open_implied": 50.0, "last_odds": -110, "last_implied": 52.4}
    inputs = _inputs(props=[over, under], run_date=day, movement={prop_key(over): move})
    inputs.scripts[0]["event_id"] = event_id
    inputs.weather[0]["event_id"] = event_id
    return inputs


def _write_slates(tmp_path: Path, *, l10: float = 0.8, chase: bool = False) -> Path:
    boxes = {"events": []}
    for day, event_id, catches in SLATES:
        inputs = _slate_inputs(day, event_id, l10=l10, chase=chase)
        run = tmp_path / "runs" / f"run-{day}"
        # An earlier capture of the same slate that the latest one must replace.
        save_trace_inputs(run.with_name(f"early-{day}") / "nfl_best_bets_inputs.json.gz", inputs,
                          meta={"date": day, "suffix": day, "updated_at": f"{day}T09:00:00Z"})
        save_trace_inputs(run / "nfl_best_bets_inputs.json.gz", inputs,
                          meta={"date": day, "suffix": day, "updated_at": f"{day}T15:30:00Z"})
        boxes["events"].append({"provider_event_id": f"box-{event_id}", "event_date": day,
                                "away": "DAL", "home": "PHI", "away_score": 20, "home_score": 24, "players": {"A.J. Brown": {"RECEIVING:REC": catches}}})
    box = tmp_path / "boxes.json"
    box.write_text(json.dumps(boxes), encoding="utf-8")
    return box


def test_load_slates_keeps_the_last_capture_per_slate(tmp_path):
    _write_slates(tmp_path)
    slates = load_slates([str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz")])
    assert [s.run_date for s in slates] == ["2026-10-04", "2026-10-11"]
    assert all(s.meta["updated_at"].endswith("15:30:00Z") for s in slates)
    every = load_slates([str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz")], latest_per_slate=False)
    assert len(every) == 4


def test_ablation_configs_cover_each_gate_both_ways():
    names = [n for n, _ in ablation_configs()]
    assert names[0] == "legacy" and names[-1] == "upgrade_b"
    for g in UPGRADE_B_GATES:
        assert f"only:{g}" in names and f"all_but:{g}" in names


def test_ablation_isolates_the_line_move_chase_gate(tmp_path):
    box = _write_slates(tmp_path, chase=True)
    slates = load_slates([str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz")])
    result = run_ablation(slates, OutcomeCache(load_boxscores(box)))
    arms = result["arms"]
    # Legacy chases both moved lines: one win at -110, one loss.
    assert (arms["legacy"]["wins"], arms["legacy"]["losses"]) == (1, 1)
    assert arms["legacy"]["flat_units"] == pytest.approx(100 / 110 - 1, abs=1e-3)
    assert arms["upgrade_b"]["graded"] == 0
    assert arms["only:line_move_chase"]["graded"] == 0
    assert arms["all_but:line_move_chase"]["graded"] == 2
    assert arms["only:odds_band"]["graded"] == 2  # irrelevant gate: same as legacy
    assert result["gate_verdicts"]["line_move_chase"] == "keep"
    assert result["gate_verdicts"]["odds_band"] == "drop_candidate"


def test_walk_forward_tunes_on_train_and_scores_test_once(tmp_path):
    box = _write_slates(tmp_path, l10=0.6)  # 60% L10: blocked by the merged 70% floor
    slates = load_slates([str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz")])
    result = run_walk_forward(slates, OutcomeCache(load_boxscores(box)), split_date="2026-10-10",
                              grid={"l10_min": (0.6, 0.7)}, min_train_picks=1)
    assert len(result["train_slates"]) == 1 and len(result["test_slates"]) == 1
    assert result["chosen"]["config"]["l10_min"] == 0.6   # only config with a train pick (a win)
    assert result["chosen"]["train"]["wins"] == 1
    assert result["chosen"]["test"]["losses"] == 1         # the held-out week disagrees
    assert result["upgrade_b_defaults"]["test"]["graded"] == 0
    assert result["legacy"]["test"]["graded"] == 1


def test_walk_forward_refuses_an_empty_side(tmp_path):
    box = _write_slates(tmp_path)
    slates = load_slates([str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz")])
    with pytest.raises(SystemExit, match="need both"):
        run_walk_forward(slates, OutcomeCache(load_boxscores(box)), split_date="2026-12-01",
                         grid={"l10_min": (0.7,)})


def test_cli_ablation_writes_reports(tmp_path):
    box = _write_slates(tmp_path, chase=True)
    out_md = tmp_path / "ablation.md"
    code = backtest_main(["ablation", "--captures", str(tmp_path / "runs" / "*" / "nfl_best_bets_inputs.json.gz"),
                          "--boxscores", str(box), "--out-md", str(out_md), "--out-json", str(tmp_path / "a.json")])
    assert code == 0
    md = out_md.read_text("utf-8")
    assert "- line_move_chase: **keep**" in md
    assert json.loads((tmp_path / "a.json").read_text("utf-8"))["mode"] == "ablation"
