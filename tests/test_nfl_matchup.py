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


def test_strong_pass_rush_targets_qb_sacks_taken_not_pass_yards():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        event_id="evt-ind-kc",
        home_team="KC",
        away_team="IND",
        game_lines=_ind_kc_lines(),
        tapes=_week1_tape(),
    )
    jones = next(
        s for s in script.prop_signals
        if s.player_name == "Daniel Jones" and s.tag == "MATCHUP_PASS_SUPPRESS"
    )
    assert jones.market == "PASSING_TIMES_SACKED" and jones.side == "OVER"
    assert jones.volume_adjustment == 0.08 and jones.confidence == "MEDIUM"
    assert not any(
        s.market == "PASS_YDS" and s.tag == "MATCHUP_PASS_SUPPRESS" for s in script.prop_signals
    )


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
        _prop("Daniel Jones", "PASSING_TIMES_SACKED", team="IND", opponent="KC", line=2.5),
        _prop("Daniel Jones", "PASS_YDS", team="IND", opponent="KC", line=199.5),
        _prop("Patrick Mahomes", "PASS_YDS", team="KC", opponent="IND", line=219.5),
    ]
    calibrated = apply_matchup_signals(props, [script])
    by_name = {p.player_name: p for p in calibrated}

    walker = by_name["Kenneth Walker III"]
    assert "MATCHUP_RUSH_MISMATCH" in walker.calibration_tags
    assert (walker.calibrated_volume_adjustment or 0) > 0
    assert walker.confidence_tier in {"TIER_2_STRONG", "TIER_1_ANCHOR"}

    jones_sacks, jones_yards = (p for p in calibrated if p.player_name == "Daniel Jones")
    assert "MATCHUP_PASS_SUPPRESS" in jones_sacks.calibration_tags
    assert jones_sacks.calibrated_volume_adjustment == 0.08
    assert jones_sacks.confidence_tier in {None, "", "STANDARD"}  # MEDIUM does not promote
    assert "MATCHUP_PASS_SUPPRESS" not in jones_yards.calibration_tags  # yards no longer faded


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


def test_pass_suppress_boosts_sacks_over_and_leaves_under_unadjusted():
    from outlier_nfl.matchup import apply_matchup_signals, build_matchup_script

    script = build_matchup_script("evt-ind-kc", "KC", "IND", _ind_kc_lines(), _week1_tape())
    over = _prop("Daniel Jones", "PASSING_TIMES_SACKED", team="IND", opponent="KC", line=2.5)
    under = _prop(
        "Daniel Jones",
        "PASSING_TIMES_SACKED",
        team="IND",
        opponent="KC",
        position="UNDER",
        line=2.5,
    )
    stacked = apply_matchup_signals([over, under], [script])
    by_side = {p.position: p for p in stacked}
    assert (by_side["OVER"].calibrated_volume_adjustment or 0) > 0
    assert "MATCHUP_FADE" not in by_side["OVER"].calibration_tags
    assert by_side["UNDER"].calibrated_volume_adjustment is None


def test_elite_rush_targets_sacks_even_for_healthy_qb():
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
    mahomes = [s for s in script.prop_signals if s.player_name == "Patrick Mahomes"]
    # A healthy QB still takes more sacks vs an elite rush; passing yards are never faded.
    assert [(s.market, s.side) for s in mahomes if s.tag == "MATCHUP_PASS_SUPPRESS"] == [
        ("PASSING_TIMES_SACKED", "OVER")
    ]
    assert not any(s.market == "PASS_YDS" and s.side == "UNDER" for s in mahomes)


def test_explicit_grades_override_raw_fallbacks():
    from outlier_nfl.matchup import _is_leaky_run_d, _is_strong_pass_rush, _unit_score

    # Pressure grade present and below 70 decides, even with 4 sacks/game.
    assert not _is_strong_pass_rush(_unit_score({"pass_rush": 55.0, "sacks": 4}))
    # No explicit grade: the sacks fallback still applies.
    assert _is_strong_pass_rush(_unit_score({"sacks": 4}))
    assert not _is_strong_pass_rush(_unit_score({"sacks": 2}))
    # Same rule for run defense: an explicit grade beats yards allowed.
    assert not _is_leaky_run_d(_unit_score({"rush_defense": 70.0, "opp_rush_yards_allowed": 180}))
    assert _is_leaky_run_d(_unit_score({"opp_rush_yards_allowed": 180}))


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


