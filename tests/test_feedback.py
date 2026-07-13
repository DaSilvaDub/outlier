import csv
import sqlite3
from pathlib import Path

import pytest

from outlier_scrapers import feedback, pack
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
from outlier_scrapers.pack import CANDIDATES_HEADER


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _candidate(
    *,
    market_id: str = "m1",
    outcome_id: str = "o1",
    line: float = 10.5,
    selected: bool = True,
    actionable: bool = True,
) -> dict:
    row = {field: "" for field in CANDIDATES_HEADER}
    row.update(
        {
            "sport": "WNBA",
            "event_id": "e1",
            "market_id": market_id,
            "outcome_id": outcome_id,
            "market_type": "PLAYER_PROP",
            "player_id": "p1",
            "selection": f"Player Points OVER {line}",
            "line": line,
            "price": 100,
            "decimal_price": 2.0,
            "book": "DK",
            "as_of": "2026-07-13T16:00:00+00:00",
            "model_prob": 0.60,
            "model_prob_source": "outlier_devig",
            "market_consensus_prob": 0.60,
            "independent_model_prob": "",
            "final_blended_prob": 0.60,
            "push_prob": 0.0,
            "implied_prob": 0.50,
            "edge_pct": 0.10,
            "recommended_units_pre_news": 2.0,
            "data_quality_flags": "",
            "board": "A" if actionable else "B",
            "signal_flags": "hit_rate_support;movement_support",
            "hit_rate_component": 65.0,
            "insight_component": 55.0,
            "movement_component": 75.0,
            "orf_component": 60.0,
            "actionable": "true" if actionable else "false",
            "selected": "true" if selected else "false",
        }
    )
    return row


def _pack(tmp_path: Path, rows: list[dict]) -> Path:
    pack_dir = tmp_path / "packs" / "2026-07-13"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [row for row in rows if row["selected"] == "true"])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], rows)
    return pack_dir


def test_capture_pack_is_idempotent_and_keeps_unselected_signal_features(tmp_path):
    selected = _candidate()
    unselected = _candidate(
        market_id="m2", outcome_id="o2", line=11.5, selected=False, actionable=False
    )
    pack_dir = _pack(tmp_path, [selected, unselected])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"

    first = feedback.capture_pack(pack_dir, db_path)
    second = feedback.capture_pack(pack_dir, db_path)

    assert first == feedback.CaptureStats(snapshots=2, decisions=2)
    assert second == first
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2
        audit = conn.execute(
            "SELECT selected, hit_rate_component, movement_component "
            "FROM market_snapshots WHERE market_id = 'm2'"
        ).fetchone()
        assert dict(audit) == {
            "selected": 0,
            "hit_rate_component": 65.0,
            "movement_component": 75.0,
        }
        verdicts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT s.market_id, d.pipeline_verdict FROM decisions d "
                "JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id"
            )
        }
        assert verdicts == {"m1": "PLAY", "m2": "STAND_DOWN"}

    decision_rows = _read_csv(pack_dir / "decisions.csv")
    assert len(decision_rows) == 2
    assert list(decision_rows[0]) == feedback.DECISION_FIELDS


def test_pack_main_captures_feedback_by_default(tmp_path, monkeypatch):
    row = _candidate()

    def fake_build(_leagues, _date, _top_ev, _top_signal, *, opportunity_rows_out=None):
        assert opportunity_rows_out is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues: [])
    monkeypatch.setattr(pack.paths, "PROJECT_ROOT", tmp_path)

    out_dir = pack.main(["--leagues", "WNBA"])

    assert out_dir == tmp_path / "packs" / "2026-07-13"
    assert (out_dir / "opportunities.csv").exists()
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_pack_main_does_not_publish_when_feedback_capture_fails(tmp_path, monkeypatch):
    row = _candidate()
    out_dir = tmp_path / "packs" / "2026-07-13"
    out_dir.mkdir(parents=True)
    (out_dir / "keep-me.txt").write_text("previous published pack", encoding="utf-8")
    (out_dir / "candidates.csv").write_text("old pack", encoding="utf-8")

    def fake_build(_leagues, _date, _top_ev, _top_signal, *, opportunity_rows_out=None):
        assert opportunity_rows_out is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues: [])
    monkeypatch.setattr(pack.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        feedback,
        "capture_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(feedback.FeedbackError("locked")),
    )

    with pytest.raises(feedback.FeedbackError, match="locked"):
        pack.main(["--leagues", "WNBA"])

    assert (out_dir / "keep-me.txt").read_text(encoding="utf-8") == "previous published pack"
    assert (out_dir / "candidates.csv").read_text(encoding="utf-8") == "old pack"
    assert not list((tmp_path / "packs").glob(".*.feedback-staging-*"))


