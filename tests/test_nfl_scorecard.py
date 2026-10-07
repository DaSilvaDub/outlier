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
    assert sc.consensus_lines(two)[("", "LAR", "davante adams", "REC_YDS")] == 82.0


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


def test_blank_or_null_scope_counts_as_full_game() -> None:
    """``scope`` present but None/"" is a full-game line, as everywhere else.

    ``calibration.py``, ``pipeline.py``, ``snapshots.py`` and ``best_bets.py``
    all read a missing/None/empty scope as full game; a strict ``!= "full_game"``
    here dropped those props and left ``hit_vs_line`` blank.
    """
    for scope in (None, ""):
        props = [{"team": "LAR", "player_name": "Davante Adams", "market": "REC_YDS",
                  "position": "OVER", "line": 79.5, "is_consensus_line": True, "scope": scope}]
        assert sc.consensus_lines(props) == {("", "LAR", "davante adams", "REC_YDS"): 79.5}
    alt_scope = [{"team": "LAR", "player_name": "Davante Adams", "market": "REC_YDS",
                  "position": "OVER", "line": 20.5, "is_consensus_line": True,
                  "scope": "first_quarter"}]
    assert sc.consensus_lines(alt_scope) == {}  # period markets still excluded


def test_consensus_line_matches_a_prop_whose_team_is_blank() -> None:
    """A prop with no resolved team still supplies its line.

    ``props.extract_player_props`` leaves ``team`` empty when the feed's team id
    resolves through neither the alias table nor the event's home/away map, so
    an exact-team key silently lost those lines.
    """
    lines = sc.consensus_lines(
        [{"event_id": "e1", "team": None, "player_name": "Kyren Williams",
          "market": "RUSH_YDS", "position": "OVER", "line": 64.5, "is_consensus_line": True}]
    )
    assert lines == {("e1", "", "kyren williams", "RUSH_YDS"): 64.5}
    assert sc.lookup_consensus_line(lines, "e1", "LAR", "kyren williams", "RUSH_YDS") == 64.5

    graded, _ = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, [
        {"event_id": "e1", "team": None, "player_name": "Kyren Williams",
         "market": "RUSH_YDS", "position": "OVER", "line": 64.5, "is_consensus_line": True},
    ])
    mismatch = {(g.tag, g.market): g for g in graded}[("MATCHUP_RUSH_MISMATCH", "RUSH_YDS")]
    assert mismatch.line == 64.5 and mismatch.hit_vs_line is True


def test_exact_team_wins_and_same_name_collision_stays_unmatched() -> None:
    lines = {
        ("e1", "LAR", "kyren williams", "RUSH_YDS"): 64.5,
        ("e1", "", "kyren williams", "RUSH_YDS"): 29.5,
    }
    # An exact team match is never overridden by the blank-team fallback.
    assert sc.lookup_consensus_line(lines, "e1", "LAR", "kyren williams", "RUSH_YDS") == 64.5
    # Two teams carry the same player name in one game and the signal's team is
    # unknown: either line could be the wrong player, so neither is used.
    collision = {
        ("e1", "LAR", "mike williams", "REC_YDS"): 49.5,
        ("e1", "NYJ", "mike williams", "REC_YDS"): 34.5,
    }
    assert sc.lookup_consensus_line(collision, "e1", "", "mike williams", "REC_YDS") is None
    assert sc.lookup_consensus_line(collision, "e1", "LAR", "mike williams", "REC_YDS") == 49.5
    assert sc.lookup_consensus_line(collision, "e1", "DEN", "mike williams", "REC_YDS") is None


def test_blank_team_fallback_never_crosses_games() -> None:
    """A blank-team prop only supplies a line inside its own game.

    ``best_bets._matching_signals`` selects signals by ``prop.event_id`` and
    ``matchup.apply_matchup_signals`` works within one game script, so both
    reach ``_names_match``'s blank-team check already scoped to a single game.
    An unscoped fallback would hand one game's unresolved-team "Mike Williams"
    line to another game's same-named player.
    """
    # NYJ's Mike Williams has a prop whose team never resolved; DEN's Mike
    # Williams is a different player in a different game with no prop at all.
    lines = sc.consensus_lines(
        [{"event_id": "nyj-game", "team": None, "player_name": "Mike Williams",
          "market": "REC_YDS", "position": "OVER", "line": 44.5,
          "is_consensus_line": True}]
    )
    assert sc.lookup_consensus_line(lines, "den-game", "DEN", "mike williams", "REC_YDS") is None
    # Same game: the blank team still falls back.
    assert sc.lookup_consensus_line(lines, "nyj-game", "NYJ", "mike williams", "REC_YDS") == 44.5


def test_grade_signals_does_not_borrow_a_line_from_another_game() -> None:
    other_game_prop = [{
        "event_id": "e2", "team": None, "player_name": "Kyren Williams",
        "market": "RUSH_YDS", "position": "OVER", "line": 20.5, "is_consensus_line": True,
    }]
    graded, _ = sc.grade_signals("2026-09-27", 3, SCRIPTS, ROWS, other_game_prop)
    mismatch = {(g.tag, g.market): g for g in graded}[("MATCHUP_RUSH_MISMATCH", "RUSH_YDS")]
    # SCRIPTS' signals are all event "e1"; the e2 prop must not grade them.
    assert mismatch.line is None and mismatch.hit_vs_line is None
