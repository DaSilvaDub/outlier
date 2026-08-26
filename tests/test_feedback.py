import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from outlier_scrapers import feedback, pack, parlay_legs
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
from outlier_scrapers.alt_bankroll_props import ALT_BANKROLL_PROPS_HEADER
from outlier_scrapers.alt_player_props import ALT_PLAYER_PROPS_HEADER
from outlier_scrapers.alt_spreads import ALT_SPREADS_HEADER
from outlier_scrapers.alt_team_totals import (
    ALT_TEAM_TOTAL_PARLAYS_HEADER,
    ALT_TEAM_TOTALS_HEADER,
)
from outlier_scrapers.pack import CANDIDATES_HEADER
from outlier_scrapers.ultimate_alt import ULTIMATE_ALT_HEADER


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
    _write_csv(
        pack_dir / "candidates.csv",
        CANDIDATES_HEADER,
        [row for row in rows if row["selected"] == "true"],
    )
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
        assert conn.execute("SELECT COUNT(*) FROM pack_snapshot_memberships").fetchone()[0] == 2
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


def test_capture_preserves_first_snapshot_pack_and_records_each_pack_vintage(tmp_path):
    first_row = _candidate()
    first_pack = _pack(tmp_path, [first_row])
    second_pack = tmp_path / "packs" / "2026-07-14"
    second_row = _candidate(actionable=False)
    _write_csv(second_pack / "candidates.csv", CANDIDATES_HEADER, [second_row])
    _write_csv(
        second_pack / "opportunities.csv",
        [*CANDIDATES_HEADER, "selected"],
        [second_row],
    )
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(first_pack, db_path)
    feedback.capture_pack(second_pack, db_path)

    with sqlite3.connect(db_path) as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
        original_pack = conn.execute("SELECT pack_path FROM market_snapshots").fetchone()[0]
        memberships = conn.execute(
            "SELECT pack_path, pipeline_verdict, units "
            "FROM pack_snapshot_memberships ORDER BY pack_path"
        ).fetchall()
    assert snapshot_count == 1
    assert original_pack == str(first_pack.resolve())
    assert memberships == [
        (str(first_pack.resolve()), "PLAY", 2.0),
        (str(second_pack.resolve()), "STAND_DOWN", 0.0),
    ]


def test_schema_v4_backfills_first_seen_pack_membership(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE pack_snapshot_memberships")
        conn.execute("PRAGMA user_version = 4")

    feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        membership = conn.execute(
            "SELECT pack_path, pipeline_verdict, units FROM pack_snapshot_memberships"
        ).fetchone()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert membership == (str(pack_dir.resolve()), "PLAY", 2.0)
    assert version == feedback.SCHEMA_VERSION


def test_capture_pack_persists_projection_feature_hash(tmp_path):
    selected = _candidate()
    selected["independent_model_prob"] = 0.62
    selected["projection_feature_hash"] = "so-starter-gamelog-v2"
    selected["projection_quality_flags"] = "projection_independent_gamelog_so"
    pack_dir = _pack(tmp_path, [selected])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT independent_model_prob, projection_feature_hash, projection_quality_flags "
            "FROM market_snapshots WHERE market_id = 'm1'"
        ).fetchone()
        assert row["independent_model_prob"] == pytest.approx(0.62)
        assert row["projection_feature_hash"] == "so-starter-gamelog-v2"
        assert row["projection_quality_flags"] == "projection_independent_gamelog_so"


def test_capture_pack_includes_qualified_and_rejected_ultimate_alt_rows(tmp_path):
    pack_dir = _pack(tmp_path, [])
    qualified = {field: "" for field in ULTIMATE_ALT_HEADER}
    qualified.update(
        {
            "sport": "MLB",
            "league": "MLB",
            "event_id": "alt-event",
            "market_id": "alt-market",
            "outcome_id": "alt-outcome",
            "selection": "TOR +5.5",
            "line": "5.5",
            "price": "-110",
            "decimal_price": "1.9091",
            "book": "Novig",
            "estimated_prob": "0.80",
            "conservative_prob": "0.72",
            "implied_prob": "0.52381",
            "edge_pct": "19.619",
            "alt_type": "SPREAD",
            "market_type": "GAMELINE",
            "shadow_status": "QUALIFIED",
            "board": "ALT_SHADOW_QUALIFIED",
            "recommended_units_pre_news": "",
            "portfolio_shadow_units": "0.5",
        }
    )
    rejected = dict(
        qualified,
        market_id="rejected-market",
        outcome_id="rejected-outcome",
        shadow_status="REJECTED",
        board="ALT_SHADOW_REJECTED",
        rejection_reasons="CONSERVATIVE_EV_BELOW_1_5",
        recommended_units_pre_news="",
        portfolio_shadow_units="",
    )
    _write_csv(pack_dir / "ultimate_alt.csv", ULTIMATE_ALT_HEADER, [qualified, rejected])
    db_path = tmp_path / "feedback.sqlite3"

    stats = feedback.capture_pack(pack_dir, db_path)

    assert stats.snapshots == 2
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT board, selected, final_blended_prob, portfolio_mode "
            "FROM market_snapshots ORDER BY market_id"
        ).fetchall()
    assert rows == [
        ("ALT_SHADOW_QUALIFIED", 1, 0.72, "shadow"),
        ("ALT_SHADOW_REJECTED", 0, 0.72, "shadow"),
    ]


def test_capture_persists_blend_segments_and_fits_settled_weights(tmp_path):
    rows = [
        _candidate(market_id="m1", outcome_id="o1"),
        _candidate(market_id="m2", outcome_id="o2"),
    ]
    for row in rows:
        row.update(
            {
                "independent_model_prob": 0.40,
                "_event_starts_at": "2026-07-13T20:00:00+00:00",
                "data_quality_tier": "HIGH",
                "blend_market_weight": 0.70,
                "blend_model_weight": 0.30,
                "blend_weight_source": "learned:market_type",
                "blend_model_version": "blend-test",
            }
        )
    pack_dir = _pack(tmp_path, rows)
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        captured = conn.execute(
            "SELECT hours_before_game, odds_range, time_before_game, "
            "data_quality_tier, blend_market_weight FROM market_snapshots "
            "ORDER BY market_id"
        ).fetchall()
        identities = conn.execute(
            "SELECT d.decision_id, s.snapshot_id, s.outcome_id, s.event_id, s.market_id "
            "FROM decisions d JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id "
            "ORDER BY s.market_id"
        ).fetchall()
        for index, identity in enumerate(identities):
            decision_id, snapshot_id, outcome_id, event_id, market_id = identity
            conn.execute(
                "INSERT INTO settlements ("
                "settlement_id, decision_id, snapshot_id, outcome_id, event_id, "
                "market_id, win_loss_push, settled_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"settlement-{index}",
                    decision_id,
                    snapshot_id,
                    outcome_id,
                    event_id,
                    market_id,
                    "W" if index == 0 else "L",
                    "2026-07-14T00:00:00+00:00",
                ),
            )

    assert captured == [(4.0, "-100_TO_+100", "1_TO_6H", "HIGH", 0.7)] * 2
    output = tmp_path / "blend_weights.json"
    artifact = feedback.fit_blend_weights(db_path, output, min_samples=2, prior_strength=0)
    assert output.exists()
    assert artifact["status"] == "active"
    assert artifact["global"]["market_weight"] == pytest.approx(0.5)
    assert artifact["dimensions"]["league"]["WNBA"]["n"] == 2


def test_v4_migration_recovers_segments_for_frozen_history(tmp_path):
    row = _candidate()
    row.update(
        {
            "independent_model_prob": 0.40,
            "_event_starts_at": "2026-07-13T20:00:00+00:00",
            "data_quality_flags": "thin_liquidity",
        }
    )
    pack_dir = _pack(tmp_path, [row])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE decisions SET final_verdict = 'PLAY'")
        conn.execute(
            "UPDATE market_snapshots SET data_quality_tier = NULL, "
            "event_starts_at = NULL, hours_before_game = NULL, odds_range = NULL, "
            "time_before_game = NULL"
        )
        conn.execute("PRAGMA user_version = 3")

    feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        recovered = conn.execute(
            "SELECT data_quality_tier, event_starts_at, hours_before_game, "
            "odds_range, time_before_game FROM market_snapshots"
        ).fetchone()
        verdict = conn.execute("SELECT final_verdict FROM decisions").fetchone()[0]
    assert recovered == (
        "MEDIUM",
        "2026-07-13T20:00:00+00:00",
        4.0,
        "-100_TO_+100",
        "1_TO_6H",
    )
    assert verdict == "PLAY"


