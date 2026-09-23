import copy
import json
from pathlib import Path
import pytest

from outlier_nfl.config import (
    normalize_team,
    normalize_market,
    detect_scope,
)
from outlier_nfl.utils import format_signed_line
from outlier_nfl.schema import validate_event_markets_payload
from outlier_nfl.models import NflGameLine, NflPlayerProp

try:
    from outlier_nfl.normalizer import (
        american_to_implied_probability,
        normalize_game_markets,
        normalize_player_props,
        build_schedule_index,
        build_team_index,
        adjust_push_probability,
    )
    HAS_NORMALIZER = True
except ImportError:
    HAS_NORMALIZER = False


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


@pytest.fixture
def schedule_payload():
    with open(FIXTURES_DIR / "schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def event_markets_payload():
    with open(FIXTURES_DIR / "event_markets.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def player_props_payload():
    with open(FIXTURES_DIR / "player_props.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_32_nfl_teams_normalization():
    # Canonical 32 NFL teams verification
    canonical_teams = {
        "BUF", "MIA", "NE", "NYJ",
        "BAL", "CIN", "CLE", "PIT",
        "HOU", "IND", "JAX", "TEN",
        "DEN", "KC", "LV", "LAC",
        "DAL", "NYG", "PHI", "WAS",
        "CHI", "DET", "GB", "MIN",
        "ATL", "CAR", "NO", "TB",
        "ARI", "LAR", "SF", "SEA",
    }

    for team_code in canonical_teams:
        assert normalize_team(team_code) == team_code

    # Variations and nicknames
    assert normalize_team("Chiefs") == "KC"
    assert normalize_team("Kansas City") == "KC"
    assert normalize_team("Kansas City Chiefs") == "KC"
    assert normalize_team("KAN") == "KC"

    assert normalize_team("Ravens") == "BAL"
    assert normalize_team("Baltimore Ravens") == "BAL"

    assert normalize_team("49ers") == "SF"
    assert normalize_team("San Francisco 49ers") == "SF"

    assert normalize_team("Rams") == "LAR"
    assert normalize_team("Los Angeles Rams") == "LAR"
    assert normalize_team("LA") == "LAR"

    assert normalize_team("Eagles") == "PHI"
    assert normalize_team("Philadelphia") == "PHI"

    assert normalize_team("Cowboys") == "DAL"
    assert normalize_team("Dallas") == "DAL"

    # Unknown team string gracefully returns None
    assert normalize_team("UnknownTeam") is None
    assert normalize_team("") is None


def test_market_taxonomy_normalization():
    # Passing
    assert normalize_market("PASSING_YARDS") == "PASS_YDS"
    assert normalize_market("PASS_YDS") == "PASS_YDS"
    assert normalize_market("Passing Yards") == "PASS_YDS"
    assert normalize_market("PASSING_TOUCHDOWNS") == "PASS_TD"
    assert normalize_market("PASS_TDS") == "PASS_TD"

    # Rushing
    assert normalize_market("RUSHING_YARDS") == "RUSH_YDS"
    assert normalize_market("RUSH_YDS") == "RUSH_YDS"
    assert normalize_market("RUSHING_ATTEMPTS") == "RUSH_ATT"

    # Receiving
    assert normalize_market("RECEIVING_YARDS") == "REC_YDS"
    assert normalize_market("REC_YDS") == "REC_YDS"
    assert normalize_market("RECEPTIONS") == "REC"

    # Scoring
    assert normalize_market("ANYTIME_TOUCHDOWN") == "ANYTIME_TD"
    assert normalize_market("ANYTIME_TD") == "ANYTIME_TD"

    # Gamelines & Team Props
    assert normalize_market("SPREAD") == "SPREAD"
    assert normalize_market("TOTAL") == "TOTAL"
    assert normalize_market("POINTS") == "POINTS"
    assert normalize_market("TEAM_TOTAL") == "POINTS"


def test_format_signed_line():
    assert format_signed_line(-3.5) == "-3.5"
    assert format_signed_line(3.5) == "+3.5"
    assert format_signed_line(-7) in ("-7.0", "-7")
    assert format_signed_line(7) in ("+7.0", "+7")
    assert format_signed_line(0) in ("0.0", "+0.0", "0", "PK")
    assert format_signed_line(None) is None


def test_detect_scope():
    assert detect_scope(None) == "full_game"
    assert detect_scope("") == "full_game"
    assert detect_scope("1st Half") == "first_half"
    assert detect_scope("2nd Half") == "second_half"
    assert detect_scope("1st Quarter") == "first_quarter"


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_american_to_implied_probability():
    # Percentage representation (52.381%, NOT 0.52381)
    assert american_to_implied_probability(-110) == 52.381
    assert american_to_implied_probability("-110") == 52.381
    assert american_to_implied_probability(100) == 50.0
    assert american_to_implied_probability("EVEN") == 50.0
    assert american_to_implied_probability(150) == 40.0
    assert american_to_implied_probability(-200) == 66.667

    # Edge and invalid inputs
    assert american_to_implied_probability(None) is None
    assert american_to_implied_probability("") is None
    assert american_to_implied_probability("INVALID") is None


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_push_probability_adjustment():
    # Key number push adjustment: Absolute Win Prob = (1.0 - push_prob) * conditional_win_prob
    conditional_p = 52.381  # %
    push_p = 0.10  # 10% push probability on integer line (e.g. -3.0)

    adjusted = adjust_push_probability(conditional_p, push_p)
    expected = 52.381 * (1.0 - 0.10)
    assert pytest.approx(adjusted, rel=1e-3) == expected

    # Half point line has push_prob = 0.0 -> no adjustment
    assert adjust_push_probability(conditional_p, 0.0) == conditional_p


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_normalize_game_markets(schedule_payload, event_markets_payload):
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]  # KC @ BAL

    lines = normalize_game_markets(event, event_markets_payload, team_index)
    assert len(lines) > 0
    assert all(isinstance(line_item, NflGameLine) for line_item in lines)

    # 1. Spreads
    spreads = [line_item for line_item in lines if line_item.market == "SPREAD"]
    assert len(spreads) == 2
    home_spread = next(s for s in spreads if s.position == "HOME")
    away_spread = next(s for s in spreads if s.position == "AWAY")

    assert home_spread.home_team == "KC"
    assert home_spread.line == -3.5
    assert home_spread.signed_line == "-3.5"
    assert "Kansas City Chiefs" in home_spread.selection
    assert home_spread.best_odds == -108
    assert len(home_spread.books) >= 2

    assert away_spread.away_team == "BAL"
    assert away_spread.line == 3.5
    assert away_spread.signed_line == "+3.5"
    assert "Baltimore Ravens" in away_spread.selection
    assert away_spread.best_odds == -110

    # 2. Game Totals
    totals = [line_item for line_item in lines if line_item.market == "TOTAL"]
    assert len(totals) == 2
    over_total = next(t for t in totals if t.position == "OVER")
    under_total = next(t for t in totals if t.position == "UNDER")

    assert over_total.line == 47.5
    assert "Over 47.5" in over_total.selection
    assert over_total.best_odds == -105

    assert under_total.line == 47.5
    assert "Under 47.5" in under_total.selection
    assert under_total.best_odds == -110

    # 3. Team Totals
    team_totals = [line_item for line_item in lines if line_item.market_type == "TEAM_PROP"]
    assert len(team_totals) >= 2
    kc_tt = next(t for t in team_totals if t.team == "KC" and t.position == "OVER")
    bal_tt = next(t for t in team_totals if t.team == "BAL" and t.position == "OVER")

    assert kc_tt.line == 25.5
    assert bal_tt.line == 22.5


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_normalize_player_props(schedule_payload, player_props_payload):
    sched_index = build_schedule_index(schedule_payload)
    props = normalize_player_props(player_props_payload, sched_index)

    assert len(props) > 0
    assert all(isinstance(p, NflPlayerProp) for p in props)

    # 1. Passing Yards (Patrick Mahomes)
    mahomes_py = next(p for p in props if p.player_name == "Patrick Mahomes" and p.market == "PASS_YDS" and p.position == "OVER")
    assert mahomes_py.player_id == "p-mahomes-15"
    assert mahomes_py.team == "KC"
    assert mahomes_py.opponent == "BAL"
    assert mahomes_py.line == 268.5
    assert mahomes_py.best_odds == -110 or mahomes_py.best_odds == -115
    assert mahomes_py.l5_hit_rate == 0.80
    assert len(mahomes_py.books) >= 2

    # 2. Rushing Yards (Lamar Jackson)
    lamar_ruy = next(p for p in props if p.player_name == "Lamar Jackson" and p.market == "RUSH_YDS")
    assert lamar_ruy.team == "BAL"
    assert lamar_ruy.opponent == "KC"
    assert lamar_ruy.line == 52.5

    # 3. Receiving Yards (Travis Kelce)
    kelce_rey = next(p for p in props if p.player_name == "Travis Kelce" and p.market == "REC_YDS")
    assert kelce_rey.team == "KC"
    assert kelce_rey.line == 64.5

    # 4. Anytime Touchdown (Derrick Henry)
    henry_td = next(p for p in props if p.player_name == "Derrick Henry" and p.market == "ANYTIME_TD")
    assert henry_td.line == 0.5
    assert henry_td.best_odds == -140


# ---------------------------------------------------------------------------
# Malformed feed values degrade one field, never the whole slate.
#
# Every one of these used to raise out of extraction: a single junk value in
# one outcome aborted normalize_game_markets / normalize_player_props, and
# neither pipeline call site guards them, so the run lost the entire slate.
# ---------------------------------------------------------------------------


def test_coerce_odds_and_coerce_float_reject_junk_instead_of_raising():
    from outlier_nfl.utils import coerce_float, coerce_odds

    assert coerce_odds(-110) == -110
    assert coerce_odds("+150") == 150
    assert coerce_odds(" -105 ") == -105
    assert coerce_odds(-110.0) == -110, "JSON often carries a whole price as a float"
    # A fractional price is not American odds: reject it rather than truncate.
    for junk in ("EVEN", "N/A", "", "-110.5", -110.5, True, object(), float("nan")):
        assert coerce_odds(junk) is None
    assert coerce_odds(None) is None

    assert coerce_float(0.8) == 0.8
    assert coerce_float("0.72") == 0.72
    assert coerce_float(0) == 0.0, "zero is a real hit rate, not a missing one"
    for junk in ("N/A", "-", "", True, object(), float("inf"), float("nan")):
        assert coerce_float(junk) is None
    assert coerce_float(None) is None

    # json parses an integer literal of any length, and float() raises
    # OverflowError -- not ValueError -- for one too large to convert.
    huge = json.loads('{"l5": 1' + "0" * 400 + "}")["l5"]
    assert coerce_float(huge) is None
    assert coerce_odds(huge) is None


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
@pytest.mark.parametrize("junk", ["EVEN", "N/A", "", "-110.5"])
def test_junk_best_odds_loses_one_price_not_the_event(
    schedule_payload, event_markets_payload, junk
):
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]
    expected = len(normalize_game_markets(event, event_markets_payload, team_index))

    payload = copy.deepcopy(event_markets_payload)
    # Strip the per-book prices so the bestOdds fallback is what gets used.
    target = payload["markets"][0]["outcomes"][0]
    target.pop("odds", None)
    target.pop("bookOdds", None)
    target["bestOdds"] = junk

    lines = normalize_game_markets(event, payload, team_index)

    assert len(lines) == expected, "a junk price dropped other lines from the event"
    damaged = next(line_item for line_item in lines if line_item.outcome_id == "o-spread-kc")
    assert damaged.best_odds is None
    assert damaged.implied_probability is None
    assert damaged.line == -3.5, "the rest of the outcome must still normalize"


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
@pytest.mark.parametrize("field", ["l5", "l10", "l20", "curSeason"])
def test_junk_hit_rate_stat_loses_one_stat_not_the_props_slate(
    schedule_payload, player_props_payload, field
):
    sched_index = build_schedule_index(schedule_payload)
    expected = len(normalize_player_props(player_props_payload, sched_index))

    payload = copy.deepcopy(player_props_payload)
    payload["props"][0].setdefault("stats", {})[field] = "N/A"

    props = normalize_player_props(payload, sched_index)

    assert len(props) == expected, "a junk hit rate dropped other props from the slate"
    mahomes_py = next(
        p
        for p in props
        if p.player_name == "Patrick Mahomes" and p.market == "PASS_YDS" and p.position == "OVER"
    )
    assert mahomes_py.line == 268.5, "the rest of the prop must still normalize"


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_normalize_game_markets_keeps_markets_identified_by_id(
    schedule_payload, event_markets_payload
):
    """A market carrying 'id' instead of 'marketId' must not look like a foreign event.

    An event's own markets response need not repeat the event id on every
    market, and validate_event_markets_payload accepts 'id' as the marketId
    fallback -- so this payload is valid. The event-id guard used to fall back
    to the same key, compare the market's own id against the event id, and drop
    every market: the whole game line board lost, with nothing to flag it.
    """
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]  # KC @ BAL

    def _rename(market):
        copied = dict(market)
        copied["id"] = copied.pop("marketId")
        copied.pop("eventId", None)
        return copied

    renamed = {"markets": [_rename(m) for m in event_markets_payload["markets"]]}
    assert all("marketId" not in market for market in renamed["markets"])
    assert validate_event_markets_payload(renamed) == []

    baseline = normalize_game_markets(event, event_markets_payload, team_index)
    lines = normalize_game_markets(event, renamed, team_index)

    assert len(lines) == len(baseline) > 0
    assert {line_item.market for line_item in lines} == {
        line_item.market for line_item in baseline
    }


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_normalize_game_markets_still_drops_another_events_markets(
    schedule_payload, event_markets_payload
):
    """The guard it replaces still has to do its job: a market that names a
    different event is not this event's market."""
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]  # KC @ BAL

    foreign = {
        "markets": [
            {**dict(market), "eventId": "nfl-event-2026-w1-some-other-game"}
            for market in event_markets_payload["markets"]
        ]
    }

    assert normalize_game_markets(event, foreign, team_index) == []


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
@pytest.mark.parametrize(
    "proposition",
    ["POINTS", "Total Points", "TOTALPOINTS", "total_points", "TOTAL", "TEAM_TOTAL_POINTS"],
)
def test_team_prop_market_type_is_never_claimed_by_the_game_total_branch(
    schedule_payload, event_markets_payload, proposition
):
    """A TEAM_PROP market stays a team total however the feed spells it.

    ``TOTAL`` and ``TOTALPOINTS`` sit in both GAME_TOTAL_PROPOSITIONS and
    TEAM_TOTAL_PROPOSITIONS, so a team total propositioned "Total Points"
    satisfied is_game_total() and was emitted as a GAMELINE total with
    team=None -- one team's 25.5 landing in the games feed beside the real
    47.5 game total and indistinguishable from it.
    """
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]  # KC @ BAL

    payload = copy.deepcopy(event_markets_payload)
    for market in payload["markets"]:
        if (market.get("marketType") or market.get("market_type")) == "TEAM_PROP":
            market["proposition"] = proposition

    lines = normalize_game_markets(event, payload, team_index)

    game_totals = [
        line_item
        for line_item in lines
        if line_item.market_type == "GAMELINE" and line_item.market == "TOTAL"
    ]
    assert {line_item.line for line_item in game_totals} == {47.5}

    team_totals = [line_item for line_item in lines if line_item.market_type == "TEAM_PROP"]
    assert {line_item.market for line_item in team_totals} == {"POINTS"}
    assert next(t for t in team_totals if t.team == "KC" and t.position == "OVER").line == 25.5
    assert next(t for t in team_totals if t.team == "BAL" and t.position == "OVER").line == 22.5


