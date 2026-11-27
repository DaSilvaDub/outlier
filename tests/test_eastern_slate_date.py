"""Slate dates are America/New_York, not the host clock.

A 10:10pm Eastern first pitch is 03:10 UTC the next civil day. On a UTC or
other non-Eastern host, ``datetime.now().astimezone()`` and aware timestamps
converted with bare ``astimezone()`` put that game on the wrong slate.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timezone

import pytest

LATE_UTC = datetime(2026, 11, 28, 3, 10, tzinfo=timezone.utc)
LATE_ISO = "2026-11-28T03:10:00+00:00"
NEXT_AFTERNOON_ISO = "2026-11-28T18:00:00+00:00"  # 1:00pm EST, next slate
EASTERN_SLATE = "2026-11-27"


@pytest.fixture
def utc_host():
    """Force the process zone to UTC so the host clock is not Eastern."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if hasattr(time, "tzset"):
            time.tzset()


def _freeze_now(monkeypatch: pytest.MonkeyPatch, module, frozen: datetime) -> None:
    real = module.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen.astimezone().replace(tzinfo=None)
            return frozen.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Frozen)


def test_late_utc_timestamp_is_previous_eastern_slate(utc_host):
    from outlier_scrapers.utils import _local_date

    assert _local_date(LATE_ISO) == EASTERN_SLATE
    assert _local_date(LATE_UTC) == EASTERN_SLATE
    assert _local_date("2026-11-28T03:10:00Z") == EASTERN_SLATE
    # 22:10 EST written with an offset stays on that slate.
    assert _local_date("2026-11-27T22:10:00-05:00") == EASTERN_SLATE
    # 04:30 UTC is 00:30 EDT (same day) and 23:30 EST (previous day).
    assert _local_date("2026-07-15T04:30:00+00:00") == "2026-07-15"
    assert _local_date("2026-11-28T04:30:00+00:00") == EASTERN_SLATE
    assert _local_date("2099-01-01T12:00:00") == "2099-01-01"
    assert _local_date(None) is None
    assert _local_date("") is None


def test_select_date_keeps_late_game_on_eastern_today(utc_host, monkeypatch):
    from outlier_scrapers import pack

    _freeze_now(monkeypatch, pack, LATE_UTC)
    rows = [
        {"_event_starts_at": LATE_ISO, "market_id": "late"},
        {"_event_starts_at": NEXT_AFTERNOON_ISO, "market_id": "next"},
        {"_event_starts_at": None, "market_id": "undated"},
    ]
    kept, target = pack.select_date(rows, None)
    assert target == EASTERN_SLATE
    assert {row["market_id"] for row in kept} == {"late", "undated"}
    assert pack._expected_pack_slate_date([], None) == EASTERN_SLATE


def test_games_default_window_uses_eastern_slate(utc_host, monkeypatch, tmp_path):
    from outlier_scrapers import games, paths as paths_mod

    _freeze_now(monkeypatch, games, LATE_UTC)
    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    event = {
        "status": "scheduled",
        "scheduledTime": LATE_ISO,
    }
    assert games._event_local_date(event) == date.fromisoformat(EASTERN_SLATE)
    assert games._is_event_in_window(event, date.fromisoformat(EASTERN_SLATE), 1, False)
    assert not games._is_event_in_window(event, date(2026, 11, 28), 1, False)

    class LateClient:
        def fetch_schedule(self, league_id):
            return {
                "events": [
                    {
                        "id": "late",
                        "eventId": "late",
                        "status": "scheduled",
                        "scheduledTime": LATE_ISO,
                        "home": {"id": "h1", "teamId": "h1", "name": "Yankees"},
                        "away": {"id": "a1", "teamId": "a1", "name": "Red Sox"},
                    }
                ]
            }

        def fetch_event_matchup(self, event_id):
            return {}

        def fetch_event_insights(self, event_id):
            return {"insights": []}

        def fetch_event_markets(self, event_id, market_type):
            return {"markets": []}

        def fetch_team_injuries(self, league_id, team_id):
            return {"players": []}

    status = games.export_games_for_league(LateClient(), "MLB")
    assert status["target_date"] == EASTERN_SLATE
    assert status["target_event_count"] == 1


def test_cards_active_slate_is_eastern_today(utc_host, monkeypatch, tmp_path):
    from outlier_scrapers import cards
    from outlier_scrapers.paths import LeaguePaths

    _freeze_now(monkeypatch, cards, LATE_UTC)
    root = tmp_path / "WNBA"
    (root / "reports").mkdir(parents=True)
    monkeypatch.setattr(
        cards,
        "league_paths",
        lambda lg: LeaguePaths(
            league=lg,
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        ),
    )

    def assemble(mid, _idx):
        start = LATE_ISO if mid == "late" else NEXT_AFTERNOON_ISO
        return {"card_id": mid, "board": "B", "event_starts_at": start, "rank_value": 1.0}

    def load_latest(_league, feed):
        if feed == "props":
            return {"records": [{"market_id": "late"}, {"market_id": "next"}]}
        if feed == "line_movement":
            return {"records": [], "ev_records": []}
        if feed == "games":
            return {"records": [{"market_id": "late"}, {"market_id": "next"}]}
        return None

    monkeypatch.setattr(cards, "load_latest", load_latest)
    monkeypatch.setattr(cards, "assemble_card", assemble)
    monkeypatch.setattr(cards, "assemble_game_card", assemble)

    player = cards.build_cards_payload("WNBA")
    games = cards.build_game_cards_payload("WNBA")
    assert player["slate_date"] == EASTERN_SLATE
    assert games["slate_date"] == EASTERN_SLATE
    for payload in (player, games):
        by_id = {card["card_id"]: card for card in payload["board_b"]}
        assert "off_slate" not in by_id["late"].get("flags", [])
        assert by_id["next"].get("off_slate") is True


def test_empty_slate_status_uses_eastern_day(utc_host, tmp_path, monkeypatch):
    from outlier_scrapers import feed_health
    from outlier_scrapers.paths import LeaguePaths

    root = tmp_path / "MLB"
    paths = LeaguePaths(
        league="MLB",
        root=root,
        raw=root / "raw",
        normalized=root / "normalized",
        reports=root / "reports",
    )
    monkeypatch.setattr(feed_health, "league_paths", lambda _league: paths)
    status_path = paths.reports / "games_status_latest.json"
    status_path.parent.mkdir(parents=True)
    status = {
        "league": "MLB",
        "status": "ok",
        "generated_at": LATE_UTC.isoformat(),
        "record_count": 0,
        "target_date": EASTERN_SLATE,
        "target_event_count": 0,
        "preserved_previous_latest": True,
    }
    status_path.write_text(json.dumps(status), encoding="utf-8")
    assert feed_health.league_has_confirmed_empty_slate("MLB", now=LATE_UTC) is True

    status["target_date"] = "2026-11-28"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    assert feed_health.league_has_confirmed_empty_slate("MLB", now=LATE_UTC) is False