def test_capture_pack_deduplicates_repeated_opportunity_decisions(tmp_path):
    repeated = _candidate()
    pack_dir = _pack(tmp_path, [repeated, repeated.copy()])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"

    stats = feedback.capture_pack(pack_dir, db_path)

    assert stats == feedback.CaptureStats(snapshots=2, decisions=1)
    assert len(_read_csv(pack_dir / "decisions.csv")) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


@pytest.mark.parametrize("source", ["opportunities", "game_totals"])
def test_snapshot_numeric_zero_values_do_not_fall_back(source, tmp_path):
    row = _candidate()
    row.update(
        {
            "market_consensus_prob": 0.0,
            "final_blended_prob": 0.0,
            "model_prob": 0.60,
            "price": 0.0,
            "best_price": 125,
            "edge": 0.0,
            "edge_pct": 0.25,
            "best_side": "OVER",
            "projected_over_prob": 0.60,
            "total_kind": "game",
        }
    )

    snapshot = feedback._snapshot_from_pack_row(source, row, tmp_path, "2026-07-13T16:00:00+00:00")

    assert snapshot["market_consensus_prob"] == pytest.approx(0.0)
    assert snapshot["final_blended_prob"] == pytest.approx(0.0)
    assert snapshot["price"] == pytest.approx(0.0)
    assert snapshot["edge"] == pytest.approx(0.0)


def test_snapshot_blank_numeric_values_use_fallbacks(tmp_path):
    row = _candidate()
    row.update(
        {
            "market_consensus_prob": "",
            "final_blended_prob": "",
            "model_prob": 0.60,
            "price": "",
            "best_price": 125,
            "edge": "",
            "edge_pct": 0.25,
        }
    )

    snapshot = feedback._snapshot_from_pack_row(
        "opportunities", row, tmp_path, "2026-07-13T16:00:00+00:00"
    )

    assert snapshot["market_consensus_prob"] == pytest.approx(0.60)
    assert snapshot["final_blended_prob"] == pytest.approx(0.60)
    assert snapshot["price"] == pytest.approx(125)
    assert snapshot["edge"] == pytest.approx(0.25)


def test_recapture_refreshes_corrected_probability_semantics(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE market_snapshots "
            "SET market_consensus_prob = 0.90, final_blended_prob = 0.90, "
            "edge = 0.40, independent_model_prob = 0.70"
        )

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT market_consensus_prob, independent_model_prob, "
            "final_blended_prob, edge FROM market_snapshots"
        ).fetchone()
    assert row == pytest.approx((0.60, 0.70, 0.60, 0.10))


@pytest.mark.parametrize("freeze_with", ["final_verdict", "settlement"])
def test_recapture_cannot_rewrite_finalized_prediction_history(tmp_path, freeze_with):
    source = _candidate()
    pack_dir = _pack(tmp_path, [source])
    db_path = tmp_path / "calibration" / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        snapshot_id, decision_id = conn.execute(
            "SELECT s.snapshot_id, d.decision_id FROM market_snapshots s "
            "JOIN decisions d ON d.snapshot_id = s.snapshot_id"
        ).fetchone()
        if freeze_with == "final_verdict":
            conn.execute(
                "UPDATE decisions SET final_verdict = 'PLAY' WHERE decision_id = ?",
                (decision_id,),
            )
        else:
            conn.execute(
                "INSERT INTO settlements ("
                "settlement_id, decision_id, snapshot_id, outcome_id, event_id, "
                "market_id, win_loss_push, settled_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("settled", decision_id, snapshot_id, "o1", "e1", "m1", "W", "now"),
            )

    source.update(
        {
            "market_consensus_prob": 0.75,
            "final_blended_prob": 0.75,
            "edge_pct": 0.50,
            "selected": "false",
            "actionable": "false",
        }
    )
    _pack(tmp_path, [source])
    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT market_consensus_prob, final_blended_prob, edge, selected FROM market_snapshots"
        ).fetchone()
        decision = conn.execute("SELECT pipeline_verdict, units FROM decisions").fetchone()
    assert snapshot == pytest.approx((0.60, 0.60, 0.10, 1))
    assert decision == ("PLAY", 2.0)

    frozen_decisions = _read_csv(pack_dir / "decisions.csv")
    assert feedback.import_decisions(pack_dir / "decisions.csv", db_path).imported == 1
    frozen_decisions[0]["pipeline_verdict"] = "STAND_DOWN"
    frozen_decisions[0]["units"] = "0"
    changed_input = tmp_path / f"changed-{freeze_with}.csv"
    _write_csv(changed_input, feedback.DECISION_FIELDS, frozen_decisions)
    with pytest.raises(feedback.FeedbackError, match="cannot change finalized or settled decision"):
        feedback.import_decisions(changed_input, db_path)


def _healthy_feed_health() -> dict:
    return {
        "props_status": "ok",
        "games_status": "ok",
        "insights_status": "ok",
        "injuries_status": "ok",
        "line_movement_status": "ok",
        "game_line_movement_status": "ok",
        "cards_status": "ok",
        "coverage_pct": 100.0,
        "oldest_source_age": 0.2,
        "latest_source_age": 0.1,
        "failed_ids": [],
        "schema_version": "1.0",
    }


def _stub_pack_feed_health(monkeypatch) -> None:
    monkeypatch.setattr(
        pack,
        "build_feed_health_by_league",
        lambda leagues: {league: _healthy_feed_health() for league in leagues},
    )
    # These pack-publication tests isolate feedback atomicity from the
    # projection artifact's independent slate-date contract.
    monkeypatch.setattr(pack, "load_projection_records", lambda *_args, **_kwargs: [])


def test_pack_main_captures_feedback_by_default(tmp_path, monkeypatch):
    row = _candidate()

    def fake_build(
        _leagues,
        _date,
        _top_ev,
        _top_signal,
        *,
        opportunity_rows_out=None,
        feed_health_by_league=None,
        blend_artifact=None,
    ):
        assert opportunity_rows_out is not None
        assert feed_health_by_league is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    _stub_pack_feed_health(monkeypatch)
    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues, _health: [])
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

    def fake_build(
        _leagues,
        _date,
        _top_ev,
        _top_signal,
        *,
        opportunity_rows_out=None,
        feed_health_by_league=None,
        blend_artifact=None,
    ):
        assert opportunity_rows_out is not None
        assert feed_health_by_league is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    _stub_pack_feed_health(monkeypatch)
    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues, _health: [])
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

    def fake_build(
        _leagues,
        _date,
        _top_ev,
        _top_signal,
        *,
        opportunity_rows_out=None,
        feed_health_by_league=None,
        blend_artifact=None,
    ):
        assert opportunity_rows_out is not None
        assert feed_health_by_league is not None
        opportunity_rows_out.append(dict(row))
        return [row], "2026-07-13", {}, {}

    _stub_pack_feed_health(monkeypatch)
    monkeypatch.setattr(pack, "build_pack_with_coverage", fake_build)
    monkeypatch.setattr(pack, "build_freshness_section", lambda _leagues, _health: [])
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
    represented["_event_starts_at"] = "2026-07-13T20:00:00+00:00"
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
            "shadow_actionable_4pct": "true",
            "shadow_recommended_units": "2",
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
            "SELECT outcome_id, line, event_starts_at FROM market_snapshots ORDER BY line"
        ).fetchall()
        shadow_signal = conn.execute(
            "SELECT signal_flags FROM market_snapshots WHERE outcome_id = 'm1:10.5:OVER'"
        ).fetchone()[0]
    assert rows == [
        ("m1:10.5:OVER", "10.5", "2026-07-13T20:00:00+00:00"),
        ("o2", "11.5", ""),
    ]
    assert "totals_shadow_4pct:QUALIFIED" in shadow_signal


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


