"""Post-slate signal scorecard grading, baselines, ledger, and report."""

from __future__ import annotations

import pytest

import json
from pathlib import Path

from outlier_nfl import scorecard as sc


def _row(name: str, team: str, week: int, **stats: float) -> dict[str, str]:
    base = {"season_type": "REG", "player_display_name": name, "team": team, "week": str(week)}
    base.update({k: str(v) for k, v in stats.items()})
    return base


ROWS = [
    # Kyren: avg 60 rush, week 3 = 89
    _row("Kyren Williams", "LA", 1, rushing_yards=50, rushing_tds=0, receiving_tds=0),
    _row("Kyren Williams", "LA", 2, rushing_yards=70, rushing_tds=1, receiving_tds=0),
    _row("Kyren Williams", "LA", 3, rushing_yards=89, rushing_tds=1, receiving_tds=0),
    # Adams: avg 110 rec, week 3 = 60
    _row("Davante Adams", "LA", 1, receiving_yards=100, targets=8),
    _row("Davante Adams", "LA", 2, receiving_yards=120, targets=10),
    _row("Davante Adams", "LA", 3, receiving_yards=60, targets=9),
    # Push vs avg: avg 40, week 3 = 40
    _row("Push Guy", "DEN", 1, receiving_yards=30),
    _row("Push Guy", "DEN", 2, receiving_yards=50),
    _row("Push Guy", "DEN", 3, receiving_yards=40),
    # Rookie: one prior game only
    _row("Rookie", "DEN", 2, receiving_yards=10),
    _row("Rookie", "DEN", 3, receiving_yards=30),
]


def _sig(tag: str, player: str, team: str, market: str, side: str) -> dict[str, str]:
    return {"event_id": "e1", "tag": tag, "player_name": player, "team": team,
            "market": market, "side": side}


SCRIPTS = [{"prop_signals": [
    _sig("MATCHUP_RUSH_MISMATCH", "Kyren Williams", "LAR", "RUSH_YDS", "OVER"),
    _sig("EFFICIENCY_HOT", "Kyren Williams", "LAR", "RUSH_YDS", "UNDER"),
    _sig("MATCHUP_RUSH_MISMATCH", "Kyren Williams", "LAR", "ANYTIME_TD", "OVER"),
    _sig("EFFICIENCY_HOT", "Davante Adams", "LAR", "REC_YDS", "UNDER"),
    _sig("EFFICIENCY_HOT", "Davante Adams", "LAR", "REC_YDS", "UNDER"),  # duplicate
    _sig("WEATHER_WIND", "Davante Adams", "LAR", "LONG_REC", "UNDER"),  # no box-score column
    _sig("EFFICIENCY_COLD", "Push Guy", "DEN", "REC_YDS", "OVER"),
    _sig("EFFICIENCY_COLD", "Rookie", "DEN", "REC_YDS", "OVER"),
    _sig("VACATED_TARGETS", "Bench Guy", "DEN", "REC_YDS", "OVER"),  # did not play
]}]
PROPS = [
    {"team": "LAR", "player_name": "Kyren Williams", "market": "RUSH_YDS", "position": "OVER",
     "line": 64.5, "is_consensus_line": True},
    {"team": "LAR", "player_name": "Kyren Williams", "market": "RUSH_YDS", "position": "OVER",
     "line": 29.5, "is_consensus_line": False},  # alt ladder ignored
    {"team": "LAR", "player_name": "Davante Adams", "market": "REC_YDS", "position": "UNDER",
     "line": 79.5, "is_consensus_line": True},  # under side ignored
    {"team": "LAR", "player_name": "Kyren Williams", "market": "RUSH_YDS", "position": "OVER",
     "line": 20.5, "is_consensus_line": True, "scope": "first_quarter"},
]


def _graded() -> tuple[dict[tuple[str, str, str], sc.GradedSignal], list[dict[str, str]]]:
    graded, skipped = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, PROPS)
    return {(g.tag, g.player, g.market): g for g in graded}, skipped