def test_pack_swap_failure_rolls_back_ledger_and_restores_published_pack(tmp_path, monkeypatch):
    row = _candidate()
    out_dir = tmp_path / "packs" / "2026-07-13"
    out_dir.mkdir(parents=True)
    (out_dir / "candidates.csv").write_text("old pack", encoding="utf-8")

    def fake_build(_leagues, _date, _top_ev, _top_signal, *, opportunity_rows_out=None):
        assert opportunity_rows_out is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues: [])
    monkeypatch.setattr(pack.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        pack,
        "_swap_staged_pack",
        lambda *_args: (_ for _ in ()).throw(OSError("swap failed")),
    )

    with pytest.raises(OSError, match="swap failed"):
        pack.main(["--leagues", "WNBA"])

    assert (out_dir / "candidates.csv").read_text(encoding="utf-8") == "old pack"
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_capture_preserves_selected_total_alternate_not_represented_by_specialized_board(tmp_path):
    represented = _candidate(market_id="m1", outcome_id="o1", line=10.5)
    alternate = _candidate(market_id="m1", outcome_id="o2", line=11.5)
    pack_dir = _pack(tmp_path, [represented, alternate])
    total = {field: "" for field in GAME_TOTALS_HEADER}
    total.update(
        {
            "totals_id": "m1:10.5:OVER",
            "sport": "WNBA",
            "event_id": "e1",
            "market_id": "m1",
            "outcome_id": "m1:10.5:OVER",
            "total_kind": "game",
            "selection": "Player Points OVER 10.5",
            "line": "10.5",
            "price": "100",
            "decimal_price": "2.0",
            "book": "FD",
            "best_side": "OVER",
            "projected_over_prob": "0.60",
            "projected_under_prob": "0.40",
            "market_consensus_prob": "0.60",
            "final_blended_prob": "0.60",
            "edge_pct": "0.10",
            "implied_prob": "0.50",
            "actionable": "true",
            "recommended_units_pre_news": "2",
            "push_prob": "0",
            "as_of": "2026-07-13T16:00:00+00:00",
        }
    )
    _write_csv(pack_dir / "game_totals.csv", GAME_TOTALS_HEADER, [total])
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT outcome_id, line FROM market_snapshots ORDER BY line"
        ).fetchall()
    assert rows == [("m1:10.5:OVER", "10.5"), ("o2", "11.5")]


def test_new_market_snapshot_gets_its_own_decision(tmp_path):
    row = _candidate()
    pack_dir = _pack(tmp_path, [row])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    row["as_of"] = "2026-07-13T16:05:00+00:00"
    row["price"] = "110"
    row["decimal_price"] = "2.1"
    _pack(tmp_path, [row])
    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2