@pytest.mark.parametrize(
    (
        "stable_updated",
        "legacy_updated",
        "expected_verdict",
        "expected_units",
        "expected_updated",
    ),
    [
        (
            "2026-07-13T16:00:00+00:00",
            "2026-07-13T14:00:00+00:00",
            "PLAY",
            2.0,
            "2026-07-13T16:00:00+00:00",
        ),
        (
            "2026-07-13T14:00:00+00:00",
            "2026-07-13T16:00:00+00:00",
            "STAND_DOWN",
            0.0,
            "2026-07-13T16:00:00+00:00",
        ),
        (
            "2026-07-13T16:00:00+00:00",
            "2026-07-13T16:00:00+00:00",
            "PLAY",
            2.0,
            "2026-07-13T16:00:00+00:00",
        ),
    ],
)
def test_initialize_database_upgrades_reduced_legacy_schema(
    tmp_path,
    stable_updated,
    legacy_updated,
    expected_verdict,
    expected_units,
    expected_updated,
):
    db_path = tmp_path / "feedback.sqlite3"
    snapshot_id = "snapshot_legacy"
    stable_decision_id = feedback._stable_id("decision", snapshot_id)
    created_at = "2026-07-13T15:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                captured_at TEXT,
                event_id TEXT,
                market_id TEXT,
                selection TEXT,
                created_at TEXT
            );
            CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                pipeline_verdict TEXT,
                units REAL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE settlements (
                settlement_id TEXT PRIMARY KEY,
                decision_id TEXT,
                event_id TEXT,
                market_id TEXT,
                win_loss_push TEXT,
                settled_at TEXT
            );
            PRAGMA user_version = 1;
            """
        )
        conn.execute(
            "INSERT INTO market_snapshots "
            "(snapshot_id, captured_at, event_id, market_id, selection, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (snapshot_id, created_at, "e1", "m1", "OVER 10.5", created_at),
        )
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
            (stable_decision_id, snapshot_id, "PLAY", 2.0, created_at, stable_updated),
        )
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-decision", snapshot_id, "STAND_DOWN", 0.0, "", legacy_updated),
        )

    feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        snapshot_columns = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)")}
        decision_columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        settlement_columns = {row[1] for row in conn.execute("PRAGMA table_info(settlements)")}
        membership_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(pack_snapshot_memberships)")
        }
        decision = conn.execute(
            "SELECT decision_id, pipeline_verdict, units, created_at, updated_at FROM decisions"
        ).fetchone()
        indexes = {
            row[1]
            for table in (
                "market_snapshots",
                "pack_snapshot_memberships",
                "decisions",
                "settlements",
            )
            for row in conn.execute(f"PRAGMA index_list({table})")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert set(feedback.MARKET_SNAPSHOT_COLUMN_DEFINITIONS) <= snapshot_columns
    assert set(feedback.DECISION_COLUMN_DEFINITIONS) <= decision_columns
    assert set(feedback.SETTLEMENT_COLUMN_DEFINITIONS) <= settlement_columns
    assert set(feedback.PACK_MEMBERSHIP_FIELDS) <= membership_columns
    assert decision == (
        stable_decision_id,
        expected_verdict,
        expected_units,
        created_at,
        expected_updated,
    )
    assert {
        "idx_snapshots_market",
        "idx_snapshots_close_book",
        "idx_snapshots_close_outcome",
        "idx_snapshots_close_selection",
        "idx_snapshots_segment",
        "idx_decisions_snapshot",
        "idx_settlements_market",
        "idx_settlements_decision",
        "idx_pack_membership_pack_path",
    } <= indexes
    assert version == feedback.SCHEMA_VERSION


def test_initialize_database_rejects_orphan_decisions_atomically(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE market_snapshots (snapshot_id TEXT PRIMARY KEY);
            CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY,
                snapshot_id TEXT
            );
            CREATE TABLE settlements (settlement_id TEXT PRIMARY KEY);
            INSERT INTO decisions VALUES ('orphan-blank', '');
            INSERT INTO decisions VALUES ('orphan-null', NULL);
            PRAGMA user_version = 1;
            """
        )

    with pytest.raises(feedback.FeedbackError, match="blank, missing, or unknown snapshot_id"):
        feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT decision_id, snapshot_id FROM decisions ORDER BY decision_id"
        ).fetchall()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert rows == [("orphan-blank", ""), ("orphan-null", None)]
    assert columns == {"decision_id", "snapshot_id"}
    assert version == 1


