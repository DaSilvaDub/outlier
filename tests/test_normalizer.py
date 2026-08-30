import json
from pathlib import Path

import pytest

from outlier_scrapers.normalizer import detect_scope, normalize_games, normalize_player_props
from outlier_scrapers.registry import get_sport_config


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_detect_scope_period_label_abbreviations():
    assert detect_scope("Total") == "full_game"
    assert detect_scope("Total", None) == "full_game"
    assert detect_scope("Hits", "6I") == "partial_period"
    assert detect_scope("F5") == "first_5_innings"
    assert detect_scope("1H") == "first_half"
    assert detect_scope("2Q") == "second_quarter"
    assert detect_scope("1st 7I") == "partial_period"
    assert detect_scope("7-9I") == "partial_period"


def test_normalize_player_props_writes_player_id_and_canonical_market_type():
    payload = load_fixture("mlb_player_props.json")
    payload["props"][0]["outcome"]["playerId"] = "judge-id"
    payload["props"][0]["outcome"]["player"] = {"id": "judge-id", "name": "Aaron Judge"}
    rows = normalize_player_props(
        payload,
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    so_rows = [row for row in rows if row.get("market") == "SO"]
    assert so_rows
    assert so_rows[0]["player_id"] == "judge-id"
    assert so_rows[0]["market_type"] == "SO"


def test_first_inning_game_props_are_not_dropped_by_normalizer():
    rows = normalize_games(
        config=get_sport_config("MLB"),
        schedule_payload=load_fixture("mlb_schedule.json"),
        events_payloads=[
            {
                "eventId": "mlb-event-1",
                "markets": [
                    {
                        "marketType": "GAME_PROP",
                        "proposition": "FIRST_INNING_RUN",
                        "label": "NRFI",
                        "periodLabel": "1st inning",
                        "outcomes": [
                            {"outcomeId": "yes", "position": "YES", "line": 0.5},
                            {"outcomeId": "no", "position": "NO", "line": 0.5},
                        ],
                    }
                ],
            }
        ],
        source_url="fixture://game-props",
    )
    assert rows["record_count"] == 2
    assert {row["market_type"] for row in rows["records"]} == {"GAME_PROP"}
    assert {row["proposition"] for row in rows["records"]} == {"FIRST_INNING_RUN"}


@pytest.mark.parametrize("period_label", ["1st inning", "1st inn", "1I"])
def test_first_inning_period_label_variants_are_admitted(period_label):
    rows = normalize_games(
        config=get_sport_config("MLB"),
        schedule_payload=load_fixture("mlb_schedule.json"),
        events_payloads=[
            {
                "eventId": "mlb-event-1",
                "markets": [
                    {
                        "marketType": "GAME_PROP",
                        "proposition": "RUN_SCORED",
                        "label": "Run scored",
                        "periodLabel": period_label,
                        "outcomes": [{"outcomeId": "yes", "position": "YES", "line": 0.5}],
                    }
                ],
            }
        ],
        source_url="fixture://game-props",
    )
    assert rows["record_count"] == 1


def test_non_first_inning_game_props_remain_excluded():
    rows = normalize_games(
        config=get_sport_config("MLB"),
        schedule_payload=load_fixture("mlb_schedule.json"),
        events_payloads=[
            {
                "eventId": "mlb-event-1",
                "markets": [
                    {
                        "marketType": "GAME_PROP",
                        "proposition": "ALTERNATE_TOTAL",
                        "label": "Alternate game total",
                        "periodLabel": "Full game",
                        "outcomes": [{"outcomeId": "over", "position": "OVER", "line": 8.5}],
                    }
                ],
            }
        ],
        source_url="fixture://game-props",
    )
    assert rows["record_count"] == 0


def test_unknown_market_is_dropped_for_mlb():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    # Mystery Barrel Prop is not in the ALLOWED_MLB_PLAYER_PROPS list, so it is dropped.
    mystery_rows = [row for row in rows if row["market_raw"] == "Mystery Barrel Prop"]
    assert len(mystery_rows) == 0


def test_mlb_doubleheader_dedupe_keeps_same_player_line_in_different_events():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    # Only the pitcher-strikeout prop is kept (mystery barrel is dropped).
    assert len(rows) == 1
    assert rows[0]["event_id"] == "mlb-event-1"
    assert rows[0]["market"] == "SO"


def test_missing_market_id_is_dropped_if_not_in_whitelist():
    rows = normalize_player_props(
        load_fixture("mlb_player_props.json"),
        load_fixture("mlb_schedule.json"),
        get_sport_config("MLB"),
    )
    mystery_rows = [row for row in rows if row["market_id"] is None]
    assert len(mystery_rows) == 0


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
                    "outcomeId": "outcome-x",
                    "teamId": "1",
                    "position": "OVER",
                    "line": 1.5,
                    "marketLabel": "Some Guy - Strikeouts",
                    "proposition": "STRIKEOUTS",
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


def test_wnba_expansion_team_portland_resolves_team_and_opponent():
    # Regression: 2026-07-18 Bridget Carleton (PDX @ MIN). PDX was missing from
    # WNBA_TEAM_ALIASES, so team came back None while opponent resolved to MIN —
    # the candidates row then showed the player's own opponent as her only team
    # context. Both expansion sides must canonicalize.
    schedule = {
        "events": [
            {
                "eventId": "evt-pdx",
                "away": {"alias": "PDX", "teamId": "t-pdx", "name": "Fire", "market": "Portland"},
                "home": {"alias": "MIN", "teamId": "t-min", "name": "Lynx", "market": "Minnesota"},
            }
        ]
    }
    props = {
        "props": [
            {
                "outcome": {
                    "eventId": "evt-pdx",
                    "outcomeId": "outcome-pdx",
                    "teamId": "t-pdx",
                    "position": "UNDER",
                    "line": 12.5,
                    "marketLabel": "Bridget Carleton - Points",
                    "proposition": "POINTS",
                    "marketId": "m-pts",
                    "bestOdds": 115,
                    "books": [],
                    "bookOdds": {},
                },
                "stats": {},
            }
        ]
    }
    rows = normalize_player_props(props, schedule, get_sport_config("WNBA"))
    assert len(rows) == 1
    row = rows[0]
    assert row["team"] == "PDX"
    assert row["opponent"] == "MIN"
    assert row["matchup"] == "PDX @ MIN"


def test_unicode_minus_odds_parse_to_int_best_odds():
    schedule = {"events": [{"eventId": "evt-u", "away": {"alias": "NYY", "teamId": "1"},
        "home": {"alias": "BOS", "teamId": "2"}}]}
    props = {"props": [{"outcome": {"eventId": "evt-u", "outcomeId": "outcome-u", "teamId": "1",
        "position": "OVER", "line": 1.5, "marketLabel": "Guy - Strikeouts",
        "proposition": "STRIKEOUTS", "marketId": "m-u",
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


def _mlb_prop(
    market_id,
    label,
    *,
    event_id="e1",
    proposition="STRIKEOUTS",
    line=1.5,
    position="OVER",
):
    side = str(position or "OVER").strip().upper()
    return {
        "outcome": {
            "eventId": event_id,
            "outcomeId": f"{market_id}:{event_id}:{line}:{side.lower()}",
            "teamId": "10",
            "oppTeamId": "20",
            "position": side,
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


def test_full_game_maps_and_scoped_variant_dropped():
    payload = {
        "props": [
            _mlb_prop("m1", "Aaron Judge - Strikeouts"),
            _mlb_prop("m2", "Aaron Judge - 1st Inning Strikeouts"),
        ]
    }
    # MLB whitelist drops the 1st inning prop because its canonical market maps to None
    rows = normalize_player_props(payload, _mlb_one_event_schedule(), get_sport_config("MLB"))
    assert len(rows) == 1
    assert rows[0]["sport_context"]["scope"] == "full_game"
    assert rows[0]["market"] == "SO"


def test_teamid_fallback_fills_team_when_event_not_in_schedule():
    # Prop references an event that is not in the schedule index, but its
    # teamId/oppTeamId appear elsewhere in the schedule.
    payload = {"props": [_mlb_prop("mz", "Some Guy - Strikeouts", event_id="e9", line=0.5)]}
    rows = normalize_player_props(payload, _mlb_one_event_schedule(), get_sport_config("MLB"))
    assert len(rows) == 1
    row = rows[0]
    assert row["team"] == "NYY"
    assert row["opponent"] == "BOS"
    assert row["event_id"] == "e9"  # row preserved despite missing schedule entry
    assert row["matchup"] is None  # no event-specific matchup available


def test_player_prop_promotes_stable_identity_to_top_level():
    rows = normalize_player_props(
        {"props": [_mlb_prop("m1", "Aaron Judge - Hits")]},
        _mlb_one_event_schedule(),
        get_sport_config("MLB"),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["outcome_id"] == row["sport_context"]["outcome_id"]
    assert row["position"] == row["side"] == "OVER"


def test_player_prop_without_stable_outcome_id_is_dropped():
    prop = _mlb_prop("m1", "Aaron Judge - Hits")
    prop["outcome"].pop("outcomeId")

    rows = normalize_player_props(
        {"props": [prop]}, _mlb_one_event_schedule(), get_sport_config("MLB")
    )

    assert rows == []


@pytest.mark.parametrize(
    ("proposition", "label"),
    [
        ("WALKS_ALLOWED", "Walks Allowed"),  # BBA — not on whitelist
        ("HITS_ALLOWED", "Hits Allowed"),  # HA — not on whitelist
        ("HOME_RUNS", "Home Runs"),  # HR — not on whitelist
        ("RBI", "RBIs"),
        ("SINGLES", "Singles"),
        ("TRIPLES", "Triples"),
        ("BATTERS_FACED", "Batters Faced"),
        ("PITCHES_THROWN", "Pitches Thrown"),
        ("TOTAL_BASES", "Total Bases"),  # raw token does not alias; live feed uses BASES
        ("HITS", "Hits"),
        ("BASES", "Total Bases"),
        ("OUTS", "Outs"),
        ("HITSRUNSRBIS", "Hits + Runs + RBIs"),
        ("EARNED_RUNS", "Earned Runs"),
        ("WALKS", "Walks"),
        ("DOUBLES", "Doubles"),
    ],
)
def test_non_whitelisted_mlb_markets_are_excluded_during_generation(
    proposition, label
):
    prop = _mlb_prop("m-prohibited", f"Aaron Judge - {label}", proposition=proposition)

    rows = normalize_player_props(
        {"props": [prop]}, _mlb_one_event_schedule(), get_sport_config("MLB")
    )

    assert rows == []


@pytest.mark.parametrize(
    ("proposition", "label", "expected_market"),
    [
        ("STRIKEOUTS", "Strikeouts", "SO"),
        ("PITCHER_STRIKEOUTS", "Strikeouts", "SO"),
    ],
)
def test_whitelisted_mlb_player_props_are_kept(proposition, label, expected_market):
    prop = _mlb_prop("m-ok", f"Aaron Judge - {label}", proposition=proposition)

    rows = normalize_player_props(
        {"props": [prop]}, _mlb_one_event_schedule(), get_sport_config("MLB")
    )

    assert len(rows) == 1
    assert rows[0]["market"] == expected_market


def test_doubles_are_dropped_not_under_only():
    over = _mlb_prop(
        "m-2b-o", "Aaron Judge - Doubles", proposition="DOUBLES", position="OVER"
    )
    under = _mlb_prop(
        "m-2b-u", "Aaron Judge - Doubles", proposition="DOUBLES", position="UNDER"
    )

    rows = normalize_player_props(
        {"props": [over, under]}, _mlb_one_event_schedule(), get_sport_config("MLB")
    )

    assert rows == []


def test_double_double_under_is_dropped():
    dd_over = {
        "outcome": {
            "eventId": "e1",
            "outcomeId": "o-dd-over",
            "teamId": "10",
            "oppTeamId": "20",
            "position": "OVER",
            "line": 0.5,
            "marketLabel": "Cameron Brink - Double Double",
            "proposition": "DOUBLE_DOUBLE",
            "marketId": "m-dd",
            "bestOdds": 110,
            "books": [],
            "bookOdds": {},
        },
        "stats": {},
    }
    dd_under = {
        "outcome": {
            "eventId": "e1",
            "outcomeId": "o-dd-under",
            "teamId": "10",
            "oppTeamId": "20",
            "position": "UNDER",
            "line": 0.5,
            "marketLabel": "Cameron Brink - Double Double",
            "proposition": "DOUBLE_DOUBLE",
            "marketId": "m-dd",
            "bestOdds": -1100,
            "books": [],
            "bookOdds": {},
        },
        "stats": {},
    }
    td_under = {
        "outcome": {
            "eventId": "e1",
            "outcomeId": "o-td-under",
            "teamId": "10",
            "oppTeamId": "20",
            "position": "UNDER",
            "line": 0.5,
            "marketLabel": "Cameron Brink - Triple Double",
            "proposition": "TRIPLE_DOUBLE",
            "marketId": "m-td",
            "bestOdds": -5000,
            "books": [],
            "bookOdds": {},
        },
        "stats": {},
    }
    schedule = {
        "e1": {
            "event_id": "e1",
            "matchup": "LAS @ SEA",
            "matchup_raw": "LAS @ SEA",
            "away": "LAS",
            "home": "SEA",
            "away_team_id": "10",
            "home_team_id": "20",
            "starts_at": "2026-08-30T21:00:00+00:00",
        }
    }
    rows = normalize_player_props(
        {"props": [dd_over, dd_under, td_under]}, schedule, get_sport_config("WNBA")
    )
    assert len(rows) == 1
    assert rows[0]["market"] == "DD"
    assert rows[0]["position"] == "OVER"

