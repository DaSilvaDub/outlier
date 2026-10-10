"""F29 (#224): the slate's season phase is explicit; unsupported phases never publish."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from outlier_nfl.season_phase import event_season_type, season_phase_receipt, slate_season_type
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"week": 1}, "REG"),
        ({"week": 18}, "REG"),
        ({"week": 19}, "POST"),
        ({"week": 22}, "POST"),
        ({"week": 18, "seasonType": "POST"}, "POST"),  # the explicit type wins over the week
        ({"gameType": "WC"}, "POST"),
        ({"gameType": "DIV"}, "POST"),
        ({"game_type": "CON"}, "POST"),
        ({"game_type": "SB"}, "POST"),
        ({"season_type": "Regular"}, "REG"),
        ({"seasonType": "PRE", "week": 2}, "PRE"),
        ({}, None),
        ({"week": "n/a"}, None),
    ],
)
def test_event_season_type(event: dict[str, Any], expected: str | None) -> None:
    assert event_season_type(event) == expected


def test_slate_season_type_mixed_and_empty() -> None:
    assert slate_season_type([]) is None
    assert slate_season_type([{"week": 18}, {"week": 19}]) == "MIXED"


@pytest.mark.parametrize(
    ("events", "season", "status"),
    [
        ([{"week": 4}], 2026, "OK"),
        ([{"week": 18}], 2026, "OK"),  # January regular season
        ([{"week": 19}], 2026, "UNSUPPORTED"),
        ([{"gameType": "SB"}], 2026, "UNSUPPORTED"),
        ([{"seasonType": "PRE"}], 2026, "UNSUPPORTED"),
        ([{}], 2026, "UNSUPPORTED"),  # the schedule states no phase
        ([{"week": 4}, {"week": 19}], 2026, "UNSUPPORTED"),
        ([{"week": 4}], 2024, "UNSUPPORTED"),  # legacy depth-chart schema
        ([], 2026, "EMPTY"),
    ],
)
def test_season_phase_receipt(events: list[dict[str, Any]], season: int, status: str) -> None:
    r = season_phase_receipt(events, season)
    assert r.required and r.status == status


def _run(tmp_path: Path, client: Any, day: str, clock: str) -> dict[str, Any]:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    return NflPipeline(client=client, data_dir=tmp_path, clock=lambda: now).run(
        date=day, as_of_utc=clock, reports_dir=tmp_path / "reports"
    )


def _published(tmp_path: Path) -> dict[str, bytes]:
    root = tmp_path / "NFL" / "normalized"
    return {p.name: p.read_bytes() for p in sorted(root.glob("*.json"))}


def test_postseason_slate_is_rejected_before_publication(tmp_path: Path) -> None:
    good = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), "2026-10-04",
                "2026-10-04T15:00:00+00:00")
    assert good["status"] == "OK" and good["season_type"] == "REG"
    before = _published(tmp_path)
    post = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, week=19), "2026-10-04",
                "2026-10-04T16:00:00+00:00")
    assert post["status"] == "FAILED" and post["season_type"] == "POST"
    phase = next(r for r in post["stage_receipts"] if r["name"] == "season_phase")
    assert phase["status"] == "UNSUPPORTED" and "3 POST" in phase["reason"]
    assert post["publication"] == "bundle_only"
    assert post["publication_reason"] == "required_stage_not_ok: season_phase=UNSUPPORTED"
    assert _published(tmp_path) == before


def test_january_regular_season_slate_publishes(tmp_path: Path) -> None:
    # 2026-09-13 + 119 days = Sunday 2027-01-10, Week 18 of the 2026 season.
    client = snap.FrozenOutlierClient(FIXTURES_DIR, shift_days=119, week=18)
    run = _run(tmp_path, client, "2027-01-10", "2027-01-10T15:00:00+00:00")
    assert run["season_type"] == "REG"
    phase = next(r for r in run["stage_receipts"] if r["name"] == "season_phase")
    assert phase["status"] == "OK"
    assert run["status"] == "OK" and run["publication"] == "published"
