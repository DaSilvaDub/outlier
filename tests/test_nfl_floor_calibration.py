"""Offline provenance, final-only settlement, and chronological calibration tests."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from outlier_nfl.floor_calibration import (
    METHOD_VERSION,
    RANK_VERSION,
    build_calibration,
    collect_snapshots,
    fit_sigmoid,
    predict_shadow,
    run_offline_build,
    settle_snapshots,
)
from scripts.nfl_floor_calibration import main

NOW = datetime(2026, 12, 1, tzinfo=UTC)


def row(**changes):
    return {
        "event_id": "event-1", "player_id": "player-1", "team": "BAL", "opponent": "ATL",
        "market": "RUSH_YDS", "scope": "full_game", "position": "OVER", "line": 49.5,
        "event_starts_at": "2026-10-11T23:00:00+00:00",
        "ranking_score": 0.88, "ranking_score_version": RANK_VERSION,
        "quote_eligible": True, "target_book": "DRAFTKINGS", "target_odds": -150,
        "run_id": "run-1", "captured_at": "2026-10-11T22:00:00+00:00", **changes,
    }


def bundle(root: Path, records=None, *, run_id="run-1", context_changes=None, manifest_changes=None, payload_changes=None):
    run = root / run_id
    run.mkdir(parents=True, exist_ok=True)
    context = {
        "mode": "live", "replay": False, "live_inputs_postdate_as_of": False,
        "as_of_utc": "2026-10-11T22:00:00+00:00", "run_started_utc": "2026-10-11T22:00:00+00:00",
        **(context_changes or {}),
    }
    payload = {
        "run_id": run_id, "captured_at": context["as_of_utc"], "run_context": context,
        "calibration_records": records if records is not None else [row(run_id=run_id, captured_at=context["as_of_utc"])],
        **(payload_changes or {}),
    }
    data = json.dumps(payload).encode()
    artifact = run / "exports" / "nfl_alt_floors_2026-10-11.json"
    artifact.parent.mkdir()
    artifact.write_bytes(data)
    manifest = {
        "run_id": run_id, "status": "OK", "created_utc": context["run_started_utc"],
        "sealed_utc": (datetime.fromisoformat(context["as_of_utc"]).replace(tzinfo=UTC) + timedelta(minutes=1)).isoformat(),
        "context": context, "artifacts": [{"name": "exports/" + artifact.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        **(manifest_changes or {}),
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    return artifact


def outcome(**changes):
    return {**row(), "status": "verified_final", "actual": 50,
            "finalized_at": "2026-10-12T03:00:00+00:00", "source_id": "nflverse-final-1",
            "source_sha256": "a" * 64, **changes}


def outcomes_file(root: Path, records):
    path = root / "outcomes.json"
    path.write_text(json.dumps({"records": records}))
    return path


def test_sealed_pregame_snapshot_is_admitted(tmp_path):
    bundle(tmp_path)
    result = collect_snapshots(tmp_path)
    assert len(result["records"]) == 1
    assert result["problems"] == []
    assert len(result["records"][0]["manifest_sha256"]) == 64


def test_artifact_changed_after_manifest_is_rejected(tmp_path):
    artifact = bundle(tmp_path)
    artifact.write_bytes(artifact.read_bytes() + b" ")
    result = collect_snapshots(tmp_path)
    assert not result["records"]
    assert "mismatch" in result["problems"][0]["reason"]


@pytest.mark.parametrize("change", [{"mode": "replay"}, {"mode": "fixture"}, {"mode": "retrospective"}, {"replay": True}, {"live_inputs_postdate_as_of": True}, {"replay": None}, {"as_of_utc": "2026-10-11T22:00:00"}])
def test_replayed_late_unknown_or_naive_context_cannot_fit(tmp_path, change):
    bundle(tmp_path, context_changes=change)
    assert not collect_snapshots(tmp_path)["records"]


@pytest.mark.parametrize("change", [{"ranking_score_version": "unknown"}, {"ranking_score": None}, {"ranking_score": float("nan")}, {"ranking_score": 1.01}, {"player_id": ""}, {"event_id": ""}, {"scope": "first_half"}, {"line": 50}, {"line": 49.2}, {"position": "UNDER"}, {"team": "ATL"}, {"captured_at": "2026-10-11T22:10:00+00:00"}, {"event_starts_at": "2026-10-11T22:01:00+00:00"}])
def test_unsupported_score_identity_and_capture_are_refused(tmp_path, change):
    bundle(tmp_path, [row(**change)])
    assert not collect_snapshots(tmp_path)["records"]


def test_export_context_must_match_sealed_manifest(tmp_path):
    bundle(tmp_path, payload_changes={"run_id": "another-run"})
    assert collect_snapshots(tmp_path)["problems"][0]["reason"] == "export_provenance_mismatch"


def test_bundle_finished_after_kickoff_is_not_pregame(tmp_path):
    bundle(tmp_path, manifest_changes={"sealed_utc": "2026-10-12T00:00:00+00:00"})
    assert not collect_snapshots(tmp_path)["records"]


@pytest.mark.parametrize("sealed", [None, "2026-10-11T22:01:00", "2026-10-11T21:59:00+00:00"])
def test_unknown_naive_or_backdated_seal_is_not_fit_evidence(tmp_path, sealed):
    bundle(tmp_path, manifest_changes={"sealed_utc": sealed})
    assert not collect_snapshots(tmp_path)["records"]


@pytest.mark.parametrize("change", [{"quote_eligible": False}, {"quote_eligible": None}, {"quote_eligible": "true"}, {"target_odds": None}, {"target_odds": float("nan")}, {"target_odds": -50}, {"target_book": ""}, {"target_book": "UNKNOWN"}, {"quote_eligible": False, "inventory_reason": "inactive"}])
def test_unquoted_or_inactive_inventory_does_not_enter_fit_population(tmp_path, change):
    bundle(tmp_path, [row(**change)])
    result = collect_snapshots(tmp_path)
    assert result["inventory_rows"] == 1
    assert not result["records"]
    assert result["problems"][0]["reason"] == "quote_ineligible_for_training"


def test_missing_quote_eligibility_is_inventory_only(tmp_path):
    record = row()
    record.pop("quote_eligible")
    bundle(tmp_path, [record])
    assert not collect_snapshots(tmp_path)["records"]


def test_legacy_scores_are_inventory_only(tmp_path):
    bundle(tmp_path, payload_changes={"calibration_records": None, "master_pool": [{"confidence_score": 0.9}]})
    result = collect_snapshots(tmp_path)
    assert not result["records"]
    assert result["inventory_rows"] == 1
    assert result["problems"][0]["reason"] == "legacy_missing_calibration_records"


def test_reruns_and_books_collapse_to_latest_exact_target(tmp_path):
    bundle(tmp_path, [row(target_book="DRAFTKINGS"), row(target_book="FANDUEL")])
    bundle(tmp_path, [row(run_id="run-2", captured_at="2026-10-11T22:00:30+00:00", ranking_score=0.9)], run_id="run-2", context_changes={"as_of_utc": "2026-10-11T22:00:30+00:00"})
    result = collect_snapshots(tmp_path)
    assert len(result["records"]) == 1
    assert result["records"][0]["ranking_score"] == 0.9
    assert result["admitted_rows_before_deduplication"] == 3


def test_conflicting_latest_score_and_event_identity_are_quarantined(tmp_path):
    bundle(tmp_path, [row(), row(ranking_score=0.8)])
    assert collect_snapshots(tmp_path)["problems"][0]["reason"] == "conflicting_score_at_latest_capture"
    other = tmp_path / "different"
    bundle(other, [row(), row(player_id="player-2", opponent="BUF")])
    assert not collect_snapshots(other)["records"]


def test_escape_and_duplicate_artifact_indexes_are_refused(tmp_path):
    artifact = bundle(tmp_path)
    manifest_path = artifact.parents[1] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"].append(manifest["artifacts"][0])
    manifest_path.write_text(json.dumps(manifest))
    assert collect_snapshots(tmp_path)["problems"][0]["reason"] == "ambiguous_or_escaped_artifact"


def test_latest_alias_does_not_duplicate_inventory_and_legacy_records_count(tmp_path):
    artifact = bundle(tmp_path, payload_changes={"calibration_records": None, "records": [{"confidence_score": 0.9}, {"confidence_score": 0.8}]})
    alias = artifact.with_name("nfl_alt_floors_latest.json")
    alias.write_bytes(artifact.read_bytes())
    manifest_path = artifact.parents[1] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"].append({**manifest["artifacts"][0], "name": "exports/" + alias.name})
    manifest_path.write_text(json.dumps(manifest))
    result = collect_snapshots(tmp_path)
    assert result["inventory_rows"] == 2
    assert len(result["problems"]) == 1


@pytest.mark.parametrize("changes", [{"actual": None}, {"actual": float("nan")}, {"actual": 49.5}, {"status": "in_progress"}, {"source_id": ""}, {"source_sha256": "bad"}, {"finalized_at": "2026-10-11T23:00:00+00:00"}, {"finalized_at": "2027-01-01T00:00:00+00:00"}, {"finalized_at": "2026-10-12T03:00:00"}])
def test_unverified_missing_or_future_actual_is_never_a_loss(tmp_path, changes):
    result = settle_snapshots([row()], outcomes_file(tmp_path, [outcome(**changes)]), as_of=NOW)
    assert not result["records"]


def test_exact_outcome_join_and_negative_yards_settlement(tmp_path):
    rows = [row(), row(player_id="second", line=0.5, market="REC_YDS")]
    result = settle_snapshots(rows, outcomes_file(tmp_path, [outcome(), outcome(player_id="second", market="REC_YDS", line=0.5, actual=-2)]), as_of=NOW)
    assert [r["result"] for r in result["records"]] == ["W", "L"]
    assert result["records"][1]["actual"] == -2


@pytest.mark.parametrize("changes", [{"line": 50.5}, {"player_id": "other"}, {"opponent": "BUF"}, {"event_starts_at": "2026-10-11T23:30:00+00:00"}])
def test_no_fuzzy_or_different_target_outcome_join(tmp_path, changes):
    result = settle_snapshots([row()], outcomes_file(tmp_path, [outcome(**changes)]), as_of=NOW)
    assert not result["records"]


def test_conflicting_finals_fail_closed(tmp_path):
    result = settle_snapshots([row()], outcomes_file(tmp_path, [outcome(), outcome(actual=49)]), as_of=NOW)
    assert not result["records"]
    assert result["problems"][0]["reason"] == "conflicting_verified_outcomes"


def synthetic():
    """Eight weeks, ten independent games/week, score inflated by 25 points."""
    data = []
    start = datetime(2026, 9, 6, 17, tzinfo=UTC)
    for week in range(8):
        for game in range(10):
            kickoff = start + timedelta(weeks=week, minutes=game)
            score = 0.6 if game < 5 else 0.9
            y = int(game in {0, 1, 5, 6, 7})
            data.append(row(event_id=f"w{week}g{game}", ranking_score=score,
                            event_starts_at=kickoff.isoformat(), captured_at=(kickoff - timedelta(hours=1)).isoformat(),
                            finalized_at=(kickoff + timedelta(hours=4)).isoformat(), y=y))
    return data


def test_synthetic_miscalibration_fit_improves_later_heldout_brier_and_logloss():
    artifact = build_calibration(synthetic(), as_of=NOW)
    family = artifact["families"]["RUSH_YDS"]
    assert family["training_events"] == 60
    assert family["holdout_events"] == 20
    assert family["coefficients"]["slope"] > 0
    assert family["holdout_metrics"]["brier"] < family["ranking_score_diagnostic_metrics"]["brier"]
    assert family["holdout_metrics"]["logloss"] < family["ranking_score_diagnostic_metrics"]["logloss"]
    low = predict_shadow(artifact, "RUSH_YDS", 0.6)["shadow_probability"]
    high = predict_shadow(artifact, "RUSH_YDS", 0.9)["shadow_probability"]
    assert 0.35 < low < 0.46
    assert 0.54 < high < 0.65
    assert artifact["approved_for_betting"] is False
    assert artifact["publication_status"] == "SHADOW_ONLY"


def test_holdout_labels_do_not_change_training_coefficients_or_cross_whole_weeks():
    rows = synthetic()
    first = build_calibration(rows, as_of=NOW)["families"]["RUSH_YDS"]
    changed = [{**r, "y": 1 - r["y"]} if r["event_id"].startswith(("w6", "w7")) else r for r in rows]
    second = build_calibration(changed, as_of=NOW)["families"]["RUSH_YDS"]
    assert first["coefficients"] == second["coefficients"]
    assert set(first["training_event_ids"]).isdisjoint(first["holdout_event_ids"])
    assert set(first["training_weeks"]).isdisjoint(first["holdout_weeks"])
    assert first["holdout_metrics"] != second["holdout_metrics"]


def test_utc_monday_snf_stays_in_same_eastern_sunday_slate_partition():
    rows = synthetic()
    night = dict(rows[50], event_id="late-snf", event_starts_at="2026-10-12T00:20:00+00:00", captured_at="2026-10-11T23:20:00+00:00", finalized_at="2026-10-12T04:20:00+00:00")
    result = build_calibration([*rows, night], as_of=NOW)["families"]["RUSH_YDS"]
    assert "late-snf" in result["training_event_ids"]
    assert "w5g0" in result["training_event_ids"]
    assert "w6g0" in result["holdout_event_ids"]


def test_outcome_unknown_at_holdout_capture_is_removed_from_training():
    rows = synthetic()
    rows[0]["finalized_at"] = rows[70]["finalized_at"]
    result = build_calibration(rows, as_of=NOW)["families"]["RUSH_YDS"]
    assert result["training_events"] == 59
    assert "w0g0" not in result["training_event_ids"]


def test_per_event_balancing_avoids_repeated_player_weight_inflation():
    rows = synthetic()[:40]
    original = fit_sigmoid(rows)
    repeated = [dict(r, player_id=f"{r['player_id']}-{index}") for r in rows for index in range(4 if r["y"] else 1)]
    fitted = fit_sigmoid(repeated)
    assert fitted["intercept"] == pytest.approx(original["intercept"], abs=1e-6)
    assert fitted["slope"] == pytest.approx(original["slope"], abs=1e-6)


def test_inverse_score_relationship_yields_nonnegative_monotone_fit():
    rows = [dict(r, y=int(r["ranking_score"] == 0.6)) for r in synthetic()]
    coefficients = fit_sigmoid(rows)
    assert coefficients["slope"] == 0
    assert math.isfinite(coefficients["intercept"])


def test_family_support_unknown_version_and_out_of_support_withhold():
    artifact = build_calibration(synthetic(), as_of=NOW)
    assert predict_shadow(artifact, "PASS_YDS", 0.8)["shadow_probability"] is None
    assert predict_shadow(artifact, "RUSH_YDS", 0.95)["method_status"] == "OUT_OF_TRAINING_SUPPORT"
    assert predict_shadow({**artifact, "method_version": "unknown"}, "RUSH_YDS", 0.8)["shadow_probability"] is None
    assert predict_shadow({**artifact, "publication_status": "APPROVED"}, "RUSH_YDS", 0.8)["shadow_probability"] is None
    assert artifact["method_version"] == METHOD_VERSION


def test_malformed_shadow_artifact_withholds_without_exception():
    artifact = build_calibration(synthetic(), as_of=NOW)
    artifact["families"]["RUSH_YDS"]["coefficients"]["slope"] = float("nan")
    assert predict_shadow(artifact, "RUSH_YDS", 0.8)["shadow_probability"] is None
    artifact["families"]["RUSH_YDS"].pop("score_support")
    assert predict_shadow(artifact, "RUSH_YDS", 0.8)["shadow_probability"] is None


@pytest.mark.parametrize("artifact", [None, [], {"families": None}, {"families": []}, {"families": {"RUSH_YDS": []}}, {"families": {"RUSH_YDS": None}}])
def test_malformed_model_container_shapes_withhold_unavailable(artifact):
    result = predict_shadow(artifact, "RUSH_YDS", 0.8)
    assert result["method_status"] == "UNAVAILABLE"
    assert result["shadow_probability"] is None


def test_sparse_or_one_class_data_has_no_fitted_probability():
    assert build_calibration(synthetic()[:18], as_of=NOW)["status"] == "DATA_INSUFFICIENT"
    assert build_calibration([dict(r, y=1) for r in synthetic()], as_of=NOW)["families"]["RUSH_YDS"]["coefficients"] is None


def test_baseline_only_compares_exact_target_available_before_capture():
    rows = synthetic()
    for record in rows:
        record.update(baseline_probability=0.5, baseline_identity=dict(record), baseline_source_id="pair", baseline_source_sha256="b" * 64, baseline_captured_at=record["captured_at"])
    rows[-1]["baseline_identity"]["line"] = 50.5
    rows[-2]["baseline_captured_at"] = rows[-2]["finalized_at"]
    result = build_calibration(rows, as_of=NOW)["families"]["RUSH_YDS"]
    assert result["exact_pair_baseline_metrics"]["rows"] == 18
    assert result["model_on_baseline_subset_metrics"]["rows"] == 18


def test_reliability_uncertainty_counts_events_not_players():
    rows = [dict(r, player_id=f"{r['player_id']}-{i}") for r in synthetic() for i in range(3)]
    result = build_calibration(rows, as_of=NOW)["families"]["RUSH_YDS"]
    assert result["holdout_metrics"]["independent_events"] == 20
    assert result["holdout_metrics"]["rows"] == 60
    assert sum(b["independent_events"] for b in result["reliability_bins"]) == 20
    assert all(b.get("interval_method", "").endswith("independent_events") for b in result["reliability_bins"] if b["rows"])


def test_offline_cold_start_writes_only_requested_outputs_without_archive_mutation(tmp_path):
    runs = tmp_path / "runs"
    bundle(runs)
    before = {str(p): p.read_bytes() for p in runs.rglob("*") if p.is_file()}
    artifact = run_offline_build(runs, tmp_path / "research", None, as_of=NOW)
    assert artifact["status"] == "DATA_INSUFFICIENT"
    assert (tmp_path / "research" / "floor_calibration_report.md").is_file()
    assert before == {str(p): p.read_bytes() for p in runs.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="outside immutable"):
        run_offline_build(runs, runs / "output", None, as_of=NOW)


def test_cli_sealed_synthetic_capture_settlement_and_fit_end_to_end(tmp_path, capsys):
    runs = tmp_path / "runs"
    finals = []
    for index, record in enumerate(synthetic()):
        run_id = f"capture-{index}"
        record = {**record, "run_id": run_id}
        captured = datetime.fromisoformat(record["captured_at"])
        bundle(runs, [record], run_id=run_id,
               context_changes={"as_of_utc": record["captured_at"], "run_started_utc": record["captured_at"]},
               manifest_changes={"created_utc": (captured + timedelta(minutes=1)).isoformat()})
        finals.append(outcome(**record, actual=100 if record["y"] else 0))
    path = outcomes_file(tmp_path, finals)
    output = tmp_path / "results"
    assert main(["--runs-dir", str(runs), "--output-dir", str(output), "--outcomes", str(path), "--as-of", NOW.isoformat()]) == 0
    assert "SHADOW_FITTED" in capsys.readouterr().out
    artifact = json.loads((output / "floor_calibration_shadow.json").read_text())
    assert artifact["families"]["RUSH_YDS"]["holdout_events"] == 20
    assert artifact["approved_for_betting"] is False
    assert len(artifact["outcomes_sha256"]) == 64
    with pytest.raises(ValueError, match="outside immutable"):
        main(["--runs-dir", str(runs), "--output-dir", str(runs / "nested"), "--as-of", NOW.isoformat()])
