"""Matchup-analysis tests: prior-week tape → mismatches → prop signals.

The NFL pipeline must analyze every slate game before calibrating props so
rush/pass/coverage mismatches can boost or fade player propositions alongside
existing deficit-risk, shell, and hit-rate rules.
"""

from __future__ import annotations

from pathlib import Path

from outlier_nfl.models import BookPrice, NflGameLine, NflPlayerProp
from outlier_nfl.pipeline import NflPipeline

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


def _book(odds: int = -110) -> BookPrice:
    return BookPrice(book="DK", odds=odds, odds_raw=str(odds), decimal=1.91)


def _line(
    *,
    event_id: str = "evt-ind-kc",
    market: str,
    line: float,
    position: str = "HOME",
    team: str | None = "KC",
    home: str = "KC",
    away: str = "IND",
    market_type: str = "GAMELINE",
    proposition: str = "SPREAD",
) -> NflGameLine:
    return NflGameLine(
        event_id=event_id,
        event_starts_at="2026-09-20T00:20:00+00:00",
        matchup=f"{away} @ {home}",
        home_team=home,
        away_team=away,
        market_type=market_type,
        market=market,
        proposition=proposition,
        position=position,
        line=line,
        signed_line=f"{line}",
        selection=f"{team or ''} {line}",
        team=team,
        books=(_book(),),
        best_odds=-110,
        implied_probability=52.38,
    )


def _prop(
    player: str,
    market: str,
    *,
    team: str = "KC",
    opponent: str = "IND",
    position: str = "OVER",
    line: float = 79.5,
    event_id: str = "evt-ind-kc",
) -> NflPlayerProp:
    return NflPlayerProp(
        event_id=event_id,
        event_starts_at="2026-09-20T00:20:00+00:00",
        matchup=f"{opponent} @ {team}" if team == "KC" else f"{team} @ {opponent}",
        team=team,
        opponent=opponent,
        player_name=player,
        player_id=f"p_{player}",
        market=market,
        market_raw=market,
        position=position,
        line=line,
        books=(_book(), _book(-108), _book(-112)),
        best_odds=-110,
        implied_probability=52.38,
        confidence_tier="STANDARD",
        calibration_tags=(),
    )


def _ind_kc_lines() -> list[NflGameLine]:
    return [
        _line(market="SPREAD", line=-6.0, position="HOME", team="KC"),
        _line(market="SPREAD", line=6.0, position="AWAY", team="IND"),
        _line(market="TOTAL", line=46.5, position="OVER", team=None, proposition="TOTAL"),
        _line(market="TOTAL", line=46.5, position="UNDER", team=None, proposition="TOTAL"),
        _line(
            market="POINTS",
            line=26.5,
            position="OVER",
            team="KC",
            market_type="TEAM_PROP",
            proposition="POINTS",
        ),
        _line(
            market="POINTS",
            line=20.5,
            position="OVER",
            team="IND",
            market_type="TEAM_PROP",
            proposition="POINTS",
        ),
    ]


def _week1_tape() -> dict[str, dict]:
    return {
        "KC": {
            "rush_yards": 220,
            "pass_yards": 184,
            "points": 31,
            "opp_rush_yards_allowed": 61,
            "opp_pass_yards_allowed": 115,
            "opp_points_allowed": 10,
            "sacks": 4,
            "third_down_allowed": 0.167,
            "rush_offense": 90.5,
            "pass_offense": 56.2,
            "rush_defense": 85.0,
            "pass_defense": 88.0,
            "pass_rush": 82.0,
            "qb_grade": 64.9,
            "qb": "Patrick Mahomes",
            "rb1": "Kenneth Walker III",
            "te": "Travis Kelce",
            "wr_slot": "Rashee Rice",
            "wr_deep": "Xavier Worthy",
        },
        "IND": {
            "rush_yards": 102,
            "pass_yards": 166,
            "points": 23,
            "opp_rush_yards_allowed": 202,
            "opp_pass_yards_allowed": 304,
            "opp_points_allowed": 41,
            "sacks": 1,
            "third_down_allowed": 0.50,
            "rush_offense": 68.0,
            "pass_offense": 53.4,
            "rush_defense": 32.9,
            "pass_defense": 35.0,
            "pass_rush": 55.0,
            "qb_grade": 53.4,
            "qb": "Daniel Jones",
            "rb1": "Jonathan Taylor",
            "te": "Tyler Warren",
            "wr_slot": "Josh Downs",
            "wr_deep": "Alec Pierce",
        },
    }


def test_rush_mismatch_emits_favorite_rb_over_and_td():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    signals = {(s.player_name, s.market, s.side): s for s in script.prop_signals}
    rush = signals[("Kenneth Walker III", "RUSH_YDS", "OVER")]
    td = signals[("Kenneth Walker III", "ANYTIME_TD", "OVER")]
    assert rush.confidence == "HIGH"
    assert rush.volume_adjustment > 0
    assert td.confidence in {"HIGH", "MEDIUM"}
    assert "MATCHUP_RUSH_MISMATCH" in rush.tag


