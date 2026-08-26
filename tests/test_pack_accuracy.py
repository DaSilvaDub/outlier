import csv
import json
import sqlite3
from pathlib import Path

import pytest

from outlier_scrapers import feedback, pack_accuracy, parlay_legs
from outlier_scrapers.alt_player_props import (
    ALT_PLAYER_PROPS_HEADER,
    ALT_PLAYER_PROPS_PARLAYS_HEADER,
)
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
from outlier_scrapers.pack import CANDIDATES_HEADER


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _candidate(
    *,
    market_id: str,
    outcome_id: str,
    selection: str,
    market_type: str = "PLAYER_PROP",
    model_prob: float = 0.60,
    line: float = 5.5,
) -> dict:
    row = {field: "" for field in CANDIDATES_HEADER}
    row.update(
        {
            "sport": "MLB",
            "event_id": "e1",
            "market_id": market_id,
            "outcome_id": outcome_id,
            "market_type": market_type,
            "selection": selection,
            "line": line,
            "price": -110,
            "decimal_price": 1.9091,
            "book": "DK",
            "as_of": "2026-08-25T16:00:00+00:00",
            "model_prob": model_prob,
            "model_prob_source": "outlier_devig",
            "market_consensus_prob": model_prob,
            "final_blended_prob": model_prob,
            "push_prob": 0.0,
            "implied_prob": 0.5238,
            "edge_pct": 0.08,
            "recommended_units_pre_news": 1.0,
            "board": "A",
            "actionable": "true",
            "selected": "true",
        }
    )
    return row


def _game_total(*, market_id: str = "gt1", outcome_id: str = "gto1") -> dict:
    row = {field: "" for field in GAME_TOTALS_HEADER}
    row.update(
        {
            "sport": "MLB",
            "league": "MLB",
            "event_id": "e1",
            "market_id": market_id,
            "outcome_id": outcome_id,
            "total_kind": "game",
            "selection": "AWY @ HME Total O/U OVER 8.5",
            "line": 8.5,
            "best_side": "OVER",
            "best_price": -105,
            "decimal_price": 1.9524,
            "projected_over_prob": 0.58,
            "projected_under_prob": 0.42,
            "as_of": "2026-08-25T16:00:00+00:00",
            "book": "DK",
        }
    )
    return row


def _pack_dir(tmp_path: Path) -> Path:
    pack_dir = tmp_path / "packs" / "2026-08-25"
    candidates = [
        _candidate(
            market_id=f"m{index}",
            outcome_id=f"o{index}",
            selection=f"Pitcher {index} - Strikeouts OVER 5.5",
        )
        for index in range(1, 4)
    ]
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, candidates)
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], candidates)
    _write_csv(pack_dir / "game_totals.csv", GAME_TOTALS_HEADER, [_game_total()])
    def _alt(index: int, event: str) -> dict:
        row = {field: "" for field in ALT_PLAYER_PROPS_HEADER}
        row.update(
            {
                "league": "MLB",
                "event_id": event,
                "matchup": "AWY @ HME",
                "market_id": f"alt{index}",
                "outcome_id": f"alto{index}",
                "player": f"Pitcher {index}",
                "market": "SO",
                "position": "OVER",
                "line": 4.5,
                "best_book": "DK",
                "best_odds": -140,
                "model_prob": 0.72,
                "recommended_units": 1.0,
            }
        )
        return row

    alt_rows = [_alt(1, "e1"), _alt(2, "e2")]
    _write_csv(pack_dir / "alt_player_props.csv", ALT_PLAYER_PROPS_HEADER, alt_rows)
    parlay = {field: "" for field in ALT_PLAYER_PROPS_PARLAYS_HEADER}
    parlay.update(
        {
            "league": "MLB",
            "type": "cross_game",
            "leg_1_player": "Pitcher 1",
            "leg_2_player": "Pitcher 2",
            "parlay_odds": "+250",
            "legs_json": parlay_legs.encode_legs(alt_rows),
        }
    )
    _write_csv(
        pack_dir / "alt_player_props_parlays.csv", ALT_PLAYER_PROPS_PARLAYS_HEADER, [parlay]
    )
    return pack_dir