def test_initialize_database_rejects_null_decision_id_atomically(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE market_snapshots (snapshot_id TEXT PRIMARY KEY);
            CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY,
                snapshot_id TEXT
            );
            CREATE TABLE settlements (settlement_id TEXT PRIMARY KEY);
            INSERT INTO market_snapshots VALUES ('snapshot-valid');
            INSERT INTO decisions VALUES (NULL, 'snapshot-valid');
            PRAGMA user_version = 1;
            """
        )

    with pytest.raises(feedback.FeedbackError, match="blank or missing decision_id"):
        feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT decision_id, snapshot_id FROM decisions").fetchall()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert rows == [(None, "snapshot-valid")]
    assert columns == {"decision_id", "snapshot_id"}
    assert version == 1


def test_schema_v2_total_probability_migration_runs_before_history_freeze(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    with sqlite3.connect(db_path) as conn:
        snapshot_id, decision_id = conn.execute(
            "SELECT s.snapshot_id, d.decision_id FROM market_snapshots s "
            "JOIN decisions d ON d.snapshot_id = s.snapshot_id"
        ).fetchone()
        conn.execute(
            "UPDATE market_snapshots SET board = 'GAME_TOTALS', push_prob = 0.20, "
            "market_consensus_prob = 0.75, final_blended_prob = 0.75, edge = 0.50"
        )
        conn.execute("UPDATE decisions SET final_verdict = 'PLAY'")
        conn.execute(
            "INSERT INTO settlements ("
            "settlement_id, decision_id, snapshot_id, outcome_id, event_id, "
            "market_id, win_loss_push, settled_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("settled", decision_id, snapshot_id, "o1", "e1", "m1", "W", "now"),
        )
        conn.execute("PRAGMA user_version = 2")

    feedback.initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        # Simulate a process failure after row conversion but before the schema
        # version was durably advanced. The row marker must make retry safe.
        conn.execute("PRAGMA user_version = 2")
    feedback.initialize_database(db_path)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute(
            "SELECT market_consensus_prob, final_blended_prob, edge, "
            "data_quality_flags FROM market_snapshots"
        ).fetchone()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert snapshot[:3] == pytest.approx((0.60, 0.60, 0.40))
    assert "probability_semantics_v3_migrated" in snapshot[3]
    assert snapshot[3].count("probability_semantics_v3_migrated") == 1
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
    stats = feedback.import_settlements(db_path, [settlement])
    assert stats == {
        "unmatched_count": 0,
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "updated_count": 1,
    }

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT clv_line, clv_price, pnl FROM settlements").fetchone()
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
        "decision_coverage.csv",
        "model_performance.csv",
        "ultimate_alt_shadow.csv",
        "summary.json",
        "report.md",
    }
    assert expected_files.issubset({path.name for path in report_dir.iterdir()})

    probability = {
        row["probability_source"]: row for row in _read_csv(report_dir / "probability_metrics.csv")
    }
    assert int(probability["market_consensus"]["n"]) == 1
    assert float(probability["market_consensus"]["brier_score"]) == pytest.approx(0.16)
    assert int(probability["independent_model"]["n"]) == 0
    market = _read_csv(report_dir / "market_type.csv")[0]
    assert float(market["profit"]) == pytest.approx(2.0)
    assert float(market["roi"]) == pytest.approx(1.0)
    decision_coverage = {
        row["decision_class"]: row for row in _read_csv(report_dir / "decision_coverage.csv")
    }
    assert int(decision_coverage["PLAY"]["total_decisions"]) == 1
    assert int(decision_coverage["PLAY"]["settled_decisions"]) == 1
    assert int(decision_coverage["PLAY"]["unsettled_decisions"]) == 0
    assert int(decision_coverage["PLAY"]["missing_event_start"]) == 1
    models = _read_csv(report_dir / "model_performance.csv")
    a_bet = next(row for row in models if row["model"] == "A" and row["verdict"] == "BET")
    assert float(a_bet["recommendation_accuracy"]) == pytest.approx(1.0)
    assert (report_dir / "ledgers" / "market_snapshots.csv").exists()
    assert (report_dir / "ledgers" / "pack_snapshot_memberships.csv").exists()


def test_report_separates_unsettled_plays_from_settled_performance(tmp_path):
    settled = _candidate(market_id="m1", outcome_id="o1")
    settled["_event_starts_at"] = "2026-07-13T20:00:00+00:00"
    unsettled = _candidate(market_id="m2", outcome_id="o2", line=12.5)
    pack_dir = _pack(tmp_path, [settled, unsettled])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    with feedback.open_database(db_path) as conn:
        decision_id, snapshot_id = conn.execute(
            "SELECT d.decision_id, d.snapshot_id FROM decisions d "
            "JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id "
            "WHERE s.market_id = 'm1'"
        ).fetchone()
        conn.execute(
            "INSERT INTO settlements ("
            "settlement_id, decision_id, snapshot_id, outcome_id, event_id, market_id, "
            "win_loss_push, settled_at) VALUES (?, ?, ?, 'o1', 'e1', 'm1', 'W', ?)",
            ("settled-m1", decision_id, snapshot_id, "2026-07-14T00:00:00+00:00"),
        )

    report_dir = feedback.generate_report(db_path, tmp_path / "report")
    play = next(
        row
        for row in _read_csv(report_dir / "decision_coverage.csv")
        if row["decision_class"] == "PLAY"
    )

    assert int(play["total_decisions"]) == 2
    assert int(play["settled_decisions"]) == 1
    assert int(play["unsettled_decisions"]) == 1
    assert int(play["missing_event_start"]) == 1
    assert "ROI, profit, hit-rate, and market tables include settled decisions only" in (
        report_dir / "report.md"
    ).read_text(encoding="utf-8")


def test_ultimate_alt_release_gate_requires_depth_clv_and_each_market_type():
    rows = []
    for alt_type in ("SPREAD", "TOTAL", "PLAYER_PROP"):
        for index in range(40):
            rows.append(
                {
                    "board": "ALT_SHADOW_QUALIFIED",
                    "signal_flags": f"ultimate_alt:{alt_type}",
                    "win_loss_push": "W" if index < 30 else "L",
                    "implied_prob": 0.60,
                    "final_blended_prob": 0.72,
                    "decimal_price": 1.8,
                    "clv_price": 0.01,
                    "captured_at": f"2099-01-{index % 30 + 1:02d}T12:00:00Z",
                }
            )

    release = feedback.ultimate_alt_shadow_release(rows)

    assert release["overall"]["n"] == 120
    assert release["gates"]["minimum_40_each_type"] is True
    assert release["gates"]["minimum_200_settled"] is False
    assert release["ready_for_manual_promotion_review"] is False
    assert release["auto_promotion"] is False


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

    stats = feedback.import_settlements(db_path, [settlement])
    assert stats["ambiguous_count"] == 1


def test_settlement_outcome_id_disambiguates_alt_lines(tmp_path):
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
            "outcome_id": "o2",
            "actual_result": "12",
            "win_loss_push": "W",
        }
    )

    stats = feedback.import_settlements(db_path, [settlement])

    assert stats["updated_count"] == 1
    assert stats["ambiguous_count"] == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT outcome_id FROM settlements").fetchone()[0] == "o2"


def test_settle_cli_reads_csv_and_prints_summary(tmp_path, capsys):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    settlement = {field: "" for field in feedback.SETTLEMENT_FIELDS}
    settlement.update(
        {
            "event_id": "e1",
            "market_id": "m1",
            "outcome_id": "o1",
            "actual_result": "12",
            "win_loss_push": "W",
        }
    )
    input_path = tmp_path / "settlements.csv"
    _write_csv(input_path, feedback.SETTLEMENT_FIELDS, [settlement])

    assert feedback.main(["--db", str(db_path), "settle", "--input", str(input_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "ambiguous_count": 0,
        "duplicate_count": 0,
        "unmatched_count": 0,
        "updated_count": 1,
    }


def test_settlement_inbox_archives_only_fully_matched_files(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    good = {field: "" for field in feedback.SETTLEMENT_FIELDS}
    good.update(
        {
            "event_id": "e1",
            "market_id": "m1",
            "outcome_id": "o1",
            "win_loss_push": "W",
        }
    )
    bad = dict(good, event_id="missing", outcome_id="missing")
    _write_csv(inbox / "good.csv", feedback.SETTLEMENT_FIELDS, [good])
    _write_csv(inbox / "bad.csv", feedback.SETTLEMENT_FIELDS, [bad])

    stats = feedback.import_settlement_inbox(inbox, db_path)

    assert stats["processed_count"] == 1
    assert stats["retained_count"] == 1
    assert not (inbox / "good.csv").exists()
    assert (inbox.parent / "processed" / "good.csv").exists()
    assert (inbox / "bad.csv").exists()


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

    stats = feedback.import_settlements(db_path, [settlement])
    assert stats["unmatched_count"] == 1


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

    metrics = {row["probability_source"]: row for row in feedback._probability_metrics(rows)}
    assert metrics["market_consensus"]["n"] == 1
    assert metrics["market_consensus"]["brier_score"] == pytest.approx((0.6 - 1.0) ** 2)


def test_model_performance_inverts_negative_recommendation_pnl():
    rows = [
        {
            "A_verdict": "FADE",
            "win_loss_push": "L",
            "decimal_price": 2.25,
        }
    ]

    performance = feedback._model_performance(rows)

    assert performance[0]["recommendation_accuracy"] == pytest.approx(1.0)
    assert performance[0]["would_have_flat_pnl"] == pytest.approx(1.25)


def test_missing_edge_diagnostics_surfaces_gameline_missing_model_source():
    rows = [
        {
            "sport": "MLB",
            "market_type": "GAMELINE",
            "model_prob_source": "",
            "edge": None,
            "market_consensus_prob": None,
            "decimal_price": 1.91,
        },
        {
            "sport": "WNBA",
            "market_type": "PLAYER_PROP",
            "model_prob_source": "market_consensus",
            "edge": 0.0,
        },
    ]

    diagnostics = feedback._missing_edge_diagnostics(rows)

    assert diagnostics["missing_edge_rows"] == 1
    assert diagnostics["missing_edge_rate"] == pytest.approx(0.5)
    assert diagnostics["breakdown"] == [
        {
            "sport": "MLB",
            "market_type": "GAMELINE",
            "reason": "gameline_missing_model_prob_source",
            "n": 1,
        }
    ]
    assert rows[0]["edge"] is None


def _seed_settled_row(
    db_path: Path,
    *,
    suffix: str,
    play: bool,
    board: str,
    settled_days_ago: int,
    closing_matches_take: bool,
) -> None:
    """Seed one snapshot + decision + settlement for retention/CLV tests."""

    feedback.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        captured_at = (
            datetime.now(timezone.utc) - timedelta(days=settled_days_ago + 1)
        ).isoformat()
        settled_at = (datetime.now(timezone.utc) - timedelta(days=settled_days_ago)).isoformat()
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                selection, line, price, book, decimal_price, board, market_type,
                event_starts_at, signal_flags, hit_rate_component, pack_path,
                cap_reasons, created_at
            ) VALUES (?, ?, 'WNBA', ?, ?, ?, ?, ?, -110, 'Book', 1.909, ?, 'PLAYER_PROP',
                      ?, 'hit_rate_support', 65.0, '/packs/2026-01-01', 'none', ?)
            """,
            (
                f"snapshot-{suffix}",
                captured_at,
                f"event-{suffix}",
                f"market-{suffix}",
                f"outcome-{suffix}",
                f"Player Points OVER {suffix}",
                "10.5",
                board,
                captured_at,
                captured_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO decisions (
                decision_id, snapshot_id, pipeline_verdict, final_verdict, units,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"decision-{suffix}",
                f"snapshot-{suffix}",
                "PLAY" if play else "STAND_DOWN",
                "PLAY" if play else "",
                1.0 if play else 0.0,
                captured_at,
                captured_at,
            ),
        )
        closing_line = "10.5" if closing_matches_take else "11.5"
        closing_price = -110 if closing_matches_take else -120
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_id, decision_id, snapshot_id, outcome_id, event_id, market_id,
                actual_result, win_loss_push, closing_line, closing_price, clv_line,
                clv_price, pnl, would_have_result, settled_at
            ) VALUES (?, ?, ?, ?, ?, ?, '12', 'W', ?, ?, 0.0, 0.0, 0.0, 'W', ?)
            """,
            (
                f"settlement-{suffix}",
                f"decision-{suffix}",
                f"snapshot-{suffix}",
                f"outcome-{suffix}",
                f"event-{suffix}",
                f"market-{suffix}",
                closing_line,
                closing_price,
                settled_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_find_distinct_closing_snapshot_excludes_the_taken_snapshot(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES ('taken', '2026-08-07T20:00:00+00:00', 'WNBA', 'e1', 'm1', 'o1',
                  'Player Points OVER 10.5', '10.5', -110, 'Book',
                  '2026-08-07T23:30:00+00:00', '2026-08-07T20:00:00+00:00')
        """
    )

    line, price = feedback.find_distinct_closing_snapshot(
        conn,
        event_id="e1",
        outcome_id="o1",
        market_id="m1",
        selection="Player Points OVER 10.5",
        book="Book",
        exclude_snapshot_id="taken",
        after_captured_at="2026-08-07T20:00:00+00:00",
    )
    assert (line, price) == (None, None)

    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES ('close', '2026-08-07T23:00:00+00:00', 'WNBA', 'e1', 'm1', 'o1',
                  'Player Points OVER 10.5', '11.5', -120, 'Book',
                  '2026-08-07T23:30:00+00:00', '2026-08-07T23:00:00+00:00')
        """
    )
    line, price = feedback.find_distinct_closing_snapshot(
        conn,
        event_id="e1",
        outcome_id="o1",
        market_id="m1",
        selection="Player Points OVER 10.5",
        book="Book",
        exclude_snapshot_id="taken",
        after_captured_at="2026-08-07T20:00:00+00:00",
    )
    assert (line, price) == ("11.5", -120)
    conn.close()


def test_find_distinct_closing_snapshot_ignores_older_history(tmp_path):
    """A latest-captured taken snapshot with only *older* history available
    must not have that stale, pre-take quote returned as its "close" --
    excluding the taken snapshot's own id is not enough on its own; the
    replacement has to be genuinely later too, or CLV gets fabricated from
    a quote that predates the bet."""

    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES ('older', '2026-08-07T20:00:00+00:00', 'WNBA', 'e1', 'm1', 'o1',
                  'Player Points OVER 10.5', '9.5', -105, 'Book',
                  '2026-08-07T23:30:00+00:00', '2026-08-07T20:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES ('taken', '2026-08-07T22:00:00+00:00', 'WNBA', 'e1', 'm1', 'o1',
                  'Player Points OVER 10.5', '10.5', -110, 'Book',
                  '2026-08-07T23:30:00+00:00', '2026-08-07T22:00:00+00:00')
        """
    )

    line, price = feedback.find_distinct_closing_snapshot(
        conn,
        event_id="e1",
        outcome_id="o1",
        market_id="m1",
        selection="Player Points OVER 10.5",
        book="Book",
        exclude_snapshot_id="taken",
        after_captured_at="2026-08-07T22:00:00+00:00",
    )
    assert (line, price) == (None, None)
    conn.close()