def test_pass_rush_vs_weak_qb_fades_underdog_pass_yards():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    jones = next(
        s for s in script.prop_signals if s.player_name == "Daniel Jones" and s.market == "PASS_YDS"
    )
    assert jones.side == "UNDER"
    assert "MATCHUP_PASS_SUPPRESS" in jones.tag


def test_leaky_secondary_upgrades_slot_and_te():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    names_markets = {(s.player_name, s.market, s.side) for s in script.prop_signals}
    assert ("Travis Kelce", "REC_YDS", "OVER") in names_markets
    assert ("Rashee Rice", "REC_YDS", "OVER") in names_markets or (
        "Josh Downs",
        "REC_YDS",
        "OVER",
    ) in names_markets


def test_favorite_grind_leans_cover_and_under():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    assert script.script_type == "FRONT_RUNNER_GRIND"
    assert script.spread_lean == "HOME"
    assert script.total_lean == "UNDER"
    assert script.home_score > script.away_score


def test_apply_matchup_signals_boosts_matching_overs_and_fades_conflicting_overs():
    from outlier_nfl.matchup import apply_matchup_signals, build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    props = [
        _prop("Kenneth Walker III", "RUSH_YDS", team="KC", opponent="IND"),
        _prop("Daniel Jones", "PASS_YDS", team="IND", opponent="KC", line=199.5),
        _prop("Patrick Mahomes", "PASS_YDS", team="KC", opponent="IND", line=219.5),
    ]
    calibrated = apply_matchup_signals(props, [script])
    by_name = {p.player_name: p for p in calibrated}

    walker = by_name["Kenneth Walker III"]
    assert "MATCHUP_RUSH_MISMATCH" in walker.calibration_tags
    assert (walker.calibrated_volume_adjustment or 0) > 0
    assert walker.confidence_tier in {"TIER_2_STRONG", "TIER_1_ANCHOR"}

    jones = by_name["Daniel Jones"]
    assert "MATCHUP_PASS_SUPPRESS" in jones.calibration_tags
    assert (jones.calibrated_volume_adjustment or 0) < 0
    assert "MATCHUP_FADE" in jones.calibration_tags


def test_missing_tape_still_builds_market_only_script():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes={},
    )
    assert script.script_type in {"COMPETITIVE", "FRONT_RUNNER_GRIND"}
    assert script.event_id == "evt-ind-kc"
    assert isinstance(script.prop_signals, tuple)


def test_deficit_risk_rush_over_is_not_revived_by_matchup_boost():
    from outlier_nfl.calibration import apply_game_script_calibration
    from outlier_nfl.matchup import apply_matchup_signals, build_matchup_script

    lines = [
        _line(market="SPREAD", line=-6.0, team="KC", home="KC", away="IND"),
        _line(market="SPREAD", line=6.0, position="AWAY", team="IND", home="KC", away="IND"),
        _line(
            market="POINTS",
            line=28.5,
            team="KC",
            home="KC",
            away="IND",
            market_type="TEAM_PROP",
            proposition="POINTS",
        ),
        _line(market="TOTAL", line=46.5, position="OVER", team=None, home="KC", away="IND"),
    ]
    tape = {
        "IND": {
            "rush_offense": 88.0,
            "rush_yards": 160,
            "rb1": "Jonathan Taylor",
            "qb_grade": 53.4,
        },
        "KC": {"rush_defense": 30.0, "opp_rush_yards_allowed": 200, "pass_rush": 40.0},
    }
    script = build_matchup_script("evt-ind-kc", "KC", "IND", lines, tape)
    taylor = _prop("Jonathan Taylor", "RUSH_YDS", team="IND", opponent="KC", line=82.5)
    calibrated = apply_game_script_calibration(lines, [taylor])
    stacked = apply_matchup_signals(calibrated, [script])
    row = stacked[0]
    assert "DEFICIT_VOLUME_RISK" in row.calibration_tags
    assert (row.calibrated_volume_adjustment or 0) <= -0.15
    assert row.confidence_tier != "TIER_2_STRONG"


def test_pass_suppress_fades_over_but_does_not_haircut_under():
    from outlier_nfl.matchup import apply_matchup_signals, build_matchup_script

    script = build_matchup_script("evt-ind-kc", "KC", "IND", _ind_kc_lines(), _week1_tape())
    over = _prop("Daniel Jones", "PASS_YDS", team="IND", opponent="KC", line=199.5)
    under = _prop(
        "Daniel Jones",
        "PASS_YDS",
        team="IND",
        opponent="KC",
        position="UNDER",
        line=199.5,
    )
    stacked = apply_matchup_signals([over, under], [script])
    by_side = {p.position: p for p in stacked}
    assert "MATCHUP_FADE" in by_side["OVER"].calibration_tags
    assert (by_side["OVER"].calibrated_volume_adjustment or 0) < 0
    assert "MATCHUP_FADE" not in by_side["UNDER"].calibration_tags
    assert (by_side["UNDER"].calibrated_volume_adjustment or 0) >= 0