def _settle(db_path: Path, results: dict[str, str]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT d.decision_id, s.market_id, s.event_id FROM decisions d "
                "JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id"
            )
        ]
    settlements = []
    for row in rows:
        outcome = results.get(row["market_id"])
        if outcome is None:
            continue
        settlement = {field: "" for field in feedback.SETTLEMENT_FIELDS}
        settlement.update(
            {
                "decision_id": row["decision_id"],
                "event_id": row["event_id"],
                "market_id": row["market_id"],
                "actual_result": "7",
                "win_loss_push": outcome,
                "would_have_result": outcome,
            }
        )
        settlements.append(settlement)
    feedback.import_settlements(db_path, settlements)


def test_inventory_prefers_opportunities_over_candidates(tmp_path):
    pack_dir = _pack_dir(tmp_path)

    counts = pack_accuracy.inventory_pack_lanes(pack_dir)

    assert "candidates.csv" not in counts
    assert counts["opportunities.csv"] == 3
    assert counts["game_totals.csv"] == 1
    assert counts["alt_player_props.csv"] == 2
    assert counts["alt_player_props_parlays.csv"] == 1


def test_audit_reports_accuracy_per_pack_type(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle(db_path, {"m1": "W", "m2": "L", "m3": "W", "gt1": "L"})

    audit = pack_accuracy.audit_pack(pack_dir, db_path, min_samples=1)

    by_type = {row["pack_type"]: row for row in audit.pack_types}
    assert set(by_type) >= {"PLAYER_PROP", "GAME_TOTAL"}
    assert by_type["PLAYER_PROP"]["graded"] == 3
    assert by_type["PLAYER_PROP"]["wins"] == 2
    assert by_type["PLAYER_PROP"]["losses"] == 1
    assert by_type["PLAYER_PROP"]["hit_rate"] == pytest.approx(2 / 3)
    assert by_type["GAME_TOTAL"]["graded"] == 1
    assert by_type["GAME_TOTAL"]["losses"] == 1


def test_every_generated_lane_including_parlays_is_captured(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    audit = pack_accuracy.audit_pack(pack_dir, db_path)

    lanes = {lane.file: lane for lane in audit.lanes}
    assert lanes["alt_player_props.csv"].captured_rows == 2
    assert lanes["alt_player_props.csv"].coverage_state == pack_accuracy.STATUS_NO_SETTLEMENTS
    assert lanes["opportunities.csv"].coverage_state == pack_accuracy.STATUS_NO_SETTLEMENTS
    assert lanes["alt_player_props_parlays.csv"].captured_rows == 1
    assert not audit.warnings


def test_a_lane_with_no_capture_path_is_reported_as_a_coverage_gap(tmp_path, monkeypatch):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    monkeypatch.setitem(pack_accuracy.LANE_FILES, "alt_player_props.csv", None)

    audit = pack_accuracy.audit_pack(pack_dir, db_path)

    lanes = {lane.file: lane for lane in audit.lanes}
    assert lanes["alt_player_props.csv"].coverage_state == pack_accuracy.STATUS_NO_COVERAGE
    assert any("alt_player_props.csv" in warning for warning in audit.warnings)


def test_alt_player_prop_rows_are_graded_under_their_own_pack_type(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle(db_path, {"alt1": "W"})

    audit = pack_accuracy.audit_pack(pack_dir, db_path, min_samples=1)

    by_type = {row["pack_type"]: row for row in audit.pack_types}
    assert by_type["ALT_PLAYER_PROP"]["graded"] == 1
    assert by_type["ALT_PLAYER_PROP"]["wins"] == 1


def test_uncaptured_pack_warns_instead_of_reporting_a_clean_sheet(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)

    audit = pack_accuracy.audit_pack(pack_dir, db_path)

    assert audit.captured_rows == 0
    assert audit.settled_rows == 0
    assert any("never captured" in warning for warning in audit.warnings)


def test_pack_type_attribution_survives_recapture_by_a_later_pack(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    later = tmp_path / "packs" / "2026-08-26"
    later.mkdir(parents=True, exist_ok=True)
    for name in ("candidates.csv", "opportunities.csv", "game_totals.csv"):
        (later / name).write_text((pack_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
    feedback.capture_pack(later, db_path)
    _settle(db_path, {"m1": "W", "gt1": "W"})

    audit = pack_accuracy.audit_pack(pack_dir, db_path, min_samples=1)

    by_type = {row["pack_type"]: row for row in audit.pack_types}
    assert by_type["GAME_TOTAL"]["graded"] == 1
    assert by_type["PLAYER_PROP"]["graded"] == 1


def test_assess_flags_an_overconfident_lane():
    status, reason = pack_accuracy.assess(
        {"graded": 40, "hit_rate": 0.45, "expected_hit_rate": 0.60, "roi": -0.2},
        min_samples=20,
    )
    assert status == pack_accuracy.STATUS_NEEDS_ADJUSTMENT
    assert "overconfident" in reason


def test_assess_holds_an_on_track_lane():
    status, _reason = pack_accuracy.assess(
        {"graded": 40, "hit_rate": 0.58, "expected_hit_rate": 0.60, "roi": 0.05},
        min_samples=20,
    )
    assert status == pack_accuracy.STATUS_ON_TRACK


def test_assess_refuses_to_read_a_thin_sample():
    status, reason = pack_accuracy.assess(
        {"graded": 3, "hit_rate": 0.0, "expected_hit_rate": 0.60}, min_samples=20
    )
    assert status == pack_accuracy.STATUS_INSUFFICIENT_DATA
    assert "3 settled rows" in reason


def test_cli_writes_the_report_and_can_fail_on_a_coverage_gap(tmp_path, capsys):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    output = tmp_path / "report"

    exit_code = pack_accuracy.main(
        [
            "--pack",
            str(pack_dir),
            "--db",
            str(db_path),
            "--output",
            str(output),
            "--fail-on-gap",
        ]
    )

    # Every gradeable lane in this pack is captured, so there is no gap to fail on.
    assert exit_code == 0
    assert (output / "report.md").exists()
    assert (output / "pack_type_accuracy.csv").exists()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["pack_label"] == "2026-08-25"
    assert summary["captured_rows"] == 7
    assert "Pack accuracy - 2026-08-25" in capsys.readouterr().out


def test_report_exposes_pack_type_breakdown(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle(db_path, {"m1": "W", "gt1": "L"})

    output_dir = feedback.generate_report(db_path, tmp_path / "reports")

    with (output_dir / "pack_type.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["pack_type"]: row for row in csv.DictReader(handle)}
    assert set(rows) == {"PLAYER_PROP", "GAME_TOTAL"}
    assert rows["PLAYER_PROP"]["wins"] == "1"
    assert rows["GAME_TOTAL"]["losses"] == "1"


def test_pack_lane_join_never_reads_retention_slimmed_columns():
    """Retention clears these columns on the premise no report reads them.

    ``_joined_rows`` gained a pack-membership join for pack-type reporting; the
    lane must come from ``pack_snapshot_memberships`` (which retention leaves
    intact) rather than the snapshot columns retention is free to blank.
    """

    import inspect

    source = inspect.getsource(feedback._joined_rows)
    for column in feedback._RETENTION_SLIMMED_COLUMNS:
        assert f"s.{column}" not in source, f"_joined_rows must not select s.{column}"


def test_parlay_lanes_report_under_their_own_pack_type(tmp_path):
    """Parlays settle from their legs, so they are measured like any lane."""

    assert not [
        filename
        for filename, source in pack_accuracy.LANE_FILES.items()
        if source is None
    ], "every generated lane should now have a capture path"
    for source, pack_type in feedback.PACK_SOURCE_TYPES.items():
        if source.endswith("parlays"):
            assert pack_type.startswith("PARLAY_")


def test_a_settled_parlay_is_audited_under_its_parlay_pack_type(tmp_path):
    pack_dir = _pack_dir(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle(db_path, {"alt1": "W", "alt2": "W"})
    assert feedback.settle_parlays(db_path).settled == 1

    audit = pack_accuracy.audit_pack(pack_dir, db_path, min_samples=1)

    by_type = {row["pack_type"]: row for row in audit.pack_types}
    assert by_type["ALT_PLAYER_PROP"]["graded"] == 2
    assert by_type["PARLAY_PLAYER_PROP"]["graded"] == 1
    assert by_type["PARLAY_PLAYER_PROP"]["wins"] == 1
    lanes = {lane.file: lane for lane in audit.lanes}
    assert lanes["alt_player_props_parlays.csv"].settled_rows == 1
