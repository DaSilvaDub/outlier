"""F12 (#228): a close row must not cross a known game or date boundary."""

from __future__ import annotations

from outlier_nfl.enrich_close import (
    index_book_close_records,
    lookup_book_close_row,
    owners_conflict,
    row_owner,
)
from outlier_nfl.fetch_odds_close import build_close_feed_from_event_odds

KICKOFF = "2026-09-13T17:00:00Z"


def _event(commence: str = KICKOFF) -> dict:
    return {
        "id": "oddsapi-bal-kc", "commence_time": commence,
        "home_team": "Kansas City Chiefs", "away_team": "Baltimore Ravens",
        "bookmakers": [{"key": "draftkings", "last_update": "2026-09-13T16:55:00Z", "markets": [
            {"key": "player_pass_yds", "last_update": "2026-09-13T16:55:00Z", "outcomes": [
                {"name": "Over", "description": "Patrick Mahomes", "price": -110,
                 "point": 250.5}]}]}],
    }


def _pred(matchup: str, starts: str, event_id: str, team: str = "KC", **kw) -> dict:
    return {"player_name": "Patrick Mahomes", "market": "PASS_YDS", "line": 250.5,
            "position": "OVER", "matchup": matchup, "event_id": event_id,
            "event_starts_at": starts, "team": team, **kw}


SAME_GAME = _pred("BAL @ KC", "2026-09-13T13:00:00-04:00", "outlier-bal-kc")
OTHER_GAME = _pred("LAR @ SF", "2026-09-13T16:05:00-04:00", "outlier-lar-sf", team="SF")
OTHER_WEEK = _pred("BAL @ KC", "2026-09-20T13:00:00-04:00", "outlier-bal-kc-w2")


def _index():
    return index_book_close_records(build_close_feed_from_event_odds(_event()))


def test_single_incompatible_close_is_not_attached_or_aligned():
    assert lookup_book_close_row(_index(), OTHER_GAME) is None
    row = build_close_feed_from_event_odds(_event(), predictions={"records": [OTHER_GAME]})[0]
    assert row["aligned_to_predictions"] is False
    assert row["alignment_skipped"] == "incompatible_owner"
    assert row["event_id"] == "oddsapi-bal-kc"


def test_same_player_in_a_different_week_is_not_attached():
    assert lookup_book_close_row(_index(), OTHER_WEEK) is None
    row = build_close_feed_from_event_odds(_event(), predictions={"records": [OTHER_WEEK]})[0]
    assert row["alignment_skipped"] == "incompatible_owner"


def test_different_provider_ids_for_the_same_game_still_join():
    hit = lookup_book_close_row(_index(), SAME_GAME)
    assert hit is not None and hit["close_odds"] == -110
    row = build_close_feed_from_event_odds(_event(), predictions={"records": [SAME_GAME]})[0]
    assert row["aligned_to_predictions"] is True
    assert row["event_id"] == "outlier-bal-kc"


def test_two_same_name_players_stay_ambiguous():
    a = {**SAME_GAME, "player_id": "p-1"}
    b = {**SAME_GAME, "team": "BAL", "player_id": "p-2"}
    row = build_close_feed_from_event_odds(_event(), predictions={"records": [a, b]})[0]
    assert row["aligned_to_predictions"] is False
    assert row["alignment_skipped"] == "ambiguous_short_key"


def test_owner_reads_full_names_codes_and_et_dates():
    assert row_owner({"matchup": "Baltimore Ravens @ Kansas City Chiefs"})[0] == {"BAL", "KC"}
    assert row_owner({"home_team": "KC", "away_team": "BAL"})[0] == {"BAL", "KC"}
    # 00:20Z on the 15th is still Monday night the 14th in New York.
    assert row_owner({"commence_time": "2026-09-15T00:20:00Z"})[1] == "2026-09-14"
    assert row_owner({"matchup": "TBD"}) == (None, None)


def test_unknown_owner_is_not_a_conflict():
    assert not owners_conflict({"matchup": "BAL @ KC"}, {"player_name": "x"})
    assert owners_conflict({"matchup": "BAL @ KC"}, {"matchup": "SF @ LAR"})
