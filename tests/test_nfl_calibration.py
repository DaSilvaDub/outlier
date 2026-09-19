"""Unit and integration test suite for NFL consensus selection and game script calibration.

Validates:
1. Balanced consensus line filtering over alternate ladders and TD markets line==0.5.
2. Push probability adjustment invariants.
3. Game script deficit risk detection for road underdogs.
4. Underdog RB rushing volume haircut (-15%) and target resilience.
5. Two-high shell defensive target divergence (+20% slot, +15% TE, -25% deep threat).
6. Empirical hit-rate tier assignment (Tier-1 Anchor / Tier-2 Strong).
7. Pipeline integration and calibrated artifact persistence.
"""

from __future__ import annotations

from pathlib import Path

from outlier_nfl.calibration import (
    apply_game_script_calibration,
    extract_game_script_context,
)
from outlier_nfl.consensus import (
    identify_consensus_lines_for_group,
    select_consensus_player_props,
)
from outlier_nfl.models import BookPrice, NflGameLine, NflPlayerProp
from outlier_nfl.normalizer import adjust_push_probability
from outlier_nfl.pipeline import NflPipeline
from scripts.nfl_game_script import NflGameScriptGenerator

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"


def _make_prop(
    player: str,
    market: str,
    line: float,
    pos: str = "OVER",
    best_odds: int = -110,
    books_count: int = 4,
    team: str = "DET",
    opponent: str = "BUF",
    event_id: str = "evt-det-buf-01",
    l5: float | None = None,
    l10: float | None = None,
) -> NflPlayerProp:
    books = tuple(
        BookPrice(book=f"Book_{i}", odds=best_odds, odds_raw=str(best_odds), decimal=1.91)
        for i in range(books_count)
    )
    return NflPlayerProp(
        event_id=event_id,
        event_starts_at="2026-09-17T20:15:00-04:00",
        matchup=f"{team} @ {opponent}",
        team=team,
        opponent=opponent,
        player_name=player,
        player_id=f"p_{player.lower().replace(' ', '_')}",
        market=market,
        market_raw=market,
        position=pos,
        line=line,
        books=books,
        best_odds=best_odds,
        implied_probability=52.38,
        l5_hit_rate=l5,
        l10_hit_rate=l10,
    )


def _make_game_line(
    market: str,
    line: float,
    market_type: str = "GAMELINE",
    proposition: str = "POINTS",
    position: str = "OVER",
    team: str | None = None,
    home_team: str = "BUF",
    away_team: str = "DET",
    event_id: str = "evt-det-buf-01",
    best_odds: int = -110,
) -> NflGameLine:
    return NflGameLine(
        event_id=event_id,
        event_starts_at="2026-09-17T20:15:00-04:00",
        matchup=f"{away_team} @ {home_team}",
        home_team=home_team,
        away_team=away_team,
        market_type=market_type,
        market=market,
        proposition=proposition,
        position=position,
        line=line,
        signed_line=f"{'+' if line > 0 else ''}{line}",
        selection=f"{team or ''} {line}",
        team=team,
        books=(BookPrice(book="FD", odds=best_odds, odds_raw=str(best_odds), decimal=1.91),),
        best_odds=best_odds,
        implied_probability=52.38,
    )


# =============================================================================
# 1. Consensus Line Selection Tests
# =============================================================================


def test_consensus_selection_balanced_vs_extreme_ladders():
    """A balanced -110/-110 line must be chosen over an extreme alternate ladder with more books."""
    ladder_over = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 110.5, "OVER", best_odds=+450, books_count=8)
    ladder_under = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 110.5, "UNDER", best_odds=-800, books_count=8)

    consensus_over = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 87.5, "OVER", best_odds=-112, books_count=4)
    consensus_under = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 87.5, "UNDER", best_odds=-108, books_count=4)

    group = [ladder_over, ladder_under, consensus_over, consensus_under]
    targets = identify_consensus_lines_for_group(group)

    assert targets == {(87.5, "OVER"), (87.5, "UNDER")}

    annotated = select_consensus_player_props(group)
    for p in annotated:
        if p.line == 87.5:
            assert p.is_consensus_line is True
        else:
            assert p.is_consensus_line is False