def test_grades_vs_average_and_consensus_line() -> None:
    g, _ = _graded()
    mismatch = g[("MATCHUP_RUSH_MISMATCH", "Kyren Williams", "RUSH_YDS")]
    assert mismatch.prior_avg == 60.0 and mismatch.actual == 89.0
    assert mismatch.hit_vs_avg is True and mismatch.line == 64.5 and mismatch.hit_vs_line is True
    hot = g[("EFFICIENCY_HOT", "Kyren Williams", "RUSH_YDS")]
    assert hot.hit_vs_avg is False and hot.hit_vs_line is False
    adams = g[("EFFICIENCY_HOT", "Davante Adams", "REC_YDS")]
    assert adams.hit_vs_avg is True and adams.line is None  # only OVER lines are used
    td = g[("MATCHUP_RUSH_MISMATCH", "Kyren Williams", "ANYTIME_TD")]
    assert td.hit_vs_avg is True and td.prior_avg is None


def test_pushes_duplicates_and_ungraded_reasons() -> None:
    g, skipped = _graded()
    assert g[("EFFICIENCY_COLD", "Push Guy", "REC_YDS")].hit_vs_avg is None  # push
    assert len([k for k in g if k[1] == "Davante Adams"]) == 1  # duplicate signal graded once
    reasons = {(s["player"], s["reason"]) for s in skipped}
    assert ("Davante Adams", "market not gradable from box scores") in reasons
    assert ("Bench Guy", "no box score this week") in reasons
    assert ("Rookie", "fewer than 2 prior games and no line") in reasons


def test_summary_excludes_pushes_and_baseline_reflects_skew() -> None:
    graded, _ = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, PROPS)
    summary = {s["tag"]: s for s in sc.summarize([g.__dict__ for g in graded])}
    assert summary["MATCHUP_RUSH_MISMATCH"]["vs_avg"] == "2/2"
    assert summary["EFFICIENCY_HOT"]["vs_avg"] == "1/2" and summary["EFFICIENCY_HOT"]["vs_line"] == "0/1"
    assert summary["EFFICIENCY_COLD"]["vs_avg"] == "-"  # only a push
    base = sc.direction_baselines(3, ROWS, {"REC_YDS", "RUSH_YDS", "LONG_REC"})
    assert base[("REC_YDS", "UNDER")] == 1.0  # Adams under; Push Guy push excluded
    assert base[("RUSH_YDS", "OVER")] == 1.0
    assert ("LONG_REC", "OVER") not in base


def test_ledger_replaces_same_date_and_accumulates(tmp_path: Path) -> None:
    graded, _ = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, PROPS)
    ledger = tmp_path / "scorecard" / "ledger.jsonl"
    first = sc.update_ledger(ledger, graded, "2026-09-27")
    again = sc.update_ledger(ledger, graded, "2026-09-27")  # rerun is idempotent
    assert len(first) == len(again) == len(graded)
    other = [sc.GradedSignal("2026-10-04", 4, "e2", "EFFICIENCY_HOT", "X", "LAR", "REC_YDS",
                             "UNDER", 50.0, 20.0, True, None, None)]
    total = sc.update_ledger(ledger, other, "2026-10-04")
    assert len(total) == len(graded) + 1
    assert all(json.loads(line) for line in ledger.read_text().splitlines())


def test_markdown_report_sections() -> None:
    graded, skipped = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, PROPS)
    summary = sc.summarize([g.__dict__ for g in graded])
    md = sc.render_markdown("2026-09-27", 3, summary, summary,
                            sc.direction_baselines(3, ROWS, {"RUSH_YDS"}), graded, skipped)
    assert md.startswith("# NFL Signal Scorecard - 2026-09-27 (Week 3)")
    assert "| MATCHUP_RUSH_MISMATCH | 2 | 2/2 (100%) | 1/1 (100%) |" in md
    assert "Ungraded: 1 fewer than 2 prior games and no line" in md


def test_alternate_lines_are_never_used_for_grading() -> None:
    alt_only = [{"team": "LAR", "player_name": "Davante Adams", "market": "REC_YDS",
                 "position": "OVER", "line": 39.5, "is_consensus_line": False}]
    assert sc.consensus_lines(alt_only) == {}
    two = [{"team": "LAR", "player_name": "Davante Adams", "market": "REC_YDS", "position": "OVER",
            "line": v, "is_consensus_line": True} for v in (79.5, 84.5)]
    assert sc.consensus_lines(two)[("LAR", "davante adams", "REC_YDS")] == 82.0