def test_closing_line_from_movement_export_requires_export_at_or_after_start(tmp_path, monkeypatch):
    from outlier_scrapers import paths as outlier_paths

    monkeypatch.setattr(outlier_paths, "DATA_DIR", tmp_path / "data")
    league_paths = outlier_paths.league_paths("WNBA").ensure()
    export_path = league_paths.normalized / "wnba_line_movement_latest.json"
    export_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-07T22:00:00+00:00",
                "records": [
                    {
                        "event_id": "e1",
                        "market_id": "m1",
                        "outcome_id": "o1",
                        "side": "OVER",
                        "current_line": 11.5,
                        "current_odds": -120,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        # Export generated before the event starts: not a genuine close.
        line, price = feedback.closing_line_from_movement_export(
            sport="WNBA",
            event_id="e1",
            market_id="m1",
            outcome_id="o1",
            selection="Player Points OVER 10.5",
            event_starts_at="2026-08-07T23:30:00+00:00",
        )
        assert (line, price) == (None, None)

        # Export generated after the event starts: usable as the close.
        line, price = feedback.closing_line_from_movement_export(
            sport="WNBA",
            event_id="e1",
            market_id="m1",
            outcome_id="o1",
            selection="Player Points OVER 10.5",
            event_starts_at="2026-08-07T21:00:00+00:00",
        )
        assert (line, price) == (11.5, -120)
    finally:
        export_path.unlink(missing_ok=True)


def test_recompute_settlement_clv_clears_the_self_matched_bug(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_settled_row(
        db_path,
        suffix="bogus",
        play=True,
        board="A",
        settled_days_ago=1,
        closing_matches_take=True,
    )

    summary = feedback.recompute_settlement_clv(db_path)
    assert summary["inspected"] == 1
    assert summary["cleared"] == 1
    assert summary["corrected"] == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT closing_line, closing_price, clv_line, clv_price FROM settlements"
    ).fetchone()
    conn.close()
    assert row == (None, None, None, None)


def test_recompute_settlement_clv_leaves_genuine_closes_alone(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_settled_row(
        db_path,
        suffix="real",
        play=True,
        board="A",
        settled_days_ago=1,
        closing_matches_take=False,
    )

    summary = feedback.recompute_settlement_clv(db_path)
    assert summary["inspected"] == 1
    assert summary["unchanged"] == 1
    assert summary["corrected"] == 0
    assert summary["cleared"] == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT closing_line, closing_price FROM settlements").fetchone()
    conn.close()
    assert row == ("11.5", -120)


def test_recompute_settlement_clv_batches_distinct_close_lookup(tmp_path, monkeypatch):
    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    with sqlite3.connect(db_path) as conn:
        for number in range(40):
            suffix = str(number)
            event_id = f"event-{suffix}"
            outcome_id = f"outcome-{suffix}"
            market_id = f"market-{suffix}"
            taken_id = f"taken-{suffix}"
            close_id = f"close-{suffix}"
            decision_id = f"decision-{suffix}"
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    selection, line, price, book, decimal_price, event_starts_at, created_at
                ) VALUES (?, '2026-08-07T20:00:00+00:00', 'WNBA', ?, ?, ?,
                          'Player Points OVER 10.5', '10.5', -110, 'Book', 1.909,
                          '2026-08-07T23:30:00+00:00', '2026-08-07T20:00:00+00:00')
                """,
                (taken_id, event_id, market_id, outcome_id),
            )
            conn.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
                    selection, line, price, book, event_starts_at, created_at
                ) VALUES (?, '2026-08-07T23:00:00+00:00', 'WNBA', ?, ?, ?,
                          'Player Points OVER 10.5', '11.5', -120, 'Book',
                          '2026-08-07T23:30:00+00:00', '2026-08-07T23:00:00+00:00')
                """,
                (close_id, event_id, market_id, outcome_id),
            )
            conn.execute(
                "INSERT INTO decisions (decision_id, snapshot_id, created_at, updated_at) "
                "VALUES (?, ?, '2026-08-07T20:00:00+00:00', '2026-08-07T20:00:00+00:00')",
                (decision_id, taken_id),
            )
            conn.execute(
                """
                INSERT INTO settlements (
                    settlement_id, decision_id, snapshot_id, outcome_id, event_id,
                    market_id, win_loss_push, closing_line, closing_price, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'W', '10.5', -110,
                          '2026-08-08T02:00:00+00:00')
                """,
                (f"settlement-{suffix}", decision_id, taken_id, outcome_id, event_id, market_id),
            )
        conn.commit()

    statements: list[str] = []
    original_connect = feedback._connect

    def traced_connect(path):
        conn = original_connect(path)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(feedback, "_connect", traced_connect)
    monkeypatch.setattr(
        feedback,
        "find_distinct_closing_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("per-settlement close lookup must not be used")
        ),
    )

    summary = feedback.recompute_settlement_clv(db_path)

    assert summary == {"inspected": 40, "corrected": 40, "cleared": 0, "unchanged": 0}
    assert sum("WITH candidates AS" in statement for statement in statements) == 1