def test_healthy_qb_grade_is_not_overridden_by_low_pass_offense():
    from outlier_nfl.matchup import build_matchup_script

    tape = _week1_tape()
    tape["BAL"] = {
        "pass_rush": 85.0,
        "sacks": 5,
        "rush_defense": 70.0,
        "pass_defense": 70.0,
    }
    lines = [
        _line(event_id="evt-bal-kc", market="SPREAD", line=-3.0, team="KC", home="KC", away="BAL"),
        _line(
            event_id="evt-bal-kc",
            market="TOTAL",
            line=46.5,
            position="OVER",
            team=None,
            home="KC",
            away="BAL",
        ),
    ]
    script = build_matchup_script("evt-bal-kc", "KC", "BAL", lines, tape)
    mahomes_pass = [
        s
        for s in script.prop_signals
        if s.player_name == "Patrick Mahomes" and s.market == "PASS_YDS" and s.side == "UNDER"
    ]
    assert mahomes_pass == []


def test_road_favorite_grind_leans_away_and_does_not_invert_score():
    from outlier_nfl.matchup import build_matchup_script

    lines = [
        _line(market="SPREAD", line=6.0, position="HOME", team="IND", home="IND", away="KC"),
        _line(market="SPREAD", line=-6.0, position="AWAY", team="KC", home="IND", away="KC"),
        _line(market="TOTAL", line=46.5, position="OVER", team=None, home="IND", away="KC"),
        _line(
            market="POINTS",
            line=20.5,
            team="IND",
            home="IND",
            away="KC",
            market_type="TEAM_PROP",
            proposition="POINTS",
        ),
        _line(
            market="POINTS",
            line=26.5,
            team="KC",
            home="IND",
            away="KC",
            market_type="TEAM_PROP",
            proposition="POINTS",
        ),
    ]
    script = build_matchup_script("evt-kc-at-ind", "IND", "KC", lines, _week1_tape())
    assert script.script_type == "FRONT_RUNNER_GRIND"
    assert script.spread_lean == "AWAY"
    assert script.away_score > script.home_score


def test_substring_last_name_does_not_tag_wrong_player():
    from outlier_nfl.matchup import apply_matchup_signals, build_matchup_script

    script = build_matchup_script("evt-ind-kc", "KC", "IND", _ind_kc_lines(), _week1_tape())
    chris = _prop("Chris Jones", "PASS_YDS", team="KC", opponent="IND", line=0.5)
    stacked = apply_matchup_signals([chris], [script])
    assert "MATCHUP_PASS_SUPPRESS" not in stacked[0].calibration_tags


def test_pipeline_analyzes_every_matchup_and_tags_props(tmp_path: Path):
    tape_dir = tmp_path / "NFL" / "tape"
    tape_dir.mkdir(parents=True)
    (tape_dir / "prior_week.json").write_text(
        __import__("json").dumps({"week": 1, "season": 2026, "teams": _week1_tape()}),
        encoding="utf-8",
    )

    pipeline = NflPipeline(data_dir=tmp_path)
    reports_dir = tmp_path / "reports" / "NFL"
    summary = pipeline.run(
        date="2026-09-13",
        offline_fixtures_dir=FIXTURES_DIR,
        generate_game_script=True,
        reports_dir=reports_dir,
    )

    assert summary["status"] == "OK"
    assert summary["matchup_scripts_count"] >= 3

    scripts_path = tmp_path / "NFL" / "normalized" / "nfl_matchup_scripts_2026-09-13.json"
    props_path = tmp_path / "NFL" / "normalized" / "nfl_matchup_props_2026-09-13.json"
    assert scripts_path.exists()
    assert props_path.exists()
    payload = __import__("json").loads(scripts_path.read_text(encoding="utf-8"))
    assert payload["count"] >= 3
    event_ids = {row["event_id"] for row in payload["records"]}
    assert "nfl-event-2026-w1-kc-bal" in event_ids
    assert "nfl-event-2026-w1-sf-lar" in event_ids
    assert "nfl-event-2026-w1-dal-phi" in event_ids

    script_files = summary.get("game_script_files") or []
    assert len(script_files) >= 3
    assert (reports_dir / "2026-09-13_BAL_KC_Game_Script.md").exists()
    assert (reports_dir / "2026-09-13_SF_LAR_Game_Script.md").exists()
    assert (reports_dir / "2026-09-13_DAL_PHI_Game_Script.md").exists()
