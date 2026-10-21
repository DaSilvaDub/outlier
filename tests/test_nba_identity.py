import pytest

from outlier_nba.identity import IDENTITY_UNRESOLVED, name_key, resolve_player, resolve_team


@pytest.mark.parametrize(
    "a,b",
    [
        ("Jaren Jackson Jr.", "Jaren Jackson"),
        ("Jaren Jackson, Jr.", "jaren jackson jr"),
        ("Gary Payton II", "Gary Payton"),
        ("Wendell Carter Jr", "Wendell Carter"),
        ("Kelly Oubre Jr.", "Kelly Oubre"),
        ("Robert Williams III", "Robert Williams"),
        ("Marvin Bagley IV", "Marvin Bagley"),
        ("Larry Nance Sr.", "Larry Nance"),
        ("De'Aaron Fox", "DeAaron Fox"),
        ("P.J. Washington", "PJ Washington"),
        ("Karl-Anthony Towns", "Karl Anthony Towns"),
        ("Nikola Jokić", "Nikola Jokic"),
        ("  Luka   Doncic ", "luka doncic"),
    ],
)
def test_name_key_is_suffix_and_punctuation_insensitive(a, b):
    assert name_key(a) == name_key(b)


def test_name_key_does_not_merge_different_people():
    assert name_key("Gary Payton II") != name_key("Gary Trent")
    assert name_key("Jalen Williams") != name_key("Jaylin Williams")


def test_name_key_keeps_a_lone_suffix_token_and_handles_empty():
    assert name_key("Jr") == "jr"
    assert name_key("") == ""
    assert name_key(None) == ""


def test_resolve_player_matches_through_suffixes_and_flags_unknowns():
    roster = {name_key("Jaren Jackson Jr."): "1628991", name_key("Gary Payton II"): "1627780"}
    assert resolve_player("Jaren Jackson", roster) == ("1628991", [])
    assert resolve_player("Gary Payton", roster) == ("1627780", [])
    pid, flags = resolve_player("Totally Unknown", roster)
    assert pid is None and flags == [IDENTITY_UNRESOLVED]


def test_resolve_player_refuses_ambiguous_keys():
    roster = {name_key("Jalen Williams"): "a"}
    # two different people collapsing onto one key must not silently pick one
    roster_ambiguous = {"jalen williams": ["a", "b"]}
    assert resolve_player("Jalen Williams", roster) == ("a", [])
    pid, flags = resolve_player("Jalen Williams", roster_ambiguous)
    assert pid is None and flags == [IDENTITY_UNRESOLVED]


def test_resolve_team_handles_la_variants_and_unknown():
    assert resolve_team("Los Angeles Clippers") == "LAC"
    assert resolve_team("LA Lakers") == "LAL"
    assert resolve_team("GS") == "GSW"
    assert resolve_team("LA") is None
