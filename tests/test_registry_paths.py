from outlier_scrapers.paths import PROJECT_ROOT, league_paths
from outlier_scrapers.registry import get_sport_config, normalize_market, normalize_team


def test_supported_sport_registry_resolves_mlb_and_wnba():
    assert get_sport_config("MLB").league_id == "MLB"
    assert get_sport_config("WNBA").league_id == "WNBA"


def test_nba_disabled_by_default():
    try:
        get_sport_config("NBA")
    except ValueError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("NBA should be disabled by default")


def test_non_nba_paths_are_standalone_sports_paths():
    paths = league_paths("MLB")
    assert paths.root == PROJECT_ROOT / "data" / "MLB"
    assert "Current_Matchups" not in str(paths.root)
    assert "nba-props-pipeline" not in str(paths.root)


def test_registry_normalizes_known_and_preserves_unknown_by_returning_none():
    mlb = get_sport_config("MLB")
    assert normalize_team(mlb, "New York Yankees") == "NYY"
    assert normalize_market(mlb, "BASES") == "TB"
    assert normalize_market(mlb, "Mystery Barrel Prop") is None


def test_wnba_has_separate_team_aliases():
    wnba = get_sport_config("WNBA")
    assert normalize_team(wnba, "Las Vegas Aces") == "LVA"
    assert normalize_market(wnba, "POINTS") == "PTS"


def test_unknown_team_returns_none_instead_of_passing_through():
    mlb = get_sport_config("MLB")
    # A short, unknown code must not be fabricated into a canonical value.
    assert normalize_team(mlb, "ZZZ") is None
    assert normalize_team(mlb, "Some Fake Club") is None


def test_phx_team_alias_resolves_for_wnba():
    # Outlier sends "PHX" for Phoenix; earlier the map only knew "PHO".
    assert normalize_team(get_sport_config("WNBA"), "PHX") == "PHX"


def test_new_full_game_market_aliases():
    mlb = get_sport_config("MLB")
    wnba = get_sport_config("WNBA")
    assert normalize_market(mlb, "BASES") == "TB"
    assert normalize_market(mlb, "BATTER_STRIKEOUTS") == "BSO"
    assert normalize_market(mlb, "HITS_RUNS_RBIS") == "HRR"
    assert normalize_market(mlb, "WALKS_ALLOWED") == "BBA"
    assert normalize_market(wnba, "STEALS") == "STL"
    assert normalize_market(wnba, "BLOCKS") == "BLK"
    assert normalize_market(wnba, "FREE_THROWS_ATTEMPTED") == "FTA"
    assert normalize_market(wnba, "DEFENSIVE_REBOUNDS") == "DREB"