def test_pickem_spread_leans_neutral():
    from outlier_nfl.matchup import build_matchup_script

    lines = [
        _line(market="SPREAD", line=0.0, position="HOME", team="KC"),
        _line(market="SPREAD", line=0.0, position="AWAY", team="IND"),
        _line(market="TOTAL", line=47.0, position="OVER", team=None, proposition="TOTAL"),
    ]
    script = build_matchup_script(
        event_id="evt-ind-kc-pickem",
        home_team="KC",
        away_team="IND",
        game_lines=lines,
        tapes={},
    )
    assert script.spread_lean == "NEUTRAL"
    assert script.script_type == "COMPETITIVE"


def test_team_totals_are_read_through_canonical_proposition_spellings():
    """A team total must drive the projected score whatever the feed calls it.

    ``NflGameLine.proposition`` holds the raw feed string, so matching it
    against literal spellings dropped real team totals ("TEAM_TOTAL_POINTS",
    "Team Total Points") and silently projected the 24/21 placeholder.
    """
    from outlier_nfl.matchup import build_matchup_script

    for proposition in (
        # the four spellings the literal set already accepted ...
        "POINTS",
        "TOTAL_POINTS",
        "TEAM_TOTAL",
        "TOTAL",
        # ... and the ones it silently dropped.
        "TEAM_TOTAL_POINTS",
        "Team Total Points",
        "team_total",
    ):
        lines = [
            _line(market="SPREAD", line=-3.0, position="HOME", team="KC"),
            _line(market="SPREAD", line=3.0, position="AWAY", team="IND"),
            _line(market="TOTAL", line=47.0, position="OVER", team=None, proposition="TOTAL"),
            _line(
                market="POINTS",
                line=27.0,
                position="OVER",
                team="KC",
                market_type="TEAM_PROP",
                proposition=proposition,
            ),
            _line(
                market="POINTS",
                line=20.0,
                position="OVER",
                team="IND",
                market_type="TEAM_PROP",
                proposition=proposition,
            ),
        ]
        script = build_matchup_script(
            event_id="evt-ind-kc-tt",
            home_team="KC",
            away_team="IND",
            game_lines=lines,
            tapes={},
        )
        assert script.home_score == 27.0, proposition
        assert script.away_score == 20.0, proposition


def test_non_points_team_props_do_not_become_projected_scores():
    """A TEAM_PROP that is not a points total must not set the score."""
    from outlier_nfl.matchup import build_matchup_script

    lines = [
        _line(market="SPREAD", line=-3.0, position="HOME", team="KC"),
        _line(market="SPREAD", line=3.0, position="AWAY", team="IND"),
        _line(market="TOTAL", line=47.0, position="OVER", team=None, proposition="TOTAL"),
        _line(
            market="POINTS",
            line=3.5,
            position="OVER",
            team="KC",
            market_type="TEAM_PROP",
            proposition="TEAM_TOTAL_TOUCHDOWNS",
        ),
    ]
    script = build_matchup_script(
        event_id="evt-ind-kc-tt-td",
        home_team="KC",
        away_team="IND",
        game_lines=lines,
        tapes={},
    )
    assert script.home_score != 4.0

def _atl_no_lines(total: float = 47.5, *, home: str = "NO", away: str = "ATL") -> list[NflGameLine]:
    """ATL -1.5 at a home team, team totals 23 (home) / 24 (away)."""
    return [
        _line(event_id="evt-atl-no", market="SPREAD", line=1.5, position="HOME", team=home,
              home=home, away=away),
        _line(event_id="evt-atl-no", market="SPREAD", line=-1.5, position="AWAY", team=away,
              home=home, away=away),
        _line(event_id="evt-atl-no", market="TOTAL", line=total, position="OVER", team=None,
              home=home, away=away),
        _line(event_id="evt-atl-no", market="POINTS", line=23.0, team=home, home=home, away=away,
              market_type="TEAM_PROP", proposition="POINTS"),
        _line(event_id="evt-atl-no", market="POINTS", line=24.0, team=away, home=home, away=away,
              market_type="TEAM_PROP", proposition="POINTS"),
    ]


# NO run D is average (65): no trench mismatch unless something degrades the grade.
NEUTRAL_TAPE = {
    "NO": {"rush_defense": 65.0, "opp_rush_yards_allowed": 130.0, "pass_defense": 60.0},
    "ATL": {"rush_offense": 80.0, "rush_yards": 170.0, "rb1": "Bijan Robinson"},
}