def test_consensus_selection_touchdown_scorer():
    """Touchdown scorer market must strictly select line == 0.5 and OVER."""
    td_05 = _make_prop("Josh Allen", "ANYTIME_TD", 0.5, "OVER", best_odds=+102, books_count=6)
    td_15 = _make_prop("Josh Allen", "ANYTIME_TD", 1.5, "OVER", best_odds=+450, books_count=7)

    group = [td_05, td_15]
    targets = identify_consensus_lines_for_group(group)
    assert targets == {(0.5, "OVER")}

    annotated = select_consensus_player_props(group)
    for p in annotated:
        if p.line == 0.5:
            assert p.is_consensus_line is True
        else:
            assert p.is_consensus_line is False


def test_push_probability_adjustment_invariant():
    """Win probability must be discounted by (1.0 - push_prob)."""
    conditional_prob = 0.55
    push_prob = 0.08
    adj = adjust_push_probability(conditional_prob, push_prob)
    assert round(adj, 4) == round(0.55 * (1.0 - 0.08), 4)

    # 0 push probability leaves conditional unchanged
    assert adjust_push_probability(0.55, 0.0) == 0.55


# =============================================================================
# 2. Game Script Calibration & Deficit Risk Tests
# =============================================================================


def test_extract_game_script_context_deficit_risk():
    """Road underdog >= +4.5 facing team total >= 28.0 triggers away deficit risk."""
    lines = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", +5.5, team="DET"),
        _make_game_line("TOTAL", 30.5, market_type="TEAM_PROP", proposition="POINTS", team="BUF"),
        _make_game_line("TOTAL", 24.5, market_type="TEAM_PROP", proposition="POINTS", team="DET"),
    ]
    contexts = extract_game_script_context(lines)
    assert "evt-det-buf-01" in contexts
    ctx = contexts["evt-det-buf-01"]
    assert ctx["away_deficit_risk"] is True
    assert ctx["home_deficit_risk"] is False
    assert ctx["away_spread"] == 5.5
    assert ctx["home_team_total"] == 30.5


def test_context_reports_quoted_team_totals_below_defaults():
    """A quoted team total below the fallback default must be reported, not the default."""
    lines = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", 5.5, team="DET"),
        _make_game_line("TOTAL", 20.5, market_type="TEAM_PROP", team="BUF"),
        _make_game_line("TOTAL", 17.5, market_type="TEAM_PROP", team="DET"),
    ]
    ctx = extract_game_script_context(lines)["evt-det-buf-01"]

    assert ctx["home_team_total"] == 20.5
    assert ctx["away_team_total"] == 17.5
    # A low-scoring home environment must not read as deficit risk for the road dog.
    assert ctx["away_deficit_risk"] is False


def test_context_falls_back_when_no_team_total_is_quoted():
    """With no team totals on the board the documented defaults still apply."""
    lines = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", 5.5, team="DET"),
    ]
    ctx = extract_game_script_context(lines)["evt-det-buf-01"]

    assert ctx["home_team_total"] == 27.0
    assert ctx["away_team_total"] == 21.0


def test_consensus_never_selects_an_unpriced_line():
    """A line with no book quotes on either side must not win the consensus."""
    ladder_over = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 110.5, "OVER", best_odds=+450, books_count=8)
    ladder_under = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 110.5, "UNDER", best_odds=-800, books_count=8)
    unpriced_over = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 87.5, "OVER", best_odds=None, books_count=0)
    unpriced_under = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 87.5, "UNDER", best_odds=None, books_count=0)

    targets = identify_consensus_lines_for_group(
        [ladder_over, ladder_under, unpriced_over, unpriced_under]
    )

    assert (87.5, "OVER") not in targets
    assert (87.5, "UNDER") not in targets
    # No balanced line exists, so selection falls back to the most-quoted line.
    assert targets == {(110.5, "OVER"), (110.5, "UNDER")}


def test_consensus_is_empty_when_the_whole_market_is_unpriced():
    """A player market entirely off the board yields no consensus line at all."""
    off_board = [
        _make_prop("Jahmyr Gibbs", "RUSH_YDS", line, pos, best_odds=None, books_count=0)
        for line in (87.5, 99.5)
        for pos in ("OVER", "UNDER")
    ]
    assert identify_consensus_lines_for_group(off_board) == set()

    # Touchdown markets take a separate path and must behave the same way.
    off_board_td = [_make_prop("Josh Allen", "ANYTIME_TD", 0.5, "OVER", best_odds=None, books_count=0)]
    assert identify_consensus_lines_for_group(off_board_td) == set()


