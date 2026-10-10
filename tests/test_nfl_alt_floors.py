"""Unit tests for NFL Sportsbook Alternate Floor Props (outlier_nfl.alt_floors)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl.alt_floors import (
    AltFloorProp,
    _extract_book_quote,
    american_to_implied,
    discover_alt_floor_candidates,
    export_alt_floors,
    generate_alt_floors_pipeline,
    rank_alt_floors,
    render_alt_floors_markdown,
)


def test_american_to_implied():
    assert round(american_to_implied(-110), 4) == 0.5238
    assert round(american_to_implied(-400), 4) == 0.8000
    assert round(american_to_implied(-900), 4) == 0.9000
    assert round(american_to_implied(100), 4) == 0.5000
    assert round(american_to_implied(200), 4) == 0.3333
    assert american_to_implied(None) == 0.5000
    assert american_to_implied("invalid") == 0.5000


def test_extract_book_quote():
    books = [
        {"book": "DRAFTKINGS", "odds": -200},
        {"book": "HARDROCK", "odds": -225},
        {"book": "FANDUEL", "odds": -210},
    ]
    # Primary target match
    name, odds = _extract_book_quote(books, target_book="HARDROCK")
    assert name == "HARDROCK"
    assert odds == -225

    # Alias target match (HARDROCK_R)
    books_alias = [
        {"book": "DRAFTKINGS", "odds": -200},
        {"book": "HARDROCK_R", "odds": -230},
    ]
    name_alias, odds_alias = _extract_book_quote(books_alias, target_book="HARDROCK")
    assert name_alias == "HARDROCK"
    assert odds_alias == -230

    # Fallback when target book is absent
    books_no_hr = [
        {"book": "PINNACLE", "odds": -190},
        {"book": "DRAFTKINGS", "odds": -185},
    ]
    name_fb, odds_fb = _extract_book_quote(books_no_hr, target_book="HARDROCK")
    assert name_fb == "DRAFTKINGS"
    assert odds_fb == -185


def test_discover_alt_floors_dynamic_lines():
    """Verify player-specific floor detection (e.g. Burrow at 224.5, Henry at 64.5)."""
    props = [
        # Joe Burrow: consensus is 274.5, HR has 224.5 (-400)
        {
            "player_name": "Joe Burrow",
            "team": "CIN",
            "opponent": "JAX",
            "matchup": "CIN vs JAX",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 274.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
            "l5_hit_rate": 0.4,
            "l10_hit_rate": 0.4,
        },
        {
            "player_name": "Joe Burrow",
            "team": "CIN",
            "opponent": "JAX",
            "matchup": "CIN vs JAX",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 224.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -400}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
            "season_hit_rate": 0.8,
        },
        # Derrick Henry: consensus is 99.5, HR has 64.5 (-525)
        {
            "player_name": "Derrick Henry",
            "team": "BAL",
            "opponent": "TEN",
            "matchup": "BAL vs TEN",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 99.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
            "l5_hit_rate": 0.6,
            "l10_hit_rate": 0.5,
        },
        {
            "player_name": "Derrick Henry",
            "team": "BAL",
            "opponent": "TEN",
            "matchup": "BAL vs TEN",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 64.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -525}],
            "l5_hit_rate": 1.0,
            "l10_hit_rate": 0.8,
            "season_hit_rate": 0.8,
        },
        # Non-full-game prop (must be excluded)
        {
            "player_name": "Derrick Henry",
            "team": "BAL",
            "opponent": "TEN",
            "matchup": "BAL vs TEN",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 34.5,
            "is_consensus_line": False,
            "scope": "second_half",
            "books": [{"book": "HARDROCK", "odds": -500}],
            "l5_hit_rate": 1.0,
            "l10_hit_rate": 1.0,
        },
    ]

    candidates = discover_alt_floor_candidates(props, target_book="HARDROCK")

    assert "PASS_YDS" in candidates
    assert len(candidates["PASS_YDS"]) == 1
    burrow = candidates["PASS_YDS"][0]
    assert burrow.player_name == "Joe Burrow"
    assert burrow.line == 224.5
    assert burrow.consensus_line == 274.5
    assert burrow.cushion == 50.0
    assert burrow.target_odds == -400

    assert "RUSH_YDS" in candidates
    assert len(candidates["RUSH_YDS"]) == 1
    henry = candidates["RUSH_YDS"][0]
    assert henry.player_name == "Derrick Henry"
    assert henry.line == 64.5
    assert henry.consensus_line == 99.5
    assert henry.cushion == 35.0
    assert henry.target_odds == -525


def test_ranking_top3_and_master_top9():
    """Verify ranking produces exactly Top 3 per category and Master 1-9."""

    def make_prop(player: str, team: str, mkt: str, line: float, cons: float, score: float) -> AltFloorProp:
        return AltFloorProp(
            player_name=player,
            team=team,
            opponent="OPP",
            matchup=f"{team} vs OPP",
            market=mkt,
            market_display=mkt,
            position="OVER",
            line=line,
            consensus_line=cons,
            cushion=cons - line,
            cushion_pct=((cons - line) / cons) * 100,
            target_book="HARDROCK",
            target_odds=-400,
            implied_probability=0.80,
            l5_hit_rate=0.80,
            l10_hit_rate=0.80,
            season_hit_rate=0.80,
            confidence_score=score,
            category_rank=0,
            master_rank=0,
            rationale="",
        )

    candidates = {
        "PASS_YDS": [
            make_prop("QB1", "T1", "PASS_YDS", 199.5, 249.5, 0.85),
            make_prop("QB2", "T2", "PASS_YDS", 224.5, 274.5, 0.82),
            make_prop("QB3", "T3", "PASS_YDS", 199.5, 249.5, 0.80),
            make_prop("QB4", "T4", "PASS_YDS", 174.5, 224.5, 0.75),
        ],
        "RUSH_YDS": [
            make_prop("RB1", "T1", "RUSH_YDS", 39.5, 59.5, 0.91),
            make_prop("RB2", "T2", "RUSH_YDS", 49.5, 79.5, 0.89),
            make_prop("RB3", "T3", "RUSH_YDS", 64.5, 99.5, 0.86),
            make_prop("RB4", "T4", "RUSH_YDS", 39.5, 55.5, 0.81),
        ],
        "REC_YDS": [
            make_prop("WR1", "T1", "REC_YDS", 39.5, 66.5, 0.92),
            make_prop("WR2", "T2", "REC_YDS", 39.5, 59.5, 0.88),
            make_prop("WR3", "T3", "REC_YDS", 39.5, 59.5, 0.87),
            make_prop("WR4", "T4", "REC_YDS", 39.5, 49.5, 0.79),
        ],
    }

    top3, master = rank_alt_floors(candidates)

    # Top 3 per category
    assert len(top3["PASS_YDS"]) == 3
    assert [p.player_name for p in top3["PASS_YDS"]] == ["QB1", "QB2", "QB3"]
    assert [p.category_rank for p in top3["PASS_YDS"]] == [1, 2, 3]

    assert len(top3["RUSH_YDS"]) == 3
    assert [p.player_name for p in top3["RUSH_YDS"]] == ["RB1", "RB2", "RB3"]
    assert [p.category_rank for p in top3["RUSH_YDS"]] == [1, 2, 3]

    assert len(top3["REC_YDS"]) == 3
    assert [p.player_name for p in top3["REC_YDS"]] == ["WR1", "WR2", "WR3"]
    assert [p.category_rank for p in top3["REC_YDS"]] == [1, 2, 3]

    # Master Top 9
    assert len(master) == 9
    assert [p.master_rank for p in master] == list(range(1, 10))
    # Highest score is WR1 (0.92), followed by RB1 (0.91), RB2 (0.89), WR2 (0.88), WR3 (0.87), RB3 (0.86), QB1 (0.85), QB2 (0.82), QB3 (0.80)
    assert master[0].player_name == "WR1"
    assert master[1].player_name == "RB1"
    assert master[2].player_name == "RB2"
    assert master[-1].player_name == "QB3"


import tempfile


def test_export_alt_floors():
    """Verify file exports for JSON, CSV, and Markdown."""
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        exports_dir = tmp_path / "exports"
        reports_dir = tmp_path / "reports"

        prop = AltFloorProp(
            player_name="Derrick Henry",
            team="BAL",
            opponent="TEN",
            matchup="BAL vs TEN",
            market="RUSH_YDS",
            market_display="Rushing Yards",
            position="OVER",
            line=64.5,
            consensus_line=99.5,
            cushion=35.0,
            cushion_pct=35.2,
            target_book="HARDROCK",
            target_odds=-525,
            implied_probability=0.84,
            l5_hit_rate=1.0,
            l10_hit_rate=0.8,
            season_hit_rate=0.8,
            confidence_score=0.86,
            category_rank=1,
            master_rank=1,
            rationale="Top floor play",
        )

        top3 = {"RUSH_YDS": [prop], "PASS_YDS": [], "REC_YDS": []}
        master = [prop]

        outputs = export_alt_floors(
            top3,
            master,
            exports_dir=exports_dir,
            reports_dir=reports_dir,
            date_str="2026-10-04",
            target_book="HARDROCK",
            write_latest=True,
        )

        assert Path(outputs["json"]).exists()
        assert Path(outputs["csv"]).exists()
        assert Path(outputs["markdown"]).exists()

        # Check JSON structure
        with open(outputs["json"], "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["date"] == "2026-10-04"
        assert data["count"] == 1
        assert data["records"][0]["player_name"] == "Derrick Henry"

        # Check CSV structure
        with open(outputs["csv"], "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) == 1
        assert reader[0]["player_name"] == "Derrick Henry"
        assert reader[0]["line"] == "64.5"
        assert reader[0]["target_odds"] == "-525"

        # Check Markdown
        md = Path(outputs["markdown"]).read_text(encoding="utf-8")
        assert "Derrick Henry" in md
        assert "OVER 64.5" in md
        assert "HARDROCK -525" in md


def test_weather_adjustment_penalty():
    """Verify foul weather penalizes PASS_YDS and boosts RUSH_YDS."""
    props = [
        {
            "player_name": "Test QB",
            "team": "BUF",
            "opponent": "NE",
            "matchup": "BUF vs NE",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 199.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "event_id": "EV_W1",
            "books": [{"book": "HARDROCK", "odds": -400}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
        },
        {
            "player_name": "Test QB",
            "team": "BUF",
            "opponent": "NE",
            "matchup": "BUF vs NE",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 249.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "event_id": "EV_W1",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
    ]

    weather = {"EV_W1": {"pass_adjustment": -0.06, "wind_mph": 18.0}}

    candidates_clean = discover_alt_floor_candidates(props)
    candidates_weather = discover_alt_floor_candidates(props, weather_by_event=weather)

    score_clean = candidates_clean["PASS_YDS"][0].confidence_score
    score_weather = candidates_weather["PASS_YDS"][0].confidence_score

    assert score_weather < score_clean


def test_depth_chart_wr_hierarchy_and_road_rush_penalty():
    """Verify WR3+ receives depth chart penalty and road rush lines receive script adjustment."""
    props = [
        # WR1 Brian Thomas Jr (JAX)
        {
            "player_name": "Brian Thomas Jr.",
            "team": "JAX",
            "opponent": "CIN",
            "matchup": "JAX @ CIN",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 49.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -450}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
        },
        {
            "player_name": "Brian Thomas Jr.",
            "team": "JAX",
            "opponent": "CIN",
            "matchup": "JAX @ CIN",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 79.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
        # WR3 Parker Washington (JAX) with identical hit rates & lines
        {
            "player_name": "Parker Washington",
            "team": "JAX",
            "opponent": "CIN",
            "matchup": "JAX @ CIN",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 49.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -450}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
        },
        {
            "player_name": "Parker Washington",
            "team": "JAX",
            "opponent": "CIN",
            "matchup": "JAX @ CIN",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 79.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
    ]

    # Fixture run: the static 2026 chart orders the WRs (F18: fixture-only).
    candidates = discover_alt_floor_candidates(props, static_depth=True)
    rec_cands = {c.player_name: c for c in candidates["REC_YDS"]}

    # Brian Thomas Jr. (WR1) should have higher confidence score than Parker Washington (WR3)
    assert rec_cands["Brian Thomas Jr."].confidence_score > rec_cands["Parker Washington"].confidence_score


def test_windowed_run_does_not_clobber_the_undated_csv():
    """A write_latest=False run must leave the slate-wide artifacts alone.

    ``nfl_alt_floors.csv`` is the undated "current slate" export, the same role
    ``nfl_alt_floors_latest.json`` plays. A windowed run (``--window late``
    sets ``write_latest=False``) only carries part of the slate, so rewriting it
    would leave the current CSV disagreeing with the current JSON.
    """
    def _prop(name: str, rank: int) -> AltFloorProp:
        return AltFloorProp(
            player_name=name,
            team="CIN",
            opponent="JAX",
            matchup="CIN vs JAX",
            market="PASS_YDS",
            market_display="Passing Yards",
            position="OVER",
            line=224.5,
            consensus_line=274.5,
            cushion=50.0,
            cushion_pct=18.2,
            target_book="HARDROCK",
            target_odds=-400,
            implied_probability=0.8,
            l5_hit_rate=0.8,
            l10_hit_rate=0.8,
            season_hit_rate=0.8,
            confidence_score=0.8,
            category_rank=rank,
            master_rank=rank,
            rationale="r",
        )

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_path = Path(tmp_dir_str)
        exports_dir = tmp_path / "exports"
        reports_dir = tmp_path / "reports"

        full = [_prop("QB1", 1), _prop("QB2", 2), _prop("QB3", 3)]
        export_alt_floors(
            {"PASS_YDS": full},
            full,
            exports_dir=exports_dir,
            reports_dir=reports_dir,
            date_str="2026-10-04",
            write_latest=True,
        )
        csv_main = exports_dir / "nfl_alt_floors.csv"
        with open(csv_main, "r", encoding="utf-8") as f:
            assert len(list(csv.DictReader(f))) == 3

        partial = [_prop("QB1", 1)]
        outputs = export_alt_floors(
            {"PASS_YDS": partial},
            partial,
            exports_dir=exports_dir,
            reports_dir=reports_dir,
            date_str="2026-10-04",
            write_latest=False,
        )

        # Full-slate current artifacts survive the windowed run.
        with open(csv_main, "r", encoding="utf-8") as f:
            assert len(list(csv.DictReader(f))) == 3
        with open(exports_dir / "nfl_alt_floors_latest.json", "r", encoding="utf-8") as f:
            assert json.load(f)["count"] == 3

        # The reported CSV path is the dated one that this run actually wrote.
        assert Path(outputs["csv"]).name == "nfl_alt_floors_2026-10-04.csv"
        with open(outputs["csv"], "r", encoding="utf-8") as f:
            assert len(list(csv.DictReader(f))) == 1


def test_alt_floors_juice_cap_and_play_type():
    """Verify that odds worse than -250 are restricted to PARLAY_ONLY while >= -250 are STRAIGHT."""
    from outlier_nfl.alt_floors import MAX_STRAIGHT_ODDS, PLAY_TYPE_PARLAY, PLAY_TYPE_STRAIGHT

    props = [
        # Bucky Irving: Hard Rock -325 (worse than -250 -> PARLAY_ONLY)
        {
            "player_name": "Bucky Irving",
            "team": "TB",
            "opponent": "DAL",
            "matchup": "TB @ DAL",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 39.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -325}],
            "l5_hit_rate": 1.0,
            "l10_hit_rate": 0.8,
            "season_hit_rate": 0.8,
        },
        {
            "player_name": "Bucky Irving",
            "team": "TB",
            "opponent": "DAL",
            "matchup": "TB @ DAL",
            "market": "RUSH_YDS",
            "position": "OVER",
            "line": 49.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
        # CeeDee Lamb: Hard Rock -600 (worse than -250 -> PARLAY_ONLY)
        {
            "player_name": "CeeDee Lamb",
            "team": "DAL",
            "opponent": "TB",
            "matchup": "TB @ DAL",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 49.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -600}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
            "season_hit_rate": 0.8,
        },
        {
            "player_name": "CeeDee Lamb",
            "team": "DAL",
            "opponent": "TB",
            "matchup": "TB @ DAL",
            "market": "REC_YDS",
            "position": "OVER",
            "line": 89.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
        # Safe Straight Player: Hard Rock -200 (>= -250 -> STRAIGHT)
        {
            "player_name": "Dak Prescott",
            "team": "DAL",
            "opponent": "TB",
            "matchup": "TB @ DAL",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 214.5,
            "is_consensus_line": False,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -200}],
            "l5_hit_rate": 0.8,
            "l10_hit_rate": 0.8,
            "season_hit_rate": 0.8,
        },
        {
            "player_name": "Dak Prescott",
            "team": "DAL",
            "opponent": "TB",
            "matchup": "TB @ DAL",
            "market": "PASS_YDS",
            "position": "OVER",
            "line": 249.5,
            "is_consensus_line": True,
            "scope": "full_game",
            "books": [{"book": "HARDROCK", "odds": -110}],
        },
    ]

    candidates = discover_alt_floor_candidates(props)
    rush = candidates["RUSH_YDS"][0]
    rec = candidates["REC_YDS"][0]
    pass_prop = candidates["PASS_YDS"][0]

    assert rush.target_odds == -325
    assert rush.play_type == PLAY_TYPE_PARLAY

    assert rec.target_odds == -600
    assert rec.play_type == PLAY_TYPE_PARLAY

    assert pass_prop.target_odds == -200
    assert pass_prop.play_type == PLAY_TYPE_STRAIGHT

    # Verify Markdown rendering incorporates play_type badges
    top3, master = rank_alt_floors(candidates)
    md = render_alt_floors_markdown(top3, master, date_str="2026-10-08")
    assert "| `PARLAY_ONLY` |" in md
    assert "| `STRAIGHT` |" in md
    assert "Juice Cap Policy" in md


def test_live_floor_depth_comes_from_tape_roles_not_static_chart() -> None:
    """F18: outside fixtures the WR order is the tape's (wr_deep, wr_slot); none -> no penalty."""
    from outlier_nfl.alt_floors import _floor_depth_chart

    tapes = {"JAX": {"wr_deep": "Parker Washington", "wr_slot": "Brian Thomas Jr.", "te": "T"}}
    assert _floor_depth_chart("JAX", tapes, False) == {
        "wrs": ["Parker Washington", "Brian Thomas Jr."], "te": "T"}
    assert _floor_depth_chart("JAX", None, False) == {"wrs": [], "te": None}
    assert _floor_depth_chart("JAX", None, True)["wrs"][0] == "Brian Thomas Jr."


def test_empty_evidenced_starter_set_blocks_qb_floors() -> None:
    """F18: a supplied but empty starter set means no QB is evidenced, so no PASS_YDS floor."""
    base = {"player_name": "Backup Passer", "team": "KC", "opponent": "BAL", "matchup": "BAL @ KC",
            "market": "PASS_YDS", "position": "OVER", "scope": "full_game",
            "l10_hit_rate": 0.9, "l5_hit_rate": 0.9, "season_hit_rate": 0.9}
    props = [{**base, "line": 250.5, "is_consensus_line": True,
              "books": [{"book": "FANDUEL", "odds": -110}]},
             {**base, "line": 200.5, "is_consensus_line": False,
              "books": [{"book": "HARDROCK", "odds": -200}]}]
    assert len(discover_alt_floor_candidates(props).get("PASS_YDS", [])) == 1  # unrestricted
    assert discover_alt_floor_candidates(props, starting_qbs=set()).get("PASS_YDS", []) == []