def test_defensive_starters_out_boost_opponent_score_once():
    from outlier_nfl.matchup import DIDF_SCORE_BOOST, build_matchup_script

    script = build_matchup_script(
        "evt-atl-no", "NO", "ATL", _atl_no_lines(), NEUTRAL_TAPE,
        defensive_out={"NO": ["Carl Granderson", "Kaden Elliss"]},
    )
    assert "ATL offense vs depleted NO defense (Carl Granderson, Kaden Elliss)" in script.mismatches
    assert script.away_score == round(24.0 + DIDF_SCORE_BOOST)
    assert script.home_score == 23.0
    # The injury boost adds points only; it must not also degrade NO's run-D grade
    # into a trench mismatch and stack a second boost.
    assert not any(s.tag == "MATCHUP_RUSH_MISMATCH" for s in script.prop_signals)


def test_flat_injury_list_never_counts_as_defensive_starters():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        "evt-atl-no", "NO", "ATL", _atl_no_lines(), NEUTRAL_TAPE,
        injuries=["Carl Granderson", "Kaden Elliss"],
    )
    assert not any("depleted" in m for m in script.mismatches)
    assert (script.home_score, script.away_score) == (23.0, 24.0)


def test_single_defensive_starter_out_is_not_enough():
    from outlier_nfl.matchup import build_matchup_script

    script = build_matchup_script(
        "evt-atl-no", "NO", "ATL", _atl_no_lines(45.5), {},
        defensive_out={"NO": ["Carl Granderson"]},
    )
    assert not any("depleted" in m for m in script.mismatches)
    assert (script.home_score, script.away_score) == (23.0, 24.0)
    assert script.total_lean == "UNDER"  # no edge, so no dome OVER either


def test_build_matchup_scripts_routes_defensive_out_to_each_game():
    from outlier_nfl.matchup import build_matchup_scripts

    scripts = build_matchup_scripts(
        _atl_no_lines(), NEUTRAL_TAPE,
        defensive_out_by_team={"NO": ["Carl Granderson", "Kaden Elliss"], "KC": ["Chris Jones", "Nick Bolton"]},
    )
    (script,) = scripts
    assert script.mismatches == ("ATL offense vs depleted NO defense (Carl Granderson, Kaden Elliss)",)


def test_trench_rushing_mismatch_upgrades_attacking_score():
    from outlier_nfl.matchup import TRENCH_SCORE_BOOST, build_matchup_script

    lines = [
        _line(event_id="e", market="SPREAD", line=3.0, position="HOME", team="CAR", home="CAR", away="PHI"),
        _line(event_id="e", market="SPREAD", line=-3.0, position="AWAY", team="PHI", home="CAR", away="PHI"),
        _line(event_id="e", market="TOTAL", line=44.0, position="OVER", team=None, home="CAR", away="PHI"),
        _line(event_id="e", market="POINTS", line=20.0, team="CAR", home="CAR", away="PHI",
              market_type="TEAM_PROP", proposition="POINTS"),
        _line(event_id="e", market="POINTS", line=24.0, team="PHI", home="CAR", away="PHI",
              market_type="TEAM_PROP", proposition="POINTS"),
    ]
    tape = {
        "CAR": {"rush_defense": 35.0, "opp_rush_yards_allowed": 165.0},
        "PHI": {"rush_offense": 85.0, "rush_yards": 180.0, "rb1": "Saquon Barkley"},
    }
    script = build_matchup_script("e", "CAR", "PHI", lines, tape)
    assert "Saquon Barkley rush vs CAR run D" in script.mismatches
    assert script.away_score == round(24.0 + TRENCH_SCORE_BOOST)
    assert script.home_score == 20.0


def test_inactive_rb1_falls_back_to_next_healthy_depth_chart_back():
    from outlier_nfl.matchup import build_matchup_script
    from outlier_nfl.roster import get_team_depth_chart

    starter, backup = get_team_depth_chart("PHI")["rbs"][:2]
    tape = {
        "CAR": {"rush_defense": 35.0, "opp_rush_yards_allowed": 165.0},
        "PHI": {"rush_offense": 85.0, "rush_yards": 180.0},  # no tape rb1 -> depth chart
    }
    script = build_matchup_script("e", "CAR", "PHI", [], tape, injuries=[starter])
    rush = [s for s in script.prop_signals if s.tag == "MATCHUP_RUSH_MISMATCH"]
    assert rush and {s.player_name for s in rush} == {backup}


def test_dome_band_needs_a_trench_or_injury_edge_to_lean_over():
    from outlier_nfl.matchup import build_matchup_script

    plain = build_matchup_script("evt-atl-no", "NO", "ATL", _atl_no_lines(45.5), {})
    assert plain.total_lean == "UNDER"  # indoors alone is not an edge
    edged = build_matchup_script(
        "evt-atl-no", "NO", "ATL", _atl_no_lines(45.5), {},
        defensive_out={"NO": ["Carl Granderson", "Kaden Elliss"]},
    )
    assert edged.total_lean == "OVER"
    assert any("Dome pace" in n for n in edged.notes)


