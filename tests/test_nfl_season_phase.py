"""F29 (#224): the slate's season phase is explicit; unsupported phases never publish."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl.external import ExternalLoad
from outlier_nfl.matchup import _event_team_codes
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


@pytest.mark.parametrize("matched", [True, False])
def test_missing_type_and_week_uses_loaded_schedule_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matched: bool,
) -> None:
    client = snap.FrozenOutlierClient(FIXTURES_DIR)
    rows = []
    for event in client._schedule["events"]:
        event.pop("week")
        assert not any(k in event for k in ("seasonType", "season_type", "gameType", "game_type"))
        home, away = _event_team_codes(event)
        rows.append({
            "source": "schedule", "season": 2026, "game_type": "REG", "week": 4,
            "gameday": "2026-10-04" if matched else "2026-10-11",
            "home_team": home, "away_team": away,
        })
    calls = []

    def load(*args: Any, **kwargs: Any) -> ExternalLoad:
        calls.append((args, kwargs))
        return ExternalLoad(phase_records=rows, before_week=4)

    monkeypatch.setattr("outlier_nfl.pipeline.load_external_metrics", load)
    run = _run(tmp_path, client, "2026-10-04", "2026-10-04T15:00:00+00:00")
    assert len(calls) == 1  # reuse F01's load, including its verified cutoff
    assert calls[0][1]["as_of_utc"] == parse_utc("2026-10-04T15:00:00+00:00")
    phase = next(r for r in run["stage_receipts"] if r["name"] == "season_phase")
    assert run["season_type"] == ("REG" if matched else None)
    assert phase["status"] == ("OK" if matched else "UNSUPPORTED")
    assert run["publication"] == ("published" if matched else "bundle_only")
    assert run["status"] == ("OK" if matched else "FAILED")
    assert bool(_published(tmp_path)) is matched


@pytest.mark.parametrize("game_type, expected", [("REG", "REG"), ("WC", "POST"),
                                                ("PRE", "PRE"), (None, None)])
def test_schedule_fallback_matches_eastern_kickoff_date_and_team_aliases(
    game_type: str | None, expected: str | None,
) -> None:
    # Monday UTC is still Sunday Eastern; nflverse LA is Outlier LAR.
    event = {"scheduledTime": "2026-10-05T00:20:00Z",
             "home": {"name": "Los Angeles Rams"}, "away": {"alias": "SF"}}
    rows = [{"gameday": "2026-10-04", "home_team": "LA", "away_team": "SF",
             "game_type": game_type}]
    assert event_season_type(event, rows) == expected
    assert slate_season_type([event], rows) == expected
    assert season_phase_receipt([event], 2026, rows).status == (
        "OK" if expected == "REG" else "UNSUPPORTED"
    )


@pytest.mark.parametrize("change", [
    {"gameday": "2026-10-05"}, {"away_team": "KC"}, {"game_type": ""}, {"season": 2025},
])
def test_schedule_fallback_refuses_unmatched_or_typeless_rows(change: dict[str, str]) -> None:
    event = {"startTime": "2026-10-05T00:20:00Z", "home_team": "LAR", "away_team": "SF"}
    event["season"] = 2026
    row = {"gameday": "2026-10-04", "home_team": "LA", "away_team": "SF", "game_type": "REG",
           "season": 2026}
    row.update(change)
    assert event_season_type(event, [row]) is None
    assert season_phase_receipt([event], 2026, [row]).status == "UNSUPPORTED"


def test_schedule_fallback_does_not_override_event_phase_or_accept_conflicting_matches() -> None:
    event = {"scheduledTime": "2026-10-04T17:00:00Z", "home_team": "KC", "away_team": "BAL"}
    row = {"gameday": "2026-10-04", "home_team": "KC", "away_team": "BAL", "game_type": "REG"}
    assert event_season_type({**event, "week": 19}, [row]) == "POST"
    assert event_season_type({**event, "seasonType": "PRE"}, [row]) == "PRE"
    assert event_season_type(event, [row, {**row, "game_type": "POST"}]) is None
    assert event_season_type(event, []) is None
    assert event_season_type({**event, "scheduledTime": "invalid"}, [row]) is None
    assert event_season_type({"scheduledTime": event["scheduledTime"]}, [row]) is None


def test_schedule_fallback_matches_neutral_site_game_with_swapped_home_away() -> None:
    # London game: Outlier lists JAX at home, nflverse lists BUF at home.
    event = {"scheduledTime": "2026-10-11T13:30:00Z", "season": 2026,
             "home_team": "JAX", "away_team": "BUF"}
    row = {"gameday": "2026-10-11", "season": 2026, "home_team": "BUF", "away_team": "JAX",
           "game_type": "REG"}
    assert event_season_type(event, [row]) == "REG"
    assert season_phase_receipt([event], 2026, [row]).status == "OK"


def test_schedule_fallback_rejects_more_than_one_matching_row() -> None:
    event = {"scheduledTime": "2026-10-11T13:30:00Z", "home_team": "JAX", "away_team": "BUF"}
    row = {"gameday": "2026-10-11", "home_team": "BUF", "away_team": "JAX", "game_type": "REG"}
    swapped = {**row, "home_team": "JAX", "away_team": "BUF"}
    # Two rows for the same pair and day (same type, either order) is ambiguous.
    assert event_season_type(event, [row, swapped]) is None
    assert event_season_type(event, [row, dict(row)]) is None
    assert season_phase_receipt([event], 2026, [row, swapped]).status == "UNSUPPORTED"
