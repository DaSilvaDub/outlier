import json
from pathlib import Path
import pytest

from outlier_nfl.config import (
    normalize_team,
    normalize_market,
    detect_scope,
)
from outlier_nfl.utils import format_signed_line
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