def test_apply_retention_policy_slims_only_settled_never_played_unflagged_rows(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_settled_row(
        db_path, suffix="played", play=True, board="A", settled_days_ago=200,
        closing_matches_take=False,
    )
    _seed_settled_row(
        db_path, suffix="flagged", play=False, board="A_FLAGGED", settled_days_ago=200,
        closing_matches_take=False,
    )
    _seed_settled_row(
        db_path, suffix="stale", play=False, board="B", settled_days_ago=200,
        closing_matches_take=False,
    )
    _seed_settled_row(
        db_path, suffix="recent", play=False, board="B", settled_days_ago=1,
        closing_matches_take=False,
    )

    stats = feedback.apply_retention_policy(db_path, cutoff_days=90)
    assert stats.eligible == 1
    assert stats.slimmed == 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = {
        row["snapshot_id"]: row
        for row in conn.execute(
            "SELECT snapshot_id, line, price, hit_rate_component, signal_flags, "
            "pack_path, cap_reasons FROM market_snapshots"
        )
    }
    conn.close()

    # Identity/result-relevant and calibration-training fields all survive
    # slimming on the eligible row -- fit_blend_weights(),
    # fit_stake_calibration_from_db(), and generate_report() read
    # hit_rate_component/signal_flags via the same shared settled-row query,
    # so retention must never clear them.
    assert rows["snapshot-stale"]["line"] == "10.5"
    assert rows["snapshot-stale"]["price"] == -110
    assert rows["snapshot-stale"]["hit_rate_component"] == 65.0
    assert rows["snapshot-stale"]["signal_flags"] == "hit_rate_support"
    # ...but the disposable portfolio-sizing/pack-provenance columns, which
    # none of those consumers read, are cleared.
    assert rows["snapshot-stale"]["pack_path"] is None
    assert rows["snapshot-stale"]["cap_reasons"] is None

    # Played, flagged, and recent rows are untouched.
    assert rows["snapshot-played"]["hit_rate_component"] == 65.0
    assert rows["snapshot-flagged"]["hit_rate_component"] == 65.0
    assert rows["snapshot-recent"]["hit_rate_component"] == 65.0

    # A rerun sees no still-populated disposable columns, so it neither
    # rewrites the same rows nor triggers another VACUUM.
    rerun = feedback.apply_retention_policy(db_path, cutoff_days=90)
    assert rerun.eligible == 0
    assert rerun.slimmed == 0


def test_apply_retention_policy_dry_run_reports_without_changing_anything(tmp_path):
    db_path = tmp_path / "feedback.sqlite3"
    _seed_settled_row(
        db_path, suffix="stale", play=False, board="B", settled_days_ago=200,
        closing_matches_take=False,
    )

    stats = feedback.apply_retention_policy(db_path, cutoff_days=90, dry_run=True)
    assert stats.eligible == 1
    assert stats.slimmed == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT hit_rate_component FROM market_snapshots").fetchone()
    conn.close()
    assert row[0] == 65.0


def test_apply_retention_policy_preserves_fitter_eligibility(tmp_path):
    """Retention must never shrink the eligible sample fit_blend_weights()
    and fit_stake_calibration_from_db() see: both read the same wide set of
    probability/segment columns off market_snapshots via _joined_rows(), so
    slimming those away as "disposable" silently destroys training data
    that happens to look identical to a dry calibration report."""

    db_path = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(db_path)
    old = "2026-01-01T20:00:00+00:00"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, market_consensus_prob,
            independent_model_prob, final_blended_prob, push_prob,
            data_quality_tier, event_starts_at, hours_before_game,
            odds_range, time_before_game, market_type, decimal_price,
            board, pack_path, cap_reasons, created_at
        ) VALUES (
            'snap-old', ?, 'WNBA', 'event-old', 'market-old', 'outcome-old',
            'Player Points OVER 10.5', '10.5', -110, 'Book', 0.55, 0.60, 0.55,
            0.0, 'CLEAN', '2026-01-01T23:30:00+00:00', 3.5, '-120_TO_-101',
            'LT_6H', 'PLAYER_PROP', 1.909, 'B', '/packs/old', 'none', ?
        )
        """,
        (old, old),
    )
    conn.execute(
        """
        INSERT INTO decisions (
            decision_id, snapshot_id, pipeline_verdict, final_verdict, units,
            created_at, updated_at
        ) VALUES ('decision-old', 'snap-old', 'STAND_DOWN', '', 0.0, ?, ?)
        """,
        (old, old),
    )
    conn.execute(
        """
        INSERT INTO settlements (
            settlement_id, decision_id, snapshot_id, outcome_id, event_id, market_id,
            actual_result, win_loss_push, closing_line, closing_price, clv_line,
            clv_price, pnl, would_have_result, settled_at
        ) VALUES ('settlement-old', 'decision-old', 'snap-old', 'outcome-old',
                   'event-old', 'market-old', '12', 'W', '11.5', -120, 1.0,
                   0.09, 0.0, 'W', ?)
        """,
        (old,),
    )
    conn.commit()
    conn.close()

    def eligible_samples() -> tuple[int, int]:
        blend = feedback.fit_blend_weights(
            db_path, tmp_path / "blend.json", min_samples=1
        )
        stake = feedback.fit_stake_calibration_from_db(
            db_path, tmp_path / "stake.json", min_samples=1
        )
        return blend["eligible_samples"], stake["eligible_samples"]

    before = eligible_samples()
    assert before == (1, 1)

    stats = feedback.apply_retention_policy(db_path, cutoff_days=90)
    assert stats.slimmed == 1

    after = eligible_samples()
    assert after == before, (
        "retention must not shrink fitter-eligible sample counts: "
        f"before={before}, after={after}"
    )


def test_recover_corrupted_database_salvages_readable_rows(tmp_path):
    pack_dir = _pack(tmp_path, [_candidate()])
    source_db = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, source_db)

    corrupted_path = tmp_path / "feedback.sqlite3.corrupted"
    corrupted_path.write_bytes(source_db.read_bytes())
    # WAL-mode commits aren't necessarily checkpointed into the main file yet
    # (sqlite3's default connection close doesn't force one); the -wal/-shm
    # companions have to travel with it, exactly as the recover docstring and
    # docs/feedback-loop.md instruct.
    for suffix in ("-wal", "-shm"):
        companion = source_db.with_name(source_db.name + suffix)
        if companion.exists():
            corrupted_path.with_name(corrupted_path.name + suffix).write_bytes(
                companion.read_bytes()
            )

    output_path = tmp_path / "feedback.recovered.sqlite3"
    stats = feedback.recover_corrupted_database(corrupted_path, output_path)

    assert stats.market_snapshots == 1
    assert stats.pack_snapshot_memberships == 1
    assert stats.decisions == 1
    assert output_path.exists()

    conn = sqlite3.connect(output_path)
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "market_snapshots",
            "pack_snapshot_memberships",
            "decisions",
            "settlements",
        )
    }
    conn.close()
    assert counts["market_snapshots"] == 1
    assert counts["pack_snapshot_memberships"] == 1
    assert counts["decisions"] == 1


def test_recover_corrupted_database_refuses_to_overwrite_output(tmp_path):
    corrupted_path = tmp_path / "feedback.sqlite3.corrupted"
    feedback.initialize_database(corrupted_path)
    output_path = tmp_path / "existing.sqlite3"
    feedback.initialize_database(output_path)

    with pytest.raises(feedback.FeedbackError):
        feedback.recover_corrupted_database(corrupted_path, output_path)


def test_recover_corrupted_database_survives_schema_page_corruption(tmp_path):
    """Corruption in sqlite_master itself (not just a data page) must not
    crash the whole recovery -- _open_for_salvage's own sanity check
    (`SELECT 1`) never touches sqlite_master, so it can succeed even when
    the schema page is the damaged one; the schema/PRAGMA queries inside
    _salvage_rows_directly are where that has to be caught instead."""

    source_db = tmp_path / "feedback.sqlite3"
    feedback.initialize_database(source_db)
    conn = sqlite3.connect(source_db)
    conn.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_id, captured_at, sport, event_id, market_id, outcome_id,
            selection, line, price, book, event_starts_at, created_at
        ) VALUES ('s1', '2026-01-01T00:00:00+00:00', 'WNBA', 'e1', 'm1', 'o1',
                  'sel', '1', '1', 'B', '2026-01-01T01:00:00+00:00',
                  '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()

    data = bytearray(source_db.read_bytes())
    # Corrupt the sqlite_master b-tree page itself (right after the 100-byte
    # file header) -- this reliably reproduces "database disk image is
    # malformed" on the schema-discovery queries, not just on row iteration.
    for i in range(100, 300):
        data[i] = 0xFF
    corrupted_path = tmp_path / "feedback.sqlite3.corrupted"
    corrupted_path.write_bytes(bytes(data))

    output_path = tmp_path / "feedback.recovered.sqlite3"
    stats = feedback.recover_corrupted_database(corrupted_path, output_path)

    assert stats.market_snapshots == 0
    assert stats.pack_snapshot_memberships == 0
    assert stats.decisions == 0
    assert stats.settlements == 0
    # One skip per recoverable table whose scan hit the corrupted schema page.
    assert stats.skipped_rows == len(feedback._RECOVERY_TABLE_ORDER)
    assert output_path.exists()


def test_run_sqlite_cli_recover_kills_hung_processes_on_timeout(tmp_path, monkeypatch):
    """A caught TimeoutExpired has to actually kill the still-running
    process(es), not just fall through to `with Popen(...) as p:`'s own
    __exit__ -- that calls p.wait() with no timeout, trading a bounded
    300s hang for an unbounded one instead of ever returning."""

    import subprocess
    from unittest.mock import MagicMock

    recover_mock = MagicMock()
    recover_mock.stdout = MagicMock()
    recover_mock.wait.side_effect = subprocess.TimeoutExpired(cmd="sqlite3", timeout=300)
    recover_mock.poll.return_value = None  # still running

    apply_mock = MagicMock()
    apply_mock.poll.return_value = None  # still running

    queue = [recover_mock, apply_mock]
    monkeypatch.setattr(feedback.subprocess, "Popen", lambda *a, **k: queue.pop(0))

    result = feedback._run_sqlite_cli_recover(
        tmp_path / "corrupted.db", tmp_path / "temp.db"
    )

    assert result is False
    recover_mock.kill.assert_called_once()
    apply_mock.kill.assert_called_once()


# --- alt lane capture -------------------------------------------------------


def _alt_player_row(**overrides) -> dict:
    row = {field: "" for field in ALT_PLAYER_PROPS_HEADER}
    row.update(
        {
            "league": "MLB",
            "event_id": "e1",
            "event_starts_at": "2026-08-25T23:05:00+00:00",
            "matchup": "AWY @ HME",
            "player": "Pitcher One",
            "player_id": "p1",
            "team": "HME",
            "market": "SO",
            "position": "OVER",
            "line": "4.5",
            "best_book": "DK",
            "best_odds": "-140",
            "market_id": "apm1",
            "outcome_id": "apo1",
            "model_prob": "0.72",
            "edge_pct": "0.06",
            "recommended_units": "1.5",
        }
    )
    row.update(overrides)
    return row


def _alt_team_total_row(**overrides) -> dict:
    row = {field: "" for field in ALT_TEAM_TOTALS_HEADER}
    row.update(
        {
            "league": "MLB",
            "event_id": "e1",
            "event_starts_at": "2026-08-25T23:05:00+00:00",
            "matchup": "AWY @ HME",
            "team": "HME",
            "market_id": "attm1",
            "outcome_id": "atto1",
            "position": "OVER",
            "line": "2.5",
            "best_book": "DK",
            "best_price": "-160",
            "decimal_price": "1.625",
            "implied_prob": "0.6154",
            "is_best_line": "true",
            "as_of": "2026-08-25T16:00:00+00:00",
            "model_prob": "0.70",
            "edge_pct": "0.08",
            "recommended_units": "1.0",
        }
    )
    row.update(overrides)
    return row


def _alt_spread_row(**overrides) -> dict:
    row = {field: "" for field in ALT_SPREADS_HEADER}
    row.update(
        {
            "league": "MLB",
            "event_id": "e1",
            "event_starts_at": "2026-08-25T23:05:00+00:00",
            "matchup": "AWY @ HME",
            "team": "HME",
            "market_type": "GAMELINE",
            "proposition": "SPREAD",
            "position": "HOME",
            "market_id": "asm1",
            "outcome_id": "aso1",
            "model_prob": "0.64",
            "edge_pct": "0.05",
            "recommended_units": "1.0",
            "line": "-1.5",
            "signed_line": "-1.5",
            "selection": "HME -1.5",
            "best_book": "DK",
            "best_price": "-120",
        }
    )
    row.update(overrides)
    return row


def _alt_bankroll_row(**overrides) -> dict:
    row = {field: "" for field in ALT_BANKROLL_PROPS_HEADER}
    row.update(
        {
            "league": "MLB",
            "event_id": "e1",
            "event_starts_at": "2026-08-25T23:05:00+00:00",
            "matchup": "AWY @ HME",
            "team": "HME",
            "market_type": "GAMELINE",
            "proposition": "TOTAL",
            "position": "OVER",
            "market_id": "abm1",
            "outcome_id": "abo1",
            "model_prob": "0.78",
            "edge_pct": "0.04",
            "recommended_units": "0.5",
            "line": "7.5",
            "best_book": "DK",
            "best_price": "-200",
        }
    )
    row.update(overrides)
    return row


def test_alt_lanes_build_selections_the_grader_can_parse():
    cases = {
        "alt_player_props": (_alt_player_row(), "Pitcher One - SO OVER 4.5"),
        "alt_team_totals": (_alt_team_total_row(), "HME Team Total OVER 2.5"),
        "alt_spreads": (_alt_spread_row(), "AWY @ HME Spread HOME -1.5"),
        "alt_bankroll_props": (_alt_bankroll_row(), "AWY @ HME Total O/U OVER 7.5"),
    }
    for kind, (row, expected) in cases.items():
        assert feedback._alt_selection(kind, row) == expected


def test_alt_bankroll_moneyline_and_team_prop_selections():
    moneyline = _alt_bankroll_row(proposition="MONEYLINE", position="HOME", line="")
    assert feedback._alt_selection("alt_bankroll_props", moneyline) == "AWY @ HME Money Line HOME"

    team_runs = _alt_bankroll_row(
        market_type="TEAM_PROP", proposition="R", position="OVER", line="4.5"
    )
    assert feedback._alt_selection("alt_bankroll_props", team_runs) == "HME Team Total OVER 4.5"


def test_non_scoring_team_prop_is_skipped_rather_than_graded_against_runs():
    """A team-hits prop must not become a team-total row graded off the score."""

    hits = _alt_bankroll_row(
        market_type="TEAM_PROP", proposition="H", position="OVER", line="8.5"
    )
    assert feedback._alt_selection("alt_bankroll_props", hits) == ""
    assert feedback._normalize_alt_lane_row("mlb_alt_bankroll_props", hits) is None


def test_alt_row_without_a_parseable_side_is_skipped():
    assert feedback._alt_selection("alt_player_props", _alt_player_row(position="")) == ""
    assert feedback._alt_selection("alt_team_totals", _alt_team_total_row(position="")) == ""
    assert feedback._alt_selection("alt_spreads", _alt_spread_row(position="OVER")) == ""


def test_capture_persists_every_alt_lane_with_its_own_source(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    _write_csv(pack_dir / "alt_player_props.csv", ALT_PLAYER_PROPS_HEADER, [_alt_player_row()])
    _write_csv(pack_dir / "alt_team_totals.csv", ALT_TEAM_TOTALS_HEADER, [_alt_team_total_row()])
    _write_csv(pack_dir / "mlb_alt_spreads.csv", ALT_SPREADS_HEADER, [_alt_spread_row()])
    _write_csv(
        pack_dir / "mlb_alt_bankroll_props.csv", ALT_BANKROLL_PROPS_HEADER, [_alt_bankroll_row()]
    )
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        sources = {
            row["source"]: row["n"]
            for row in conn.execute(
                "SELECT source, COUNT(*) AS n FROM pack_snapshot_memberships GROUP BY source"
            )
        }
        selections = {
            row["selection"]: dict(row)
            for row in conn.execute("SELECT selection, sport, market_type, book FROM market_snapshots")
        }
    assert sources == {
        "alt_player_props": 1,
        "alt_team_totals": 1,
        "mlb_alt_spreads": 1,
        "mlb_alt_bankroll_props": 1,
    }
    assert set(selections) == {
        "Pitcher One - SO OVER 4.5",
        "HME Team Total OVER 2.5",
        "AWY @ HME Spread HOME -1.5",
        "AWY @ HME Total O/U OVER 7.5",
    }
    assert selections["Pitcher One - SO OVER 4.5"]["sport"] == "MLB"
    assert selections["Pitcher One - SO OVER 4.5"]["market_type"] == "PLAYER_PROP"
    assert selections["Pitcher One - SO OVER 4.5"]["book"] == "DK"


def test_alt_units_seed_a_play_and_a_zero_unit_row_stands_down(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    _write_csv(
        pack_dir / "alt_player_props.csv",
        ALT_PLAYER_PROPS_HEADER,
        [
            _alt_player_row(),
            _alt_player_row(
                market_id="apm2", outcome_id="apo2", player="Pitcher Two", recommended_units=""
            ),
        ],
    )
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        verdicts = dict(
            conn.execute(
                "SELECT s.market_id, d.pipeline_verdict FROM decisions d "
                "JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id"
            )
        )
    assert verdicts == {"apm1": "PLAY", "apm2": "STAND_DOWN"}


def test_alt_ladder_keeps_only_the_best_line_selected(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    _write_csv(
        pack_dir / "alt_team_totals.csv",
        ALT_TEAM_TOTALS_HEADER,
        [
            _alt_team_total_row(),
            _alt_team_total_row(
                market_id="attm2", outcome_id="atto2", line="3.5", is_best_line="false"
            ),
        ],
    )
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        selected = dict(conn.execute("SELECT market_id, selected FROM market_snapshots"))
    assert selected == {"attm1": 1, "attm2": 0}


def test_alt_total_that_duplicates_the_totals_ledger_is_not_counted_twice(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    totals_row = {field: "" for field in GAME_TOTALS_HEADER}
    totals_row.update(
        {
            "sport": "MLB",
            "event_id": "e1",
            "market_id": "abm1",
            "outcome_id": "gto1",
            "total_kind": "game",
            "selection": "AWY @ HME Total O/U OVER 7.5",
            "line": "7.5",
            "best_side": "OVER",
            "projected_over_prob": "0.6",
            "projected_under_prob": "0.4",
            "as_of": "2026-08-25T16:00:00+00:00",
        }
    )
    _write_csv(pack_dir / "game_totals.csv", GAME_TOTALS_HEADER, [totals_row])
    _write_csv(
        pack_dir / "mlb_alt_bankroll_props.csv", ALT_BANKROLL_PROPS_HEADER, [_alt_bankroll_row()]
    )
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        sources = [row[0] for row in conn.execute("SELECT source FROM pack_snapshot_memberships")]
    assert sources == ["game_totals"]


def test_alt_lane_price_is_captured_whichever_column_the_board_uses(tmp_path):
    """A row with no price cannot compute PnL when it wins, so the odds column
    each board happens to use must all reach the snapshot."""

    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    # alt_player_props spells it best_odds; alt_team_totals spells it best_price.
    _write_csv(pack_dir / "alt_player_props.csv", ALT_PLAYER_PROPS_HEADER, [_alt_player_row()])
    _write_csv(pack_dir / "alt_team_totals.csv", ALT_TEAM_TOTALS_HEADER, [_alt_team_total_row()])
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        priced = dict(
            conn.execute("SELECT market_id, price FROM market_snapshots")
        )
        decimals = dict(
            conn.execute("SELECT market_id, decimal_price FROM market_snapshots")
        )
    assert priced == {"apm1": -140.0, "attm1": -160.0}
    assert all(value is not None for value in decimals.values())


# --- parlay capture and settlement ------------------------------------------


def _parlay_pack(tmp_path: Path, *, legs_json: str | None = None) -> Path:
    """A pack with two alt team-total singles and one parlay over both."""

    pack_dir = tmp_path / "packs" / "2026-08-25"
    _write_csv(pack_dir / "candidates.csv", CANDIDATES_HEADER, [])
    _write_csv(pack_dir / "opportunities.csv", [*CANDIDATES_HEADER, "selected"], [])
    leg_one = _alt_team_total_row()
    leg_two = _alt_team_total_row(
        event_id="e2",
        team="AWY",
        market_id="attm2",
        outcome_id="atto2",
        line="3.5",
        best_price="-200",
        decimal_price="1.5",
    )
    _write_csv(pack_dir / "alt_team_totals.csv", ALT_TEAM_TOTALS_HEADER, [leg_one, leg_two])
    parlay = {field: "" for field in ALT_TEAM_TOTAL_PARLAYS_HEADER}
    parlay.update(
        {
            "parlay_id": "atto1+atto2",
            "league": "MLB",
            "num_legs": 2,
            "legs": "HME o2.5 + AWY o3.5",
            "legs_json": parlay_legs.encode_legs([leg_one, leg_two])
            if legs_json is None
            else legs_json,
            "event_ids": "e1,e2",
            "is_sgp": "false",
            "combined_decimal": "2.4375",
            "combined_american": "+138",
            "combined_implied_prob": "41.026",
            "naive_l10_prob": "56.0",
            "as_of": "2026-08-25T16:00:00+00:00",
            "recommended_units": "1.0",
        }
    )
    _write_csv(pack_dir / "alt_team_total_parlays.csv", ALT_TEAM_TOTAL_PARLAYS_HEADER, [parlay])
    return pack_dir


def _settle_legs(db_path: Path, results: dict[str, str]) -> None:
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
                "actual_result": "5",
                "win_loss_push": outcome,
                "would_have_result": outcome,
            }
        )
        settlements.append(settlement)
    feedback.import_settlements(db_path, settlements)


def test_capture_links_a_parlay_to_the_legs_it_was_built_from(tmp_path):
    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        legs = [
            dict(row)
            for row in conn.execute(
                "SELECT leg_index, event_id, market_id, outcome_id, leg_snapshot_id "
                "FROM parlay_legs ORDER BY leg_index"
            )
        ]
        leg_snapshots = {
            row["snapshot_id"]: row["market_id"]
            for row in conn.execute("SELECT snapshot_id, market_id FROM market_snapshots")
        }
    assert [leg["outcome_id"] for leg in legs] == ["atto1", "atto2"]
    # Each link must point at the snapshot this same capture created.
    assert [leg_snapshots[leg["leg_snapshot_id"]] for leg in legs] == ["attm1", "attm2"]


def test_a_parlay_settles_only_once_every_leg_has(tmp_path):
    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    _settle_legs(db_path, {"attm1": "W"})
    assert feedback.settle_parlays(db_path) == feedback.ParlayStats(settled=0, pending=1)

    _settle_legs(db_path, {"attm2": "W"})
    assert feedback.settle_parlays(db_path) == feedback.ParlayStats(settled=1)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(
            conn.execute(
                "SELECT t.win_loss_push, t.actual_result, t.pnl, t.would_have_result "
                "FROM settlements t JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id "
                "WHERE s.market_type = 'PARLAY'"
            ).fetchone()
        )
    assert row["win_loss_push"] == "W"
    assert row["actual_result"] == "W/W"
    assert row["would_have_result"] == "W"
    # 1.625 * 1.5 = 2.4375 decimal, 1 unit staked -> 1.4375 profit.
    assert row["pnl"] == pytest.approx(1.4375)


def test_a_losing_leg_loses_the_parlay(tmp_path):
    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle_legs(db_path, {"attm1": "W", "attm2": "L"})

    assert feedback.settle_parlays(db_path) == feedback.ParlayStats(settled=1)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT t.win_loss_push, t.pnl FROM settlements t "
            "JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id "
            "WHERE s.market_type = 'PARLAY'"
        ).fetchone()
    assert row[0] == "L"
    assert row[1] == pytest.approx(-1.0)


def test_a_pushed_leg_drops_out_and_shrinks_the_payout(tmp_path):
    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle_legs(db_path, {"attm1": "W", "attm2": "PUSH"})

    feedback.settle_parlays(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT t.win_loss_push, t.pnl FROM settlements t "
            "JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id "
            "WHERE s.market_type = 'PARLAY'"
        ).fetchone()
    assert row[0] == "W"
    # Only the 1.625 leg survives, so the payout is 0.625 -- not the 1.4375
    # the parlay was priced at before the push was known.
    assert row[1] == pytest.approx(0.625)


def test_settling_a_parlay_twice_is_idempotent(tmp_path):
    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)
    _settle_legs(db_path, {"attm1": "W", "attm2": "W"})

    assert feedback.settle_parlays(db_path).settled == 1
    assert feedback.settle_parlays(db_path) == feedback.ParlayStats()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM settlements t "
            "JOIN market_snapshots s ON s.snapshot_id = t.snapshot_id "
            "WHERE s.market_type = 'PARLAY'"
        ).fetchone()[0]
    assert count == 1


def test_a_parlay_whose_legs_are_not_in_the_pack_is_not_captured(tmp_path):
    """An unresolvable leg makes the parlay ungradeable forever."""

    missing = parlay_legs.encode_legs(
        [
            {"event_id": "e9", "market_id": "gone", "outcome_id": "gone1"},
            {"event_id": "e8", "market_id": "gone2", "outcome_id": "gone2"},
        ]
    )
    pack_dir = _parlay_pack(tmp_path, legs_json=missing)
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM parlay_legs").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE market_type = 'PARLAY'"
            ).fetchone()[0]
            == 0
        )


def test_a_parlay_without_leg_identity_is_not_captured(tmp_path):
    pack_dir = _parlay_pack(tmp_path, legs_json="")
    db_path = tmp_path / "feedback.sqlite3"

    feedback.capture_pack(pack_dir, db_path)

    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE market_type = 'PARLAY'"
            ).fetchone()[0]
            == 0
        )


def test_recovery_preserves_parlay_leg_links(tmp_path):
    """A recovered ledger that lost its links could never settle a parlay."""

    pack_dir = _parlay_pack(tmp_path)
    db_path = tmp_path / "feedback.sqlite3"
    feedback.capture_pack(pack_dir, db_path)

    recovered = tmp_path / "recovered.sqlite3"
    stats = feedback.recover_corrupted_database(db_path, recovered)

    assert stats.parlay_legs == 2
    with sqlite3.connect(recovered) as conn:
        assert conn.execute("SELECT COUNT(*) FROM parlay_legs").fetchone()[0] == 2