def test_generator_load_data_reads_pipeline_output(tmp_path):
    """load_data must pick up the normalized files a pipeline run writes."""
    pipeline = NflPipeline(data_dir=tmp_path)
    pipeline.run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR)

    generator = NflGameScriptGenerator(data_dir=tmp_path / "NFL" / "normalized")
    games, props = generator.load_data("2026-09-13")

    assert games and props
    assert all("market" in g for g in games)
    assert all("player_name" in p for p in props)


def test_underdog_rb_deficit_haircut_and_resilient_targets():
    """Underdog RB rushing overs get -15% volume haircut and DEFICIT_VOLUME_RISK tag."""
    lines = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", +5.5, team="DET"),
        _make_game_line("TOTAL", 30.5, market_type="TEAM_PROP", team="BUF"),
    ]
    gibbs_rush = _make_prop("Jahmyr Gibbs", "RUSH_YDS", 87.5, "OVER", team="DET")
    gibbs_td = _make_prop("Jahmyr Gibbs", "ANYTIME_TD", 0.5, "OVER", team="DET")
    gibbs_rec = _make_prop("Jahmyr Gibbs", "REC_YDS", 29.5, "OVER", team="DET")
    cook_rush = _make_prop("James Cook III", "RUSH_YDS", 78.5, "OVER", team="BUF")

    calibrated = apply_game_script_calibration(lines, [gibbs_rush, gibbs_td, gibbs_rec, cook_rush])
    by_name_mkt = {(p.player_name, p.market): p for p in calibrated}

    # Gibbs Rushing gets -15% haircut
    p_gibbs_rush = by_name_mkt[("Jahmyr Gibbs", "RUSH_YDS")]
    assert "DEFICIT_VOLUME_RISK" in p_gibbs_rush.calibration_tags
    assert p_gibbs_rush.calibrated_volume_adjustment == -0.15

    # Gibbs TD and Receiving get RESILIENT_GAME_SCRIPT_TARGET
    p_gibbs_td = by_name_mkt[("Jahmyr Gibbs", "ANYTIME_TD")]
    assert "RESILIENT_GAME_SCRIPT_TARGET" in p_gibbs_td.calibration_tags

    p_gibbs_rec = by_name_mkt[("Jahmyr Gibbs", "REC_YDS")]
    assert "RESILIENT_GAME_SCRIPT_TARGET" in p_gibbs_rec.calibration_tags

    # Cook (favorite) does NOT get deficit risk haircut
    p_cook_rush = by_name_mkt[("James Cook III", "RUSH_YDS")]
    assert "DEFICIT_VOLUME_RISK" not in p_cook_rush.calibration_tags


def test_two_high_shell_target_divergence():
    """Slot WR gets +20%, TE gets +15%, while vertical deep threat gets -25% haircut."""
    lines = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", +5.5, team="DET"),
        _make_game_line("TOTAL", 30.5, market_type="TEAM_PROP", team="BUF"),
    ]
    amon_ra = _make_prop("Amon-Ra St. Brown", "REC_YDS", 79.5, "OVER", team="DET")
    laporta = _make_prop("Sam LaPorta", "REC_YDS", 49.5, "OVER", team="DET")
    jamo = _make_prop("Jameson Williams", "REC_YDS", 59.5, "OVER", team="DET")

    calibrated = apply_game_script_calibration(lines, [amon_ra, laporta, jamo])
    by_name = {p.player_name: p for p in calibrated}

    # Slot upgrade
    p_amon = by_name["Amon-Ra St. Brown"]
    assert "SHELL_COVERAGE_TARGET_UPGRADE" in p_amon.calibration_tags
    assert p_amon.calibrated_volume_adjustment == 0.20

    # TE upgrade
    p_laporta = by_name["Sam LaPorta"]
    assert "SHELL_COVERAGE_TARGET_UPGRADE" in p_laporta.calibration_tags
    assert p_laporta.calibrated_volume_adjustment == 0.15

    # Deep threat haircut
    p_jamo = by_name["Jameson Williams"]
    assert "SHELL_COVERAGE_DEEP_HAIRCUT" in p_jamo.calibration_tags
    assert p_jamo.calibrated_volume_adjustment == -0.25


