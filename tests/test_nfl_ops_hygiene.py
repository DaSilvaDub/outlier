"""Ops hygiene: unknown --window fail-closed, no *_latest clobber, placeholder provenance."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from outlier_nfl.matchup import MatchupScript, _market_context, render_matchup_markdown
from outlier_nfl.models import NflGameLine
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.utils import matches_kickoff_window, normalize_kickoff_window

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
ET = ZoneInfo("America/New_York")


@pytest.fixture
def mock_api_client():
    client = MagicMock()
    with open(FIXTURES_DIR / "schedule.json", "r", encoding="utf-8") as f:
        sched_data = json.load(f)
    with open(FIXTURES_DIR / "event_markets.json", "r", encoding="utf-8") as f:
        mkts_data = json.load(f)
    with open(FIXTURES_DIR / "player_props.json", "r", encoding="utf-8") as f:
        props_data = json.load(f)
    client.fetch_schedule.return_value = sched_data
    client.fetch_event_markets.return_value = mkts_data
    client.fetch_player_props.return_value = props_data
    return client


def test_normalize_kickoff_window_known_and_clock():
    assert normalize_kickoff_window(None) is None
    assert normalize_kickoff_window(" 1PM ") == "1pm"
    assert normalize_kickoff_window("19:30") == "19:30"


def test_normalize_kickoff_window_unknown_raises():
    with pytest.raises(ValueError, match="Unknown kickoff window"):
        normalize_kickoff_window("typo-window")


def test_matches_kickoff_window_unknown_fails_closed():
    kick = datetime(2026, 9, 27, 13, 0, tzinfo=ET)
    assert matches_kickoff_window(kick, "typo-window") is False
    assert matches_kickoff_window(kick, "1pm") is True


def test_pipeline_window_skips_latest_writes(tmp_path: Path, mock_api_client):
    pipeline = NflPipeline(client=mock_api_client, data_dir=tmp_path)
    norm = tmp_path / "NFL" / "normalized"
    norm.mkdir(parents=True)
    sentinel = {"date": "SENTINEL", "records": []}
    (norm / "nfl_games_latest.json").write_text(json.dumps(sentinel), encoding="utf-8")
    (norm / "nfl_props_latest.json").write_text(json.dumps(sentinel), encoding="utf-8")

    summary = pipeline.run(
        date="2026-09-13",
        window="1pm",
        offline_fixtures_dir=FIXTURES_DIR,
    )
    assert summary["status"] == "OK"
    assert summary.get("window") == "1pm"

    games_latest = json.loads((norm / "nfl_games_latest.json").read_text(encoding="utf-8"))
    assert games_latest["date"] == "SENTINEL"
    assert (norm / "nfl_games_2026-09-13_1pm.json").exists()


def test_pipeline_unknown_window_raises(tmp_path: Path, mock_api_client):
    pipeline = NflPipeline(client=mock_api_client, data_dir=tmp_path)
    with pytest.raises(ValueError, match="Unknown kickoff window"):
        pipeline.run(
            date="2026-09-13",
            window="not-a-window",
            offline_fixtures_dir=FIXTURES_DIR,
        )


def test_market_context_marks_default_placeholders():
    ctx = _market_context([], home="KC", away="BUF")
    assert ctx["total"] == 45.5
    assert ctx["total_source"] == "default"
    assert ctx["home_tt_source"] == "default"
    assert ctx["away_tt_source"] == "default"


def test_market_context_marks_quoted_total_as_market():
    line = NflGameLine(
        event_id="e1",
        event_starts_at=None,
        matchup="BUF @ KC",
        home_team="KC",
        away_team="BUF",
        market_type="GAMELINE",
        market="TOTAL",
        proposition="TOTAL",
        position="OVER",
        line=47.5,
        signed_line=None,
        selection="Over 47.5",
        team=None,
        books=(),
        best_odds=-110,
        implied_probability=None,
        scope="full_game",
    )
    ctx = _market_context([line], home="KC", away="BUF")
    assert ctx["total"] == 47.5
    assert ctx["total_source"] == "market"


def test_markdown_flags_placeholder_projected_score():
    script = MatchupScript(
        event_id="e1",
        matchup="BUF @ KC",
        home_team="KC",
        away_team="BUF",
        script_type="COMPETITIVE",
        spread_lean="HOME",
        total_lean="UNDER",
        home_score=24.0,
        away_score=21.0,
        home_spread=-3.0,
        total=45.5,
        mismatches=(),
        prop_signals=(),
        total_source="default",
        home_tt_source="default",
        away_tt_source="default",
    )
    md = render_matchup_markdown(script, date="2026-09-27")
    assert "PLACEHOLDER" in md
    assert "placeholder" in md.lower()
