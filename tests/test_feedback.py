import csv
import json
import sqlite3
from pathlib import Path

import pytest

from outlier_scrapers import feedback, pack
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
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
            "recommended_units_pre_news": "0.5",
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
            "SELECT outcome_id, line FROM market_snapshots ORDER BY line"
        ).fetchall()
        shadow_signal = conn.execute(
            "SELECT signal_flags FROM market_snapshots WHERE outcome_id = 'm1:10.5:OVER'"
        ).fetchone()[0]
    assert rows == [("m1:10.5:OVER", "10.5"), ("o2", "11.5")]
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
        decision = conn.execute(
            "SELECT decision_id, pipeline_verdict, units, created_at, updated_at FROM decisions"
        ).fetchone()
        indexes = {
            row[1]
            for table in ("market_snapshots", "decisions", "settlements")
            for row in conn.execute(f"PRAGMA index_list({table})")
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert set(feedback.MARKET_SNAPSHOT_COLUMN_DEFINITIONS) <= snapshot_columns
    assert set(feedback.DECISION_COLUMN_DEFINITIONS) <= decision_columns
    assert set(feedback.SETTLEMENT_COLUMN_DEFINITIONS) <= settlement_columns
    assert decision == (
        stable_decision_id,
        expected_verdict,
        expected_units,
        created_at,
        expected_updated,
    )
    assert {
        "idx_snapshots_market",
        "idx_snapshots_segment",
        "idx_decisions_snapshot",
        "idx_settlements_market",
        "idx_settlements_decision",
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
    models = _read_csv(report_dir / "model_performance.csv")
    a_bet = next(row for row in models if row["model"] == "A" and row["verdict"] == "BET")
    assert float(a_bet["recommendation_accuracy"]) == pytest.approx(1.0)
    assert (report_dir / "ledgers" / "market_snapshots.csv").exists()


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
