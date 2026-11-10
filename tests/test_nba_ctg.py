"""Cleaning the Glass import adapter: names, cells, season tables, game logs (no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from outlier_nba.ingest import ctg
from outlier_scrapers.registry import NBA_TEAM_DISPLAY

FIX = Path(__file__).parent / "fixtures" / "ctg"


# --- team names -------------------------------------------------------------------------------


def test_city_map_covers_exactly_the_registry_tricodes() -> None:
    assert set(ctg.CTG_TEAM_CITIES.values()) == set(NBA_TEAM_DISPLAY)
    assert len(ctg.CTG_TEAM_CITIES) == 30


@pytest.mark.parametrize(
    ("raw", "code"),
    [("Oklahoma City", "OKC"), ("LA Lakers", "LAL"), ("LA Clippers", "LAC"),
     ("Golden State", "GSW"), ("New York", "NYK"), ("  Brooklyn ", "BKN"),
     ("BKN", "BKN"), ("GSW", "GSW")],
)  # fmt: skip
def test_resolve_ctg_team(raw: str, code: str) -> None:
    assert ctg.resolve_ctg_team(raw) == code


def test_average_row_and_unknowns_are_not_teams() -> None:
    assert ctg.resolve_ctg_team("Average") is None
    assert ctg.resolve_ctg_team("LA") is None  # ambiguous: never guessed
    assert ctg.resolve_ctg_team("") is None


# --- cells ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [("54.7%", 0.547), ("+11.9", 11.9), ("-2.7", -2.7), ("20.7", 20.7), ("64", 64.0),
     (" 115.9 ", 115.9), ("", None), ("-", None), ("NaN", None), (None, None),
     (131.0, 131.0), (float("nan"), None)],
)  # fmt: skip
def test_parse_value(raw, value) -> None:
    got = ctg.parse_value(raw)
    assert got == (pytest.approx(value) if value is not None else None)


def test_parse_value_rejects_garbage() -> None:
    with pytest.raises(ctg.CTGParseError):
        ctg.parse_value("12.3abc")


@pytest.mark.parametrize("raw", ["inf", "-inf", "Infinity", "inf%", float("inf"), float("-inf")])
def test_parse_value_rejects_infinite_values(raw) -> None:
    with pytest.raises(ctg.CTGParseError, match="finite"):
        ctg.parse_value(raw)


# --- season tables ----------------------------------------------------------------------------


def test_read_season_table_detects_fourfactors_and_drops_average() -> None:
    table = ctg.read_season_table(FIX / "fourfactors_synthetic.csv")
    assert table.name == "fourfactors"
    assert [r.team for r in table.rows] == ["BOS", "GSW", "LAL"]
    bos = table.rows[0].metrics
    assert bos["off_pts_poss"] == pytest.approx(114.0)
    assert bos["def_pts_poss"] == pytest.approx(108.0)
    assert bos["off_efg"] == pytest.approx(0.53)
    assert "Diff Rank" not in bos and not any(k.endswith("_rank") for k in bos)


def test_read_season_table_detects_summary_and_skips_spread_diff() -> None:
    table = ctg.read_season_table(FIX / "summary_synthetic.csv")
    assert table.name == "summary"
    bos = table.rows[0].metrics
    assert bos["off_pts_poss"] == pytest.approx(114.0)
    assert bos["l2w_def_pts_poss"] == pytest.approx(108.0)
    assert not any("spread" in k for k in bos)  # market-derived: never imported


def test_read_season_table_rejects_unknown_header(tmp_path) -> None:
    bad = tmp_path / "x.csv"
    bad.write_text("Team,Foo\nBoston,1\n", encoding="utf-8")
    with pytest.raises(ctg.CTGParseError, match="unrecognised CTG table"):
        ctg.read_season_table(bad)


def test_read_season_table_rejects_unresolved_team(tmp_path) -> None:
    src = (FIX / "fourfactors_synthetic.csv").read_text(encoding="utf-8")
    bad = tmp_path / "ff.csv"
    bad.write_text(src.replace("Boston,", "Bostn,"), encoding="utf-8")
    with pytest.raises(ctg.CTGParseError, match="Bostn"):
        ctg.read_season_table(bad)


# --- game logs --------------------------------------------------------------------------------


def _key(games):
    return [(g.game_date, g.opp, g.team_pts, g.opp_pts, g.off_pp100, g.def_pp100) for g in games]


@pytest.mark.parametrize("name", ["gamelog_old_synthetic.json", "gamelog_new_synthetic.json"])
def test_both_json_layouts_parse_identically(name: str) -> None:
    games = ctg.read_gamelog(FIX / name, team="BOS")
    reference = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    assert _key(games) == _key(reference)


def test_read_gamelog_rows() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    assert len(games) == 4
    g = games[0]
    assert g.team == "BOS" and g.opp in ctg.CTG_TEAM_CITIES.values()
    assert isinstance(g.game_date, date) and g.venue in {"home", "away", None}
    assert g.off_pp100 is not None and g.def_pp100 is not None
    assert g.season == "2026-27" and g.season_type == "regular"


def test_gamelog_rows_are_oldest_first_with_venue_and_metrics() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS")
    assert [g.game_date for g in games] == sorted(g.game_date for g in games)
    nyk = games[0]
    assert (nyk.opp, nyk.venue, nyk.team_pts, nyk.opp_pts) == ("NYK", "home", 118, 104)
    assert nyk.off_pp100 == pytest.approx(120.4) and nyk.def_pp100 == pytest.approx(106.1)
    assert nyk.metrics["off_efg"] == pytest.approx(0.55)
    assert nyk.metrics["def_ftr"] == pytest.approx(18.0)
    assert nyk.metrics["spread"] == pytest.approx(-4.0)  # metadata only, never a model input
    assert not any("percentile" in k for k in nyk.metrics)


def test_old_layout_venue_comes_from_the_blank_header_column() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_old_synthetic.json", team="BOS")
    assert [g.venue for g in games] == ["home", "away", "home", "away"]
    assert games[0].season_type == "unspecified"  # old layout mixes in playoff rows


def test_gamelog_dates_are_bounded_by_the_season() -> None:
    games = ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2026-27")
    assert all(date(2026, 8, 1) <= g.game_date <= date(2027, 7, 31) for g in games)
    with pytest.raises(ctg.CTGParseError, match="2025-26"):
        ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="BOS", season="2025-26")


def test_mislabeled_file_is_rejected_and_names_the_claimed_team() -> None:
    with pytest.raises(ctg.CTGIdentityError, match="BOS"):
        ctg.read_gamelog(FIX / "gamelog_mislabeled.json", team="BOS")


def test_file_team_field_must_agree_with_the_folder() -> None:
    with pytest.raises(ctg.CTGIdentityError, match="NYK"):
        ctg.read_gamelog(FIX / "gamelog_new_synthetic.json", team="NYK")


def test_actual_team_is_inferred_only_when_unique() -> None:
    every_other = [t for t in sorted(ctg.CTG_TEAM_CITIES.values()) if t != "TOR"]
    assert ctg.infer_log_owner(every_other) == "TOR"
    assert ctg.infer_log_owner(["NYK", "MIA"]) is None


@pytest.mark.parametrize("name", ["gamelog_empty.json", "gamelog_broken_header.json"])
def test_unusable_files_raise_unusable(name: str) -> None:
    with pytest.raises(ctg.CTGUnusableError):
        ctg.read_gamelog(FIX / name, team="BOS")


def test_infer_season() -> None:
    assert ctg.season_for(date(2025, 10, 22)) == "2025-26"
    assert ctg.season_for(date(2026, 4, 12)) == "2025-26"
    assert ctg.season_for(date(2026, 8, 1)) == "2026-27"


def _new_layout_doc() -> dict:
    return json.loads((FIX / "gamelog_new_synthetic.json").read_text(encoding="utf-8"))


def test_a_game_listed_in_both_views_is_one_playoff_game() -> None:
    doc = _new_layout_doc()
    regular = doc["season_views"]["regular_season"]["four_factors"]
    doc["season_views"]["playoffs"]["four_factors"] = [dict(regular[0])]
    games = ctg.parse_gamelog(json.dumps(doc).encode(), team="BOS", name="x.json")
    assert len(games) == 4
    by_date = {g.game_date: g.season_type for g in games}
    assert by_date[date(2026, 11, 3)] == "playoffs"
    assert set(by_date.values()) == {"regular", "playoffs"}


def test_different_rows_for_one_date_still_conflict() -> None:
    doc = _new_layout_doc()
    regular = doc["season_views"]["regular_season"]["four_factors"]
    doc["season_views"]["playoffs"]["four_factors"] = [dict(regular[0], **{"Team Pts": 1})]
    with pytest.raises(ctg.CTGParseError, match="conflicting"):
        ctg.parse_gamelog(json.dumps(doc).encode(), team="BOS", name="x.json")