def test_dome_rule_leaves_low_totals_and_outdoor_games_alone():
    from outlier_nfl.matchup import build_matchup_script

    injured = {"NO": ["Carl Granderson", "Kaden Elliss"], "KC": ["Chris Jones", "Nick Bolton"]}
    low = build_matchup_script(
        "evt-atl-no", "NO", "ATL", _atl_no_lines(43.5), {}, defensive_out=injured
    )
    assert low.total_lean == "UNDER"
    outdoor = build_matchup_script(
        "evt-atl-no", "KC", "ATL", _atl_no_lines(45.5, home="KC"), {}, defensive_out=injured
    )
    assert outdoor.total_lean == "UNDER"


def test_trench_protection_locks_favorite_on_micro_spread():
    from outlier_nfl.matchup import build_matchup_script

    tape = {
        "NO": {"rush_defense": 40.0, "opp_rush_yards_allowed": 160.0},
        "ATL": {"rush_offense": 85.0, "rush_yards": 175.0, "rb1": "Bijan Robinson"},
    }
    script = build_matchup_script("evt-atl-no", "NO", "ATL", _atl_no_lines(), tape)
    assert script.spread_lean == "AWAY"
    assert any(n.startswith("Trench protection: ATL") for n in script.notes)


def test_render_spread_lean_names_the_backed_side():
    from outlier_nfl.matchup import MatchupScript, render_matchup_markdown

    def md(lean: str, home_spread: float) -> str:
        script = MatchupScript(
            event_id="e", matchup="ATL @ NO", home_team="NO", away_team="ATL",
            script_type="COMPETITIVE", spread_lean=lean, total_lean="OVER",
            home_score=23.0, away_score=31.0, home_spread=home_spread, total=47.5,
            mismatches=(), prop_signals=(),
        )
        return render_matchup_markdown(script, date="2026-10-05")

    assert "- **Spread lean:** AWAY (ATL -1.5)" in md("AWAY", 1.5)
    assert "- **Spread lean:** HOME (NO -3.0)" in md("HOME", -3.0)
    assert "- **Spread lean:** NEUTRAL (NO +0.0)" in md("NEUTRAL", 0.0)


def test_underdog_rush_mismatch_triggers_clock_bleed_and_blocks_dome_over():
    """Verify that an underdog rush mismatch suppresses pace (-3.0 pts), blocks dome OVER, and leans dog on heavy spread."""
    from outlier_nfl.matchup import (
        GROUND_DOMINANCE_CLOCK_BLEED,
        HEAVY_FAVORITE_SPREAD,
        build_matchup_script,
    )

    lines = [
        _line(event_id="evt-tb-dal", market="SPREAD", line=-7.5, position="HOME", team="DAL", home="DAL", away="TB"),
        _line(event_id="evt-tb-dal", market="SPREAD", line=7.5, position="AWAY", team="TB", home="DAL", away="TB"),
        _line(event_id="evt-tb-dal", market="TOTAL", line=48.5, position="OVER", team=None, home="DAL", away="TB"),
        _line(event_id="evt-tb-dal", market="POINTS", line=28.0, team="DAL", home="DAL", away="TB", market_type="TEAM_PROP", proposition="POINTS"),
        _line(event_id="evt-tb-dal", market="POINTS", line=20.5, team="TB", home="DAL", away="TB", market_type="TEAM_PROP", proposition="POINTS"),
    ]
    tape = {
        "DAL": {"rush_defense": 35.0, "opp_rush_yards_allowed": 160.0},
        "TB": {"rush_offense": 85.0, "rush_yards": 170.0, "rb1": "Bucky Irving"},
    }

    script = build_matchup_script("evt-tb-dal", "DAL", "TB", lines, tape)

    # 1. Total lean must be UNDER despite DAL being a dome team in DOME_TEAMS
    assert script.total_lean == "UNDER"
    assert any("Ground dominance clock bleed" in n for n in script.notes)

    # 2. Spread lean must be AWAY (TB +7.5) due to possession compression on heavy favorite
    assert script.spread_lean == "AWAY"
    assert any("Underdog ground control" in n for n in script.notes)

    # 3. Projected scores reflect the clock-bleed haircut
    # Without clock bleed, DAL 28, TB 21 + 3.5 = 24.5 -> total ~ 52.5
    # With -3.0 pt clock bleed, total is reduced
    assert (script.home_score + script.away_score) <= 50.0
