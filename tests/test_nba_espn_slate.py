"""ESPN slate feed: schedule, DraftKings lines and injury listings parsed from fixtures."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from outlier_nba.ingest import espn, espn_slate

FIX = Path(__file__).parent / "fixtures" / "nba"
NOW = datetime(2026, 11, 26, 0, 20, tzinfo=UTC)  # 19:20 ET, ten minutes before the 19:30 tip


def _json(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _games():
    games, skipped = espn_slate.parse_slate(_json("espn_slate_scoreboard.json"))
    return {g.event_id: g for g in games}, skipped


def test_scheduled_game_carries_canonical_teams_tip_and_draftkings_lines() -> None:
    games, skipped = _games()
    g = games["401909319"]
    assert (g.away, g.home) == ("CLE", "NYK")  # ESPN's "NY" resolves to the registry tricode
    assert g.tip == datetime(2026, 11, 26, 0, 30, tzinfo=UTC)
    assert g.scheduled and not g.in_progress
    assert skipped == []
    lines = g.lines
    assert lines is not None and lines.book == "DraftKings"
    assert (lines.sp_home_line, lines.sp_home_odds, lines.sp_away_odds) == (-4.5, -107, -113)
    assert (lines.tot_line, lines.tot_over_odds, lines.tot_under_odds) == (221.5, -105, -116)
    assert (lines.ml_home, lines.ml_away) == (-180, 151)


def test_in_progress_game_without_odds_has_no_lines() -> None:
    games, _ = _games()
    g = games["401909317"]
    assert g.in_progress and not g.scheduled
    assert g.lines is None


def test_an_event_with_an_unresolvable_team_is_skipped_and_reported() -> None:
    payload = copy.deepcopy(_json("espn_slate_scoreboard.json"))
    payload["events"][0]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "ZZZ"
    games, skipped = espn_slate.parse_slate(payload)
    assert [g.event_id for g in games] == [payload["events"][1]["id"]]
    assert skipped == [payload["events"][0]["id"]]


def test_malformed_odds_yield_no_lines_instead_of_a_partial_set() -> None:
    payload = copy.deepcopy(_json("espn_slate_scoreboard.json"))
    odds = payload["events"][1]["competitions"][0]["odds"][0]
    del odds["total"]["under"]
    games, _ = espn_slate.parse_slate(payload)
    assert {g.event_id: g for g in games}["401909319"].lines is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+151", 151), ("-107", -107), ("EVEN", 100), ("even", 100), (" -110 ", -110)],
)
def test_american_prices_parse_including_even(raw, expected) -> None:
    assert espn_slate.parse_american(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "OFF", "+50", "-99", "abc", 0])
def test_unusable_american_prices_are_none(raw) -> None:
    assert espn_slate.parse_american(raw) is None


def test_injuries_map_to_canonical_teams_and_flag_fresh_news() -> None:
    games, _ = _games()
    names = espn_slate.team_names(_json("espn_slate_scoreboard.json"))
    assert names["New York Knicks"] == "NYK"
    inj = espn_slate.parse_injuries(_json("espn_slate_injuries.json"), names)
    assert {"NYK", "CLE", "PHI"} <= set(inj)
    nyk = {n.player: n for n in inj["NYK"]}
    anunoby = nyk["OG Anunoby"]
    assert anunoby.status == "Out"
    assert anunoby.reported == datetime(2026, 11, 25, 23, 19, tzinfo=UTC)
    assert anunoby.is_fresh(NOW, hours=48)
    old = [n for n in inj["CLE"] if n.player == "Evan Mobley"][0]
    assert not old.is_fresh(NOW, hours=48)


def test_freshness_requires_a_parseable_date_and_a_blocking_status() -> None:
    note = espn_slate.InjuryNote("A", "Out", None, "")
    assert not note.is_fresh(NOW, hours=48)  # unknown date is not treated as fresh news
    recent = NOW - timedelta(hours=1)
    assert espn_slate.InjuryNote("A", "Questionable", recent, "").is_fresh(NOW, hours=48)
    assert not espn_slate.InjuryNote("A", "Available", recent, "").is_fresh(NOW, hours=48)


def test_teams_outside_the_slate_are_ignored_in_the_injury_feed() -> None:
    inj = espn_slate.parse_injuries(_json("espn_slate_injuries.json"), {"Miami Heat": "MIA"})
    assert inj == {}


def test_fetch_uses_the_espn_endpoints_for_the_slate_day() -> None:
    seen: list[str] = []

    def fake(url: str) -> dict:
        seen.append(url)
        return {"events": []} if "scoreboard" in url else {"injuries": []}

    board, injuries = espn_slate.fetch_feeds("2026-11-25", fetch=fake)
    assert seen == [
        f"{espn.ESPN_BASE}/scoreboard?dates=20261125",
        f"{espn.ESPN_BASE}/injuries",
    ]
    assert board == {"events": []} and injuries == {"injuries": []}


def test_fetch_rejects_a_malformed_slate_date() -> None:
    with pytest.raises(ValueError):
        espn_slate.fetch_feeds("11/25/2026", fetch=lambda url: {})


def test_the_neutral_site_flag_is_parsed_strictly() -> None:
    games, _ = _games()
    assert games["401909319"].neutral is False
    payload = copy.deepcopy(_json("espn_slate_scoreboard.json"))
    payload["events"][1]["competitions"][0]["neutralSite"] = True
    payload["events"][0]["competitions"][0]["neutralSite"] = "maybe"
    parsed, _ = espn_slate.parse_slate(payload)
    by_id = {g.event_id: g for g in parsed}
    assert by_id["401909319"].neutral is True
    assert by_id["401909317"].neutral is None  # unrecognised value: unknown, never assumed


def test_a_missing_neutral_flag_is_unknown() -> None:
    payload = copy.deepcopy(_json("espn_slate_scoreboard.json"))
    del payload["events"][0]["competitions"][0]["neutralSite"]
    parsed, _ = espn_slate.parse_slate(payload)
    assert {g.event_id: g for g in parsed}["401909317"].neutral is None


def test_a_pick_em_spread_parses_as_zero() -> None:
    payload = copy.deepcopy(_json("espn_slate_scoreboard.json"))
    odds = payload["events"][1]["competitions"][0]["odds"][0]
    odds["pointSpread"]["home"]["close"]["line"] = "PK"
    games, _ = espn_slate.parse_slate(payload)
    lines = {g.event_id: g for g in games}["401909319"].lines
    assert lines is not None and lines.sp_home_line == 0.0


def test_an_empty_or_missing_injury_feed_is_not_usable() -> None:
    assert espn_slate.injury_feed_usable(_json("espn_slate_injuries.json")) is True
    assert espn_slate.injury_feed_usable({"injuries": []}) is False
    assert espn_slate.injury_feed_usable({}) is False
    assert espn_slate.injury_feed_usable({"injuries": [{"displayName": "X"}]}) is False
