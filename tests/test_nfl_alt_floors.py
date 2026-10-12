"""Unit tests for NFL Sportsbook Alternate Floor Props (outlier_nfl.alt_floors)."""

from __future__ import annotations

import csv
import json
import sys as _sys
import tempfile
from pathlib import Path

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
    assert american_to_implied(None) is None
    assert american_to_implied("invalid") is None


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

    # Hardrock R is a separate book, not the target (#229 review): retail order wins
    books_alias = [
        {"book": "DRAFTKINGS", "odds": -200},
        {"book": "HARDROCK_R", "odds": -230},
    ]
    name_alias, odds_alias = _extract_book_quote(books_alias, target_book="HARDROCK")
    assert name_alias == "DRAFTKINGS"
    assert odds_alias == -200

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
            "event_id": "evt-cinvsjax",
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
            "event_id": "evt-cinvsjax",
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
            "event_id": "evt-balvsten",
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
            "event_id": "evt-balvsten",
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
            "event_id": "evt-balvsten",
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
            quote_eligible=True,
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

    top3, master, _inventory = rank_alt_floors(candidates)

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
            quote_eligible=True,
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
            "event_id": "evt-jaxcin",
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
            "event_id": "evt-jaxcin",
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
            "event_id": "evt-jaxcin",
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
            "event_id": "evt-jaxcin",
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
            quote_eligible=True,
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
    from outlier_nfl.alt_floors import PLAY_TYPE_PARLAY, PLAY_TYPE_STRAIGHT

    props = [
        # Bucky Irving: Hard Rock -325 (worse than -250 -> PARLAY_ONLY)
        {
            "player_name": "Bucky Irving",
            "team": "TB",
            "opponent": "DAL",
            "matchup": "TB @ DAL",
            "event_id": "evt-tbdal",
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
            "event_id": "evt-tbdal",
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
            "event_id": "evt-tbdal",
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
            "event_id": "evt-tbdal",
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
            "event_id": "evt-tbdal",
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
            "event_id": "evt-tbdal",
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
    top3, master, _inventory = rank_alt_floors(candidates)
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


# --- F23 (#229): executable floors need a real quote, identity and injury evidence ---

_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nfl_snapshot_diff as _snap  # noqa: E402

AS_OF = "2026-09-13T15:00:00+00:00"
OK = {"BAL": [], "KC": []}


def _scan(props, **kw):
    kw.setdefault("inactive_by_team", OK)
    cands = discover_alt_floor_candidates(
        props, as_of_utc=AS_OF, require_injury_evidence=True, **kw)
    return rank_alt_floors(cands)


@pytest.mark.parametrize("case,reason", [
    ("missing_odds", "no_quote"),
    ("nan_odds", "nonfinite_quote"),
    ("stale_quote_after_kickoff", "stale_quote"),
    ("inactive_rb", "inactive_player"),
    ("target_book_absent_dfs_only", "no_sportsbook_quote"),
    ("unresolved_team", "missing_identity"),
    ("no_injury_report", "injury_unverified"),
])
def test_ineligible_floor_is_inventory_never_ranked(case, reason):
    props, extra = _snap.alt_floor_cases()[case]
    top3, master, inventory = _scan(props, **extra)
    assert master == [] and all(not v for v in top3.values())
    assert [(i.player_name, i.inventory_reason, i.actionable) for i in inventory] == [
        ("Derrick Henry", reason, False)]


def test_no_quote_has_no_invented_implied_probability():
    props, extra = _snap.alt_floor_cases()["missing_odds"]
    _top3, _master, [inv] = _scan(props, **extra)
    assert inv.implied_probability is None and inv.target_odds is None


def test_nan_odds_never_reach_the_record():
    props, extra = _snap.alt_floor_cases()["nan_odds"]
    _top3, _master, [inv] = _scan(props, **extra)
    assert inv.target_odds is None


def test_same_name_in_two_events_is_two_players():
    props, extra = _snap.alt_floor_cases()["same_name_two_events"]
    _top3, master, inventory = _scan(props, **extra)
    assert sorted((m.event_id, m.team) for m in master) == [("e-bal-kc", "BAL"), ("e-other", "KC")]
    assert inventory == []


def test_retail_fallback_book_stays_executable_and_named():
    props, extra = _snap.alt_floor_cases()["target_book_absent_retail"]
    _top3, [m], _inv = _scan(props, **extra)
    assert (m.target_book, m.target_odds, m.quote_eligible) == ("DRAFTKINGS", -350, True)
    assert not m.actionable and not m.bet_actionable


def test_unresolved_team_row_does_not_abort_the_scan_and_is_inventory():
    """#214's regression, plus F23: the unattributed back is inventory only."""
    props = _snap._floor_pair(team=None, player_id="00-UNK", player="Unattributed Back") + (
        _snap._floor_pair())
    _top3, master, inventory = _scan(props)
    assert [m.player_name for m in master] == ["Derrick Henry"]
    assert [(i.player_name, i.inventory_reason) for i in inventory] == [
        ("Unattributed Back", "missing_identity")]


def test_export_lists_inventory_apart_from_ranked_floors(tmp_path):
    props, extra = _snap.alt_floor_cases()["inactive_rb"]
    top3, master, inventory = _scan(props, **extra)
    export_alt_floors(top3, master, tmp_path / "x", tmp_path / "r", "2026-09-13",
                      inventory=inventory)
    payload = json.loads((tmp_path / "x" / "nfl_alt_floors_2026-09-13.json").read_text())
    assert payload["count"] == 0 and payload["inventory_count"] == 1
    assert payload["inventory"][0]["inventory_reason"] == "inactive_player"
    md = (tmp_path / "r" / "2026-09-13_Alt_Floors.md").read_text(encoding="utf-8")
    assert "Non-actionable inventory" in md and "`inactive_player`" in md


@pytest.mark.parametrize("raw,canonical", [
    ("Hard Rock", "HARDROCK"), ("hardrock", "HARDROCK"), ("HARD_ROCK", "HARDROCK")])
def test_display_name_matches_the_target(raw, canonical):
    assert _extract_book_quote([{"book": raw, "odds": -300}]) == (canonical, -300)


@pytest.mark.parametrize("raw,canonical", [
    ("DraftKings", "DRAFTKINGS"), ("ESPN Bet", "ESPNBET"), ("Hardrock R", "HARDROCK_R")])
def test_display_names_count_as_retail_not_target(raw, canonical):
    from outlier_nfl.alt_floors import floor_inventory_reason

    assert _extract_book_quote([{"book": raw, "odds": -300}]) == (canonical, -300)
    row = {"team": "BAL", "event_id": "e1", "player_name": "x"}
    assert floor_inventory_reason(row, book=raw, odds=-300, target_book="HARDROCK") is None


@pytest.mark.parametrize("case,book", [
    ("display_name_hard_rock", "HARDROCK"), ("display_name_hardrock_r", "HARDROCK_R"),
    ("display_name_espn_bet", "ESPNBET")])
def test_display_name_floor_stays_executable(case, book):
    props, extra = _snap.alt_floor_cases()[case]
    _top3, [m], inventory = _scan(props, **extra)
    assert (m.target_book, m.quote_eligible) == (book, True) and inventory == []


def test_dfs_display_name_is_still_inventory():
    props, extra = _snap.alt_floor_cases()["display_name_prizepicks"]
    _top3, master, [inv] = _scan(props, **extra)
    assert master == [] and inv.inventory_reason == "no_sportsbook_quote"


def test_all_inventory_without_injury_report_logs_a_warning(tmp_path, caplog):
    props, _extra = _snap.alt_floor_cases()["no_injury_report"]
    with caplog.at_level("WARNING", logger="outlier_nfl.alt_floors"):
        generate_alt_floors_pipeline(
            props, exports_dir=tmp_path / "x", reports_dir=tmp_path / "r",
            date_str="2026-09-13", as_of_utc=AS_OF, inactive_by_team=None,
            require_injury_evidence=True)
    assert any("injury report was not admitted" in r.getMessage() for r in caplog.records)



def test_quote_eligible_floor_is_research_never_actionable(tmp_path):
    props, extra = _snap.alt_floor_cases()["target_book_absent_retail"]
    extra = {k: v for k, v in extra.items() if k != "inactive_by_team"}
    cands = discover_alt_floor_candidates(props, as_of_utc=AS_OF, require_injury_evidence=True,
                                         inactive_by_team=OK, **extra)
    [candidate] = cands["RUSH_YDS"]
    assert candidate.quote_eligible and not candidate.actionable
    assert not candidate.bet_actionable and candidate.stake_fraction == 0
    assert candidate.calibrated_probability is None and candidate.ev_per_unit is None
    result = generate_alt_floors_pipeline(
        props, exports_dir=tmp_path / "x", reports_dir=tmp_path / "r",
        date_str="2026-09-13", as_of_utc=AS_OF, require_injury_evidence=True,
        inactive_by_team=OK, export_context={"run_id": "run1", "captured_at": AS_OF,
                                          "run_context": {"mode": "live"}}, **extra)
    assert result["count"] == 1 and result["actionable_count"] == 0
    payload = json.loads(Path(result["outputs"]["json"]).read_text())
    assert payload["records"][0]["quote_eligible"]
    assert payload["records"][0]["ranking_score_version"] == "nfl_alt_floor_rank_v1"
    assert payload["calibration_records"][0]["run_id"] == "run1"
    assert payload["calibration_records"][0]["captured_at"] == AS_OF
    assert payload["calibration_records"][0]["run_context"] == {"mode": "live"}
    assert payload["status"] == "QUOTED_RESEARCH"


def test_capture_includes_selected_candidates_beyond_top_three_and_ineligible(tmp_path):
    props = []
    for n in range(5):
        rows = _snap._floor_pair(player=f"Runner {n}", player_id=f"p{n}")
        if n == 4:
            rows[-1]["books"] = []
        props.extend(rows)
    result = generate_alt_floors_pipeline(
        props, exports_dir=tmp_path / "x", reports_dir=tmp_path / "r",
        date_str="2026-09-13", as_of_utc=AS_OF, require_injury_evidence=True,
        inactive_by_team=OK)
    payload = json.loads(Path(result["outputs"]["json"]).read_text())
    assert result["count"] == 3
    assert payload["calibration_record_count"] == 5
    assert len({r["player_id"] for r in payload["calibration_records"]}) == 5
    assert sum(r["quote_eligible"] for r in payload["calibration_records"]) == 4
    assert all(not r["actionable"] for r in payload["calibration_records"])
    assert all(r["scope"] == "full_game" and r["position"] == "OVER"
               for r in payload["calibration_records"])


def test_missing_historical_rates_are_unavailable_without_invented_counts():
    props = _snap._floor_pair()
    for row in props:
        for key in ("l5_hit_rate", "l10_hit_rate", "season_hit_rate"):
            row.pop(key, None)
    cands = discover_alt_floor_candidates(props)
    [candidate] = cands["RUSH_YDS"]
    assert candidate.l5_hit_rate is None and candidate.l10_hit_rate is None
    assert candidate.season_hit_rate is None
    top3, master, inventory = rank_alt_floors(cands)
    report = render_alt_floors_markdown(top3, master, "2026-09-13", inventory=inventory)
    assert "L5: unavailable" in report and "L10: unavailable" in report
    assert "Ranking Score" in report and "not a win probability" in report
    assert "Confidence Score" not in report and "/5" not in report and "/10" not in report
    assert "Parlay Construction Guidelines" not in report


def test_integer_floor_withholds_unconditional_break_even():
    props = _snap._floor_pair()
    props[-1]["line"] = 49.0
    [candidate] = discover_alt_floor_candidates(props)["RUSH_YDS"]
    assert candidate.break_even_probability is None and candidate.implied_probability is None
    assert candidate.to_dict()["implied_probability"] is None
    assert candidate.break_even_probability_status == "push_probability_unmodeled"
    assert candidate.market_probability is None


def test_full_input_under_quote_is_used_only_for_exact_selected_line():
    props = _snap._floor_pair()
    floor_row = props[-1]
    props.append({**floor_row, "position": "UNDER",
                  "books": [{"book": "HARDROCK", "odds": 400}]})
    [candidate] = discover_alt_floor_candidates(props)["RUSH_YDS"]
    assert candidate.market_probability is not None
    assert candidate.market_probability_status == "proportional_devig_market_benchmark"
    assert candidate.quote_eligible and not candidate.actionable


def test_conflicting_selected_quote_is_not_ranked():
    props = _snap._floor_pair()
    props.append({**props[-1], "books": [{"book": "HARDROCK", "odds": -600}]})
    top3, master, inventory = rank_alt_floors(discover_alt_floor_candidates(props))
    assert not master and all(not items for items in top3.values())
    assert inventory[0].inventory_reason == "conflicting_selected_quotes"



def test_direct_constructor_cannot_activate_an_unvalidated_floor():
    [source] = discover_alt_floor_candidates(_snap._floor_pair())["RUSH_YDS"]
    candidate = AltFloorProp(**{**source.to_dict(), "actionable": True,
                               "bet_actionable": True, "stake_fraction": 0.05,
                               "calibrated_probability": 0.99, "model_probability": 0.99,
                               "fair_decimal_odds": 1.01, "ev_per_unit": 0.2,
                               "probability_status": "validated"})
    assert not candidate.actionable and not candidate.bet_actionable
    assert candidate.stake_fraction == 0 and candidate.calibrated_probability is None
    assert candidate.model_probability is None and candidate.fair_decimal_odds is None
    assert candidate.ev_per_unit is None
    assert candidate.to_dict()["probability_status"] == "insufficient_calibration_history"


def test_conflicting_line_does_not_hide_safe_alternative_quote():
    props = _snap._floor_pair()
    alternative = {**props[-1], "line": 59.5,
                   "books": [{"book": "HARDROCK", "odds": -300}]}
    props.append({**props[-1], "books": [{"book": "HARDROCK", "odds": -600}]})
    props.append(alternative)
    [candidate] = discover_alt_floor_candidates(props)["RUSH_YDS"]
    assert candidate.line == 59.5 and candidate.quote_eligible
    assert candidate.inventory_reason is None and not candidate.actionable