def test_schema_v1_decision_and_push_mass_are_migrated_on_recapture(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    with sqlite3.connect(db_path) as conn:
        snapshot_id = conn.execute("SELECT snapshot_id FROM market_snapshots").fetchone()[0]
        conn.execute("UPDATE decisions SET decision_id = 'legacy-decision'")
        conn.execute("UPDATE market_snapshots SET push_prob = NULL")
        conn.execute("PRAGMA user_version = 1")

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        decisions = conn.execute("SELECT decision_id, snapshot_id FROM decisions").fetchall()
        push_prob = conn.execute("SELECT push_prob FROM market_snapshots").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert decisions == [(feedback._stable_id("decision", snapshot_id), snapshot_id)]
    assert push_prob == pytest.approx(0.0)
    assert version == feedback.SCHEMA_VERSION


def test_settlement_computes_clv_pnl_and_all_requested_reports(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    decisions = _read_csv(pack_dir / "decisions.csv")
    decisions[0].update(
        {
            "A_verdict": "BET",
            "B_verdict": "CONFIRMS",
            "C_verdict": "NEUTRAL",
            "D_verdict": "BET",
            "final_verdict": "PLAY",
            "units": "2",
        }
    )
    decision_input = tmp_path / "filled_decisions.csv"
    _write_csv(decision_input, feedback.DECISION_FIELDS, decisions)
    assert feedback.import_decisions(decision_input, db_path).imported == 1

    settlement = {field: "" for field in feedback.SETTLEMENT_FIELDS}
    settlement.update(
        {
            "decision_id": decisions[0]["decision_id"],
            "event_id": "e1",
            "market_id": "m1",
            "actual_result": "14",
            "win_loss_push": "W",
            "closing_line": "11.5",
            "closing_price": "-110",
            "would_have_result": "W",
        }
    )
    settlement_input = tmp_path / "settlements.csv"
    _write_csv(settlement_input, feedback.SETTLEMENT_FIELDS, [settlement])
    stats = feedback.import_settlements(settlement_input, db_path)
    assert stats == feedback.ImportStats(imported=1, unlinked=0)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT clv_line, clv_price, pnl FROM settlements"
        ).fetchone()
    assert row[0] == pytest.approx(1.0)
    assert row[1] == pytest.approx(2.0 / (1.0 + 100.0 / 110.0) - 1.0)
    assert row[2] == pytest.approx(2.0)

    report_dir = feedback.generate_report(db_path, tmp_path / "report")
    expected_files = {
        "probability_metrics.csv",
        "calibration_curves.csv",
        "expected_vs_actual.csv",
        "market_type.csv",
        "edge_buckets.csv",
        "odds_ranges.csv",
        "books.csv",
        "leagues.csv",
        "signal_flags.csv",
        "play_vs_stand_down.csv",
        "model_performance.csv",
        "summary.json",
        "report.md",
    }
    assert expected_files.issubset({path.name for path in report_dir.iterdir()})

    probability = {
        row["probability_source"]: row
        for row in _read_csv(report_dir / "probability_metrics.csv")
    }
    assert int(probability["market_consensus"]["n"]) == 1
    assert float(probability["market_consensus"]["brier_score"]) == pytest.approx(0.16)
    assert int(probability["independent_model"]["n"]) == 0
    market = _read_csv(report_dir / "market_type.csv")[0]
    assert float(market["profit"]) == pytest.approx(2.0)
    assert float(market["roi"]) == pytest.approx(1.0)
    models = _read_csv(report_dir / "model_performance.csv")
    a_bet = next(row for row in models if row["model"] == "A" and row["verdict"] == "BET")
    assert float(a_bet["recommendation_accuracy"]) == pytest.approx(1.0)
    assert (report_dir / "ledgers" / "market_snapshots.csv").exists()


def test_settlement_requires_identifier_when_alt_lines_are_ambiguous(tmp_path):
    rows = [
        _candidate(market_id="m1", outcome_id="o1", line=10.5),
        _candidate(market_id="m1", outcome_id="o2", line=11.5),
    ]
    pack_dir = _pack(tmp_path, rows)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    settlement = {field: "" for field in feedback.SETTLEMENT_FIELDS}
    settlement.update(
        {
            "event_id": "e1",
            "market_id": "m1",
            "actual_result": "9",
            "win_loss_push": "L",
            "closing_line": "11",
            "closing_price": "-110",
            "would_have_result": "L",
        }
    )
    settlement_input = tmp_path / "ambiguous.csv"
    _write_csv(settlement_input, feedback.SETTLEMENT_FIELDS, [settlement])

    with pytest.raises(feedback.FeedbackError, match="matches 2 decisions"):
        feedback.import_settlements(settlement_input, db_path)


def test_settlement_rejects_identity_that_contradicts_decision(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    decision = _read_csv(pack_dir / "decisions.csv")[0]
    settlement = {field: "" for field in feedback.SETTLEMENT_FIELDS}
    settlement.update(
        {
            "decision_id": decision["decision_id"],
            "event_id": "wrong-event",
            "market_id": "m1",
            "actual_result": "14",
            "win_loss_push": "W",
            "closing_line": "11.5",
            "closing_price": "-110",
            "would_have_result": "W",
        }
    )
    input_path = tmp_path / "contradictory.csv"
    _write_csv(input_path, feedback.SETTLEMENT_FIELDS, [settlement])

    with pytest.raises(feedback.FeedbackError, match="contradicts"):
        feedback.import_settlements(input_path, db_path)


@pytest.mark.parametrize(
    ("selection", "taken", "closing", "expected"),
    [
        ("Player Points OVER 10.5", 10.5, 11.5, 1.0),
        ("Player Points UNDER 10.5", 10.5, 9.5, 1.0),
        ("Team Spread +3", 3.0, 2.0, 1.0),
        ("Team Spread -2.5", -2.5, -3.0, 0.5),
    ],
)
def test_compute_clv_line_sign_convention(selection, taken, closing, expected):
    assert feedback.compute_clv_line(selection, taken, closing) == pytest.approx(expected)


def test_probability_scoring_conditions_on_non_push_mass():
    rows = [
        {
            "win_loss_push": "W",
            "market_consensus_prob": 0.54,
            "independent_model_prob": "",
            "final_blended_prob": 0.54,
            "push_prob": 0.10,
        },
        {
            "win_loss_push": "L",
            "market_consensus_prob": 0.55,
            "independent_model_prob": "",
            "final_blended_prob": 0.55,
            "push_prob": "",
        },
    ]

    metrics = {
        row["probability_source"]: row for row in feedback._probability_metrics(rows)
    }
    assert metrics["market_consensus"]["n"] == 1
    assert metrics["market_consensus"]["brier_score"] == pytest.approx((0.6 - 1.0) ** 2)