def test_slate_records_and_report_paths(tmp_path) -> None:
    from datetime import date

    from outlier_nfl.scorecard import load_slate_records, write_report

    slate = date(2026, 9, 27)
    assert load_slate_records(tmp_path, "matchup_scripts", slate) is None
    (tmp_path / "nfl_matchup_scripts_2026-09-27.json").write_text('{"records": [{"a": 1}]}')
    assert load_slate_records(tmp_path, "matchup_scripts", slate) == [{"a": 1}]
    with pytest.raises(ValueError):
        load_slate_records(tmp_path, "../x", slate)
    out = write_report(tmp_path / "reports", slate, "# ok")
    assert out.name == "2026-09-27_Signal_Scorecard.md" and out.read_text() == "# ok"


# `team` is not in validate_player_prop_record's required_fields, so
# extract_player_props writing `team=None` reaches the normalized props file.
# Such a prop keys as ("", name, market) and can never meet a signal keyed
# (team, name, market): before the name fallback the signal kept its average
# grade and silently lost its line grade.
_BLANK_TEAM_SCRIPTS = [{"event_id": "E1", "prop_signals": [
    {"event_id": "E1", "player_name": "Darnell Mooney", "team": "ATL",
     "market": "REC_YDS", "side": "UNDER", "tag": "EFFICIENCY_HOT"},
]}]
_BLANK_TEAM_ROWS = [
    _row("Darnell Mooney", "ATL", 1, receiving_yards=60),
    _row("Darnell Mooney", "ATL", 2, receiving_yards=55),
    _row("Darnell Mooney", "ATL", 3, receiving_yards=17),
]


def _mooney_prop(**over: object) -> dict[str, object]:
    return {"event_id": "E1", "player_name": "Darnell Mooney", "market": "REC_YDS",
            "position": "OVER", "line": 42.5, "is_consensus_line": True,
            "scope": "full_game", "team": None, **over}


def _only(props: list[dict[str, object]]) -> sc.GradedSignal:
    graded, _ = sc.grade_signals("2026-09-27", 3, _BLANK_TEAM_SCRIPTS, _BLANK_TEAM_ROWS, props)
    assert len(graded) == 1
    return graded[0]


def test_blank_prop_team_still_grades_against_its_line() -> None:
    blank = _only([_mooney_prop()])
    assert blank.line == 42.5 and blank.hit_vs_line is True
    # Identical to the grade the same prop gets once the feed does resolve a team.
    named = _only([_mooney_prop(team="ATL")])
    assert (named.line, named.hit_vs_line) == (blank.line, blank.hit_vs_line)
    # The average grade never depended on the line and must be unchanged either way.
    assert blank.hit_vs_avg is True and named.hit_vs_avg is True


def test_same_name_two_teams_is_never_guessed() -> None:
    # Two different players share a name and market at different lines: neither
    # line may be credited to the signal's player.
    ambiguous = _only([_mooney_prop(), _mooney_prop(team="CHI", line=61.5)])
    assert ambiguous.line is None and ambiguous.hit_vs_line is None
    assert ambiguous.hit_vs_avg is True  # average grade survives the dropped line
    # Same name on two teams at the same line is not ambiguous: grading is identical.
    agreeing = _only([_mooney_prop(), _mooney_prop(team="CHI")])
    assert agreeing.line == 42.5


def test_exact_team_match_wins_over_the_name_fallback() -> None:
    # The signal's own team carries 42.5; another team's same-named prop sits at
    # 61.5. The exact key must win, so hit_vs_line reflects 42.5, not 61.5.
    graded = _only([_mooney_prop(team="ATL"), _mooney_prop(team="CHI", line=61.5)])
    assert graded.line == 42.5


def test_blank_scope_counts_as_full_game() -> None:
    # pipeline.py and scripts/export_nfl_extra_pack.py both read scope
    # None/"" as full game; consensus_lines must not drop those rows.
    for scope in (None, "", "full_game"):
        assert _only([_mooney_prop(team="ATL", scope=scope)]).line == 42.5
    assert _only([_mooney_prop(team="ATL", scope="first_quarter")]).line is None
