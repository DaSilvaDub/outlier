import json
from pathlib import Path

from outlier_scrapers.normalizer import normalize_player_props
from outlier_scrapers.registry import get_sport_config


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_mlb_unknown_market_is_preserved_with_raw_fields():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    mystery = next(row for row in rows if row["market_raw"] == "Mystery Barrel Prop")
    assert mystery["market"] is None
    assert mystery["team"] == "NYY"
    assert mystery["team_raw"] == "NYY"
    assert mystery["opponent"] == "BOS"
    assert mystery["opp_rank"] is None
    assert mystery["opp_rank_signal"] == "not_applicable"


def test_mlb_doubleheader_dedupe_keeps_same_player_line_in_different_events():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    assert len(rows) == 2
    assert {row["event_id"] for row in rows} == {"mlb-event-1", "mlb-event-2"}


def test_missing_market_id_uses_market_raw_identity_without_collapsing():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    row = next(row for row in rows if row["market_id"] is None)
    assert row["market_raw"] == "Mystery Barrel Prop"


def test_unknown_team_yields_none_team_but_preserves_team_raw():
    schedule = {
        "events": [
            {
                "eventId": "evt-x",
                "away": {"alias": "ZZZ", "teamId": "1"},
                "home": {"alias": "QQQ", "teamId": "2"},
            }
        ]
    }
    props = {
        "props": [
            {
                "outcome": {
                    "eventId": "evt-x",
                    "teamId": "1",
                    "position": "OVER",
                    "line": 1.5,
                    "marketLabel": "Some Guy - Total Bases",
                    "proposition": "TOTAL_BASES",
                    "marketId": "m-x",
                    "bestOdds": -110,
                    "books": [],
                    "bookOdds": {},
                },
                "stats": {},
            }
        ]
    }
    rows = normalize_player_props(props, schedule, get_sport_config("MLB"))
    assert len(rows) == 1
    row = rows[0]
    assert row["team"] is None
    assert row["team_raw"] == "ZZZ"
    assert row["opponent"] is None
    assert row["opponent_raw"] == "QQQ"


def test_unicode_minus_odds_parse_to_int_best_odds():
    schedule = {"events": [{"eventId": "evt-u", "away": {"alias": "NYY", "teamId": "1"},
        "home": {"alias": "BOS", "teamId": "2"}}]}
    props = {"props": [{"outcome": {"eventId": "evt-u", "teamId": "1",
        "position": "OVER", "line": 1.5, "marketLabel": "Guy - Total Bases",
        "proposition": "TOTAL_BASES", "marketId": "m-u",
        "bestOdds": "−120", "books": [], "bookOdds": {}}, "stats": {}}]}
    rows = normalize_player_props(props, schedule, get_sport_config("MLB"))
    assert rows[0]["best_odds"] == -120


def test_wnba_basketball_market_normalizes_and_opp_rank_not_applicable():
    rows = normalize_player_props(
        load_fixture("wnba_player_props.json"),
        load_fixture("wnba_schedule.json"),
        get_sport_config("WNBA"),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["market"] == "PTS"
    assert row["team"] == "LVA"
    assert row["opponent"] == "NYL"
    # The playerProps API carries no opponent rank, so V1 reports not_applicable.
    assert row["opp_rank"] is None
    assert row["opp_rank_signal"] == "not_applicable"


def _mlb_one_event_schedule():
    return {
        "events": [
            {
                "eventId": "e1",
                "away": {"alias": "NYY", "teamId": "10"},
                "home": {"alias": "BOS", "teamId": "20"},
            }
        ]
    }


def _mlb_prop(market_id, label, *, event_id="e1", proposition="HITS", line=1.5):
    return {
        "outcome": {
            "eventId": event_id,
            "teamId": "10",
            "oppTeamId": "20",
            "position": "OVER",
            "line": line,
            "marketLabel": label,
            "proposition": proposition,
            "marketId": market_id,
            "bestOdds": -110,
            "books": [],
            "bookOdds": {},
        },
        "stats": {},
    }


def test_full_game_maps_and_scoped_variant_preserved_not_canonicalized():
    payload = {
        "props": [
            _mlb_prop("m1", "Aaron Judge - Hits"),
            _mlb_prop("m2", "Aaron Judge - 1st Inning Hits"),
        ]
    }
    rows = normalize_player_props(payload, _mlb_one_event_schedule(), get_sport_config("MLB"))
    by_scope = {r["sport_context"]["scope"]: r for r in rows}

    assert by_scope["full_game"]["market"] == "H"
    assert by_scope["full_game"]["market_raw"] == "Hits"

    scoped = by_scope["first_inning"]
    assert scoped["market"] is None  # not canonicalized
    assert scoped["market_raw"] == "1st Inning Hits"  # preserved
    assert scoped["sport_context"]["proposition"] == "HITS"


def test_teamid_fallback_fills_team_when_event_not_in_schedule():
    # Prop references an event that is not in the schedule index, but its
    # teamId/oppTeamId appear elsewhere in the schedule.
    payload = {"props": [_mlb_prop("mz", "Some Guy - Hits", event_id="e9", line=0.5)]}
    rows = normalize_player_props(payload, _mlb_one_event_schedule(), get_sport_config("MLB"))
    assert len(rows) == 1
    row = rows[0]
    assert row["team"] == "NYY"
    assert row["opponent"] == "BOS"
    assert row["event_id"] == "e9"  # row preserved despite missing schedule entry
    assert row["matchup"] is None  # no event-specific matchup available

