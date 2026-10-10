"""Offline forensic probes. Reads source; writes only into a temporary directory.

Default mode documents defects rather than asserting that the repository is fixed.
--verify-candidate checks the narrowly scoped fixes in candidate.patch.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import logging
import math
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--data-root", type=Path, help="Optional read-only stored artifact audit")
    parser.add_argument("--verify-candidate", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo_root.resolve()))
    logging.disable(logging.CRITICAL)

    from outlier_nfl import boxscore_nflverse, settle
    from outlier_nfl.best_bets import _price
    from outlier_nfl.boxscore import BoxScoreError, _token, grade_side, player_actual
    from outlier_nfl.config import normalize_market
    from outlier_nfl.projection import (
        attach_model_p_hierarchy_record,
        project_hit_probability_v2,
        week_stat_value,
    )
    from outlier_nfl.snapshots import prop_key

    probes: list[dict] = []

    def emit(name: str, observed, repaired: bool, expected: str) -> None:
        probes.append({"probe": name, "observed": observed, "correct_behavior": expected,
                       "status": "REPAIR_VERIFIED" if repaired else "DEFECT_REPRODUCED"})

    base = {"event_id": "e", "player_name": "Test Player", "market": "REC",
            "position": "OVER", "line": 2.5, "scope": "full_game",
            "best_odds": -110, "implied_probability": 100 * 110 / 210}
    opposite = {**base, "position": "UNDER"}
    price = _price(base, .51, {prop_key(opposite) + (2.5,): opposite})
    emit("negative_ev_price", {"status": price.status, **price.evidence},
         price.status == "CONTRADICTS", "Reject p=.51 at -110: EV=-.02636")

    rows = [{"passing_yards": v} for v in (300, 310, 290)]
    record = {"player_name": "Test Player", "market": "PASS_YDS", "line": 200.5,
              "position": "OVER", "l10_hit_rate": .2}
    direct = project_hit_probability_v2(player_name="Test Player", market="PASS_YDS",
                                      line=200.5, position="OVER", week_rows=rows)
    hierarchy = attach_model_p_hierarchy_record(dict(record), week_index={_token("Test Player"): rows})
    emit("v2_hierarchy", {"direct_p": direct.model_p, "hierarchy": hierarchy},
         hierarchy.get("model_p_source") == direct.source
         and hierarchy.get("model_p") == direct.model_p,
         "Preserve successful Gaussian/Poisson result and matching metadata")

    result = project_hit_probability_v2(player_name="Test Player", market="REC", line=2,
                                       position="UNDER", week_rows=[{"receptions": 2}] * 3)
    strict = math.exp(-2) * 3
    emit("integer_poisson_under", {"observed_p": result.model_p, "strict_p": strict,
                                  "push_p": math.exp(-2) * 2},
         abs(result.model_p - strict) < 1e-6, "UNDER 2 wins only at 0 or 1 reception")

    if args.verify_candidate:
        zero_under = {
            str(rate): project_hit_probability_v2(
                player_name="Test Player", market="REC", line=0, position="UNDER",
                week_rows=[{"receptions": rate}] * 3,
            ).model_p
            for rate in (0, 2)
        }
        emit("poisson_under_zero", zero_under, all(p == 0 for p in zero_under.values()),
             "UNDER 0 has zero win probability at both zero and positive rates")

    td = week_stat_value("ANYTIME_TD", {"passing_tds": 2, "rushing_tds": 0,
                                       "receiving_tds": 0, "special_teams_tds": 0})
    emit("passing_td_is_not_scored_td", td, td == 0, "QB passing TDs do not count as scored TDs")

    first = player_actual("FIRST_TD", {"RUSHING:TD": 1, "RECEIVING:TD": 0})
    emit("first_td_needs_sequence", first, first is None,
         "Aggregate box score cannot settle first-scorer; leave unsupported")

    try:
        nan_grade = grade_side(80, float("nan"), "OVER")
        rejected_nan = False
    except BoxScoreError as exc:
        nan_grade = type(exc).__name__
        rejected_nan = True
    emit("nan_is_not_push", nan_grade, rejected_nan, "Reject nonfinite lines before grading")

    with patch.object(boxscore_nflverse, "load_nflverse_events", return_value=[]) as loader:
        boxscore_nflverse.fetch_nflverse_boxscores_for_date(date(2027, 1, 10))
    season = loader.call_args.kwargs["season"]
    emit("january_boxscore_season", season, season == 2026, "January 2027 belongs to NFL 2026")

    ns = argparse.Namespace(boxscores=None, provider="nflverse", slate_date="2027-02-07",
                            season=None, nflverse_cache=None, week=None)
    with patch.object(boxscore_nflverse, "fetch_nflverse_boxscores_for_date", return_value=[]) as loader:
        settle._load_events_from_args(ns)
    selected = loader.call_args.kwargs["season"]
    emit("settle_cli_does_not_override_season", selected, selected in (None, 2026),
         "Let season-aware provider infer season when CLI --season is absent")

    aliases = {raw: normalize_market(raw) for raw in
               ("LONGEST_PASSING_COMPLETION", "PASSING_COMPLETIONS", "INTERCEPTIONS_THROWN",
                "MADE_FIELD_GOALS")}
    emit("raw_market_aliases", aliases,
         list(aliases.values()) == ["LONG_PASS", "PASS_COMP", "INT", "FGM"],
         "Canonical markets must reach matching weather/projection/settlement consumers")

    if not args.verify_candidate:
        from outlier_nfl.api import OutlierNflApiClient, OutlierNflApiError
        from outlier_nfl.best_bets import TraceInputs, _Sources, _injury_weather
        from outlier_nfl.config import detect_scope
        from outlier_nfl.matchup import load_prior_week_tape
        from outlier_nfl.pipeline import NflPipeline
        from outlier_nfl.scorecard import _actual
        from outlier_nfl.usage import build_profiles

        client = OutlierNflApiClient(bearer_token="forensic_mock_token_no_network")
        first_page = {"props": [{"id": "one"}], "pagination": {"pages": 2, "pageNumber": 1}}
        client.fetch_json = MagicMock(side_effect=[first_page] + [OutlierNflApiError("page 2 failed")] * 20)
        partial = client._fetch_paginated("/offline", "props")
        emit("failed_page_returns_partial_success", partial, False,
             "Incomplete pagination must raise or carry an enforced incomplete status")
        scopes = {x: detect_scope(x) for x in ("Q1", "H1", "1ST_QUARTER", "OT")}
        emit("unknown_period_defaults_full_game", scopes, False,
             "Known period encodings canonicalize; unknown encodings remain unsupported")
        blank_actual = _actual({}, "REC_YDS")
        emit("missing_stat_is_zero", blank_actual, blank_actual is None, "Missing stat must remain unknown")

        player_rows = [{"season_type": "REG", "player_id": "p", "team": "KC", "position": "WR",
                        "player_display_name": "Test Player", "week": str(w), "receiving_yards": str(y)}
                       for w, y in ((1, 100), (2, 100), (3, 0), (9, 500))]
        future = build_profiles(player_rows, before_week=None)["p"]
        prior = build_profiles(player_rows, before_week=4)["p"]
        emit("missing_usage_cutoff", {"unbounded": future.to_dict(), "before_week4": prior.to_dict()},
             False, "Production must require a verified cutoff before calling usage")

        weather = {"event_id": "e", "venue": "outdoor", "pass_adjustment": 0, "tags": []}
        pillar, _ = _injury_weather(base, _Sources(TraceInputs(props=[], run_date="2026-10-04",
                                     weather=[weather], inactive_by_team={})), "KC", "BUF", "2026-10-04")
        emit("empty_forecast_verifies", pillar.to_dict(), pillar.status != "VERIFIED",
             "Unknown outdoor conditions must not validate weather")

        with tempfile.TemporaryDirectory(prefix="outlier_nfl_forensics_") as task_tmp:
            root = Path(task_tmp)
            fixtures = args.repo_root / "tests" / "fixtures" / "nfl"
            pipe = NflPipeline(data_dir=root)
            emit("missing_tape_falls_back", len(load_prior_week_tape(root / "NFL")), False,
                 "Missing production tape must remain unavailable, with explicit fixture mode")
            pipe.run(date="2026-09-13", offline_fixtures_dir=fixtures, reports_dir=root / "reports")
            full = (pipe.normalized_dir / "nfl_matchup_scripts_2026-09-13.json").read_text()
            pipe.run(date="2026-09-13", window="1pm", offline_fixtures_dir=fixtures,
                     reports_dir=root / "reports")
            after = (pipe.normalized_dir / "nfl_matchup_scripts_2026-09-13.json").read_text()
            emit("window_overwrites_full_slate", {"changed": full != after,
                 "before_count": json.loads(full)["count"], "after_count": json.loads(after)["count"]},
                 full == after, "Window run must write only window-qualified outputs")
            wrong = pipe.run(date="2026-10-08", offline_fixtures_dir=fixtures,
                             reports_dir=root / "reports")
            emit("fixture_date_mismatch", {"date": wrong["date"], "events": wrong["events_count"]},
                 wrong["events_count"] == 0, "Replay must not relabel unmatched fixture dates")
            with patch("outlier_nfl.pipeline.validate_normalized_dataset", return_value=["invalid"]):
                invalid = pipe.run(date="2026-09-13", offline_fixtures_dir=fixtures,
                                   reports_dir=root / "reports")
            emit("validation_does_not_gate_publication", {"status": invalid["status"],
                 "errors": invalid["errors"]}, invalid["status"] != "OK",
                 "Invalid normalized data must not be published as successful")
            api = MagicMock()
            api.fetch_schedule.return_value = json.loads((fixtures / "schedule.json").read_text())
            api.fetch_event_markets.side_effect = RuntimeError("market unavailable")
            api.fetch_player_props.side_effect = RuntimeError("props unavailable")
            with patch("outlier_nfl.pipeline.load_external_metrics", return_value=[]), \
                 patch("outlier_nfl.pipeline.load_slate_weather", return_value={}), \
                 patch("outlier_nfl.pipeline.load_usage", return_value={}):
                failed = NflPipeline(client=api, data_dir=root / "failed").run(
                    date="2026-09-13", reports_dir=root / "reports")
            emit("ingestion_failure_success", {k: failed[k] for k in
                 ("status", "events_count", "game_lines_count", "player_props_count", "errors")},
                 failed["status"] != "OK", "Required fetch failures must invalidate the run")

    observations = []
    if args.data_root:
        normalized = args.data_root / "NFL" / "normalized"
        for day in ("2026-10-01", "2026-10-04", "2026-10-05"):
            payload = json.loads((normalized / f"nfl_best_bets_{day}.json").read_text(encoding="utf-8-sig"))
            valid = [r for r in payload["picks"] if r.get("verdict") == "VALIDATED"]
            bad = [r for r in valid if r.get("ev_per_unit", 0) <= 0]
            observations.append({"date": day, "validated": len(valid), "nonpositive_ev": len(bad),
                                 "examples": [{k: r.get(k) for k in
                                  ("player_name", "market", "position", "final_p", "best_odds", "ev_per_unit")}
                                  for r in bad[:5]]})
    output = {"mode": "verify_candidate" if args.verify_candidate else "observe_baseline",
              "probes": probes, "stored_card_observations": observations}
    text = json.dumps(output, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(args.verify_candidate and any(p["status"] != "REPAIR_VERIFIED" for p in probes))


if __name__ == "__main__":
    raise SystemExit(main())