def test_empirical_hit_rate_tiering():
    """100% L5 and >=80% L10 with >=3 books qualifies as TIER_1_ANCHOR."""
    lines = [_make_game_line("SPREAD", -5.5, team="BUF")]

    t1_prop = _make_prop("Sam LaPorta", "REC", 2.5, "OVER", l5=1.0, l10=0.90, books_count=4)
    t2_prop = _make_prop("Amon-Ra St. Brown", "REC", 6.5, "OVER", l5=0.80, l10=0.75, books_count=4)
    std_prop = _make_prop("DJ Moore", "REC", 4.5, "OVER", l5=0.60, l10=0.50, books_count=4)

    calibrated = apply_game_script_calibration(lines, [t1_prop, t2_prop, std_prop])
    by_name = {p.player_name: p for p in calibrated}

    assert by_name["Sam LaPorta"].confidence_tier == "TIER_1_ANCHOR"
    assert "HIGH_HIT_RATE_ANCHOR" in by_name["Sam LaPorta"].calibration_tags

    assert by_name["Amon-Ra St. Brown"].confidence_tier == "TIER_2_STRONG"
    assert "CONSISTENT_HIT_RATE" in by_name["Amon-Ra St. Brown"].calibration_tags

    assert by_name["DJ Moore"].confidence_tier == "STANDARD"


# =============================================================================
# 3. Pipeline Integration & Game Script Generator Tests
# =============================================================================


def test_pipeline_calibrated_and_high_prob_artifacts(tmp_path):
    """Pipeline run must output calibrated and high probability datasets."""
    pipeline = NflPipeline(data_dir=tmp_path)
    summary = pipeline.run(
        date="2026-09-13",
        offline_fixtures_dir=FIXTURES_DIR,
        generate_game_script=True,
    )

    assert summary["status"] == "OK"
    assert "consensus_props_count" in summary
    assert "tier_1_anchors_count" in summary

    normalized_dir = tmp_path / "NFL" / "normalized"
    assert (normalized_dir / "nfl_calibrated_props_latest.json").exists()
    assert (normalized_dir / "nfl_calibrated_props_2026-09-13.json").exists()
    assert (normalized_dir / "nfl_high_prob_props_latest.json").exists()
    assert (normalized_dir / "nfl_high_prob_props_2026-09-13.json").exists()


def test_game_script_generator_output(tmp_path):
    """NflGameScriptGenerator must generate report with calibration signals and Section 3.5."""
    # Built in-test rather than read from data/NFL/normalized: that directory is
    # gitignored, so sourcing the report from it made this test pass only on a
    # machine that had already run the pipeline, and fail everywhere else.
    games = [
        _make_game_line("SPREAD", -5.5, team="BUF"),
        _make_game_line("SPREAD", 5.5, team="DET"),
        _make_game_line("TOTAL", 30.5, market_type="TEAM_PROP", team="BUF"),
        _make_game_line("TOTAL", 24.5, market_type="TEAM_PROP", team="DET"),
    ]
    raw_props = []
    for player, market, line in (
        ("Jahmyr Gibbs", "RUSH_YDS", 87.5),
        ("Amon-Ra St. Brown", "REC_YDS", 79.5),
        ("Sam LaPorta", "REC", 2.5),
        ("Jared Goff", "PASS_YDS", 249.5),
    ):
        raw_props.append(_make_prop(player, market, line, "OVER", best_odds=-112, team="DET"))
        raw_props.append(_make_prop(player, market, line, "UNDER", best_odds=-108, team="DET"))

    props = apply_game_script_calibration(games, select_consensus_player_props(raw_props))

    generator = NflGameScriptGenerator(data_dir=tmp_path)
    env = generator.extract_game_environment(games, home_team="BUF", away_team="DET")
    profiles = generator.build_player_profiles(props)
    report = generator.generate_report(env, profiles)

    assert "# DETROIT LIONS @ BUFFALO BILLS" in report
    assert "## 3.5 Calibrated High-Probability Prop Anchors & Sizing Calibrations" in report
    assert "Calibrated Signal" in report
    assert "DEFICIT RISK (-15% Vol)" in report
    assert "SHELL UPGRADE (+20% Vol)" in report