@pytest.mark.skipif(not HAS_NORMALIZER, reason="outlier_nfl.normalizer not yet implemented in M1")
def test_game_total_mislabelled_team_prop_is_still_a_game_total(schedule_payload):
    """The TEAM_PROP guard keys on team attribution, not on the label alone.

    A genuine team total always names a team; a game total never does. Declining
    the game-total branch for anything merely typed TEAM_PROP would make a
    provider's mislabelled game total vanish into the team-total branch and be
    published with team=None -- the mirror of the bug the guard exists to fix.
    """
    team_index = build_team_index(schedule_payload)
    event = schedule_payload["events"][0]  # KC @ BAL

    payload = {
        "markets": [
            {
                "marketId": "mislabelled-total",
                "marketType": "TEAM_PROP",  # provider error: no team is named anywhere
                "proposition": "Total Points",
                "label": "Total Points",
                "outcomes": [
                    {"outcomeId": "o1", "position": "OVER", "line": 47.5, "bestOdds": -110},
                    {"outcomeId": "o2", "position": "UNDER", "line": 47.5, "bestOdds": -110},
                ],
            }
        ]
    }

    lines = normalize_game_markets(event, payload, team_index)

    assert len(lines) == 2
    assert {line_item.market_type for line_item in lines} == {"GAMELINE"}
    assert {line_item.market for line_item in lines} == {"TOTAL"}
    assert {line_item.line for line_item in lines} == {47.5}
    assert all(line_item.team is None for line_item in lines)
