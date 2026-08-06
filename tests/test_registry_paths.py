from outlier_scrapers.paths import PROJECT_ROOT, league_paths
from outlier_scrapers.registry import (
    MLB_TEAM_DISPLAY,
    classify_foreign_market,
    get_sport_config,
    normalize_market,
    normalize_team,
    team_display_name,
)


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


def test_all_mlb_teams_resolve_from_their_stats_api_full_name():
    """Every MLB team's full display name must normalize back to its own code.

    Regression for a real bug: "Cincinnati Reds" (the exact string the MLB
    Stats API returns for probable-pitcher lookups) had no alias entry, so
    normalize_team() silently returned None for Cincinnati. That dropped CIN
    entirely from probable_pitchers.py's by_team lookup and left the
    Athletics' `opponent` field blank for that game — a reasoning pass had no
    way to see it had already-confirmed starter data and fell back to
    external web research instead.
    """
    mlb = get_sport_config("MLB")
    for code, full_name in MLB_TEAM_DISPLAY.items():
        assert normalize_team(mlb, full_name) == code, (
            f"{full_name!r} (display name for {code}) failed to normalize back to {code}"
        )


def test_team_display_name_disambiguates_los_angeles_codes():
    # The exact confusion from the 2026-07-10 report: LAS is the Sparks, not Vegas.
    wnba = get_sport_config("WNBA")
    assert team_display_name(wnba, "LAS") == "Los Angeles Sparks"
    assert team_display_name(wnba, "LVA") == "Las Vegas Aces"
    # Accepts a raw value normalize_team can canonicalize, too.
    assert team_display_name(wnba, "Los Angeles Sparks") == "Los Angeles Sparks"
    mlb = get_sport_config("MLB")
    assert team_display_name(mlb, "LAA") == "Los Angeles Angels"
    assert team_display_name(mlb, "LAD") == "Los Angeles Dodgers"


def test_team_display_name_returns_none_for_unknown():
    wnba = get_sport_config("WNBA")
    assert team_display_name(wnba, "ZZZ") is None
    assert team_display_name(wnba, "") is None


def test_classify_foreign_market_flags_cross_sport_artifacts():
    # A basketball market landing on an MLB event resolves under WNBA, not MLB.
    assert classify_foreign_market("MLB", "REBOUNDS") == "WNBA"
    assert classify_foreign_market("WNBA", "STRIKEOUTS") == "MLB"


def test_classify_foreign_market_ignores_valid_and_shared_markets():
    # Valid own-sport markets, shared markets, and truly-unknown values never flag.
    assert classify_foreign_market("MLB", "PITCHES_THROWN") is None
    assert classify_foreign_market("MLB", "Pitches Thrown") is None  # raw label form
    assert classify_foreign_market("MLB", "TOTAL") is None
    assert classify_foreign_market("WNBA", "POINTS") is None
    assert classify_foreign_market("MLB", "Mystery Barrel Prop") is None


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

