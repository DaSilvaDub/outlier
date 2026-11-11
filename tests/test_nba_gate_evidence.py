"""Gate-evidence plumbing: key-checked join, counted exclusions, and the gate-status report.

Everything is offline and synthetic. Stub ``nba_model_eval`` tables stand in for the ledger view
where a test needs full control of one row; the end-to-end tests use the real capture, model
run and settlement path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import outlier_nba.cli as cli
from outlier_nba.track import evidence as ev
from outlier_nba.track import gate_report, model_ledger, promotion

TIP = "2026-10-22T02:00:00+00:00"
COLS = (
    "family, event_id, market_id, line, selection, event_starts_at, model_prob, "
    "model_market_prob, win_loss_push, close_status, clv_prob, model_market_id, model_line, "
    "clv_minutes_before_tip"
)


def _stub(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.execute(f"CREATE TABLE nba_model_eval ({COLS})")
    return conn


def _row(**kw):
    base = {
        "family": "team.TOTAL", "event_id": "e1", "market_id": "m1", "line": "221.5",
        "selection": "over", "event_starts_at": TIP, "model_prob": 0.6, "model_market_prob": 0.55,
        "win_loss_push": "W", "close_status": "verified", "clv_prob": 0.02,
        "model_market_id": "m1", "model_line": "221.5", "clv_minutes_before_tip": 20.0,
    }  # fmt: skip
    base.update(kw)
    return tuple(base[c.strip()] for c in COLS.split(","))


def _insert(conn: sqlite3.Connection, rows) -> None:
    marks = ",".join("?" * len(COLS.split(",")))
    conn.executemany(f"INSERT INTO nba_model_eval ({COLS}) VALUES ({marks})", rows)
    conn.commit()
    conn.close()


def _report(db: Path, family: str = "team.TOTAL"):
    return ev.build_report(db)[family]


# ------------------------------------------------------------------ key integrity


def test_a_probability_for_a_different_market_is_excluded_and_counted(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(), _row(event_id="e2", market_id="m2", model_market_id="OTHER")])
    rep = _report(db)
    assert rep.evidence.n_settled == 1
    assert rep.exclusions.counts["key_mismatch"] == 1


def test_a_probability_for_a_different_line_is_excluded_and_counted(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(), _row(event_id="e2", model_line="222.5")])
    rep = _report(db)
    assert rep.evidence.n_settled == 1 and rep.exclusions.counts["key_mismatch"] == 1


def test_numerically_equal_lines_match_and_a_blank_against_a_number_does_not(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(
        _stub(db),
        [
            _row(line="221.5", model_line="221.50"),
            _row(event_id="e2", line="", model_line=""),  # moneyline: both blank
            _row(event_id="e3", line="221.5", model_line=""),
            _row(event_id="e4", line="", model_line="3.5"),
        ],
    )
    rep = _report(db)
    assert rep.evidence.n_settled == 2 and rep.exclusions.counts["key_mismatch"] == 2


def test_a_row_recorded_before_keys_were_stored_cannot_be_verified_so_is_excluded(
    tmp_path,
) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(), _row(event_id="e2", model_market_id=None)])
    rep = _report(db)
    assert rep.evidence.n_settled == 1
    assert rep.exclusions.counts["key_unverifiable"] == 1


# ---------------------------------------------------------- fail-closed exclusions


def test_every_unusable_row_is_excluded_for_one_counted_reason(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(
        _stub(db),
        [
            _row(),
            _row(event_id="e2", model_prob=None),
            _row(event_id="e3", model_market_prob=None),
            _row(event_id="e4", win_loss_push=None),
            _row(event_id="e5", win_loss_push=""),
            _row(event_id="e6", win_loss_push="P"),
        ],
    )
    c = _report(db).exclusions.counts
    assert _report(db).evidence.n_settled == 1
    assert c["no_model_prob"] == 1 and c["no_market_prob"] == 1
    assert c["unsettled"] == 2 and c["push_or_void"] == 1


def test_a_missing_close_is_counted_and_never_imputed(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(
        _stub(db),
        [_row(close_status="close_unverified", clv_prob=None, clv_minutes_before_tip=None)],
    )
    rep = _report(db)
    assert rep.evidence.n_settled == 1  # calibration and log loss still use the row
    assert rep.evidence.median_clv is None and rep.evidence.clv_n == 0  # no CLV invented
    assert rep.exclusions.counts["clv_no_verified_close"] == 1


def test_a_verified_close_with_no_clv_value_is_counted(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(clv_prob=None)])
    rep = _report(db)
    assert rep.evidence.clv_n == 0 and rep.exclusions.counts["clv_value_missing"] == 1


@pytest.mark.parametrize("minutes", [75.0, -3.0, None])
def test_clv_outside_the_capture_timing_policy_is_excluded(tmp_path, minutes) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(clv_minutes_before_tip=minutes)])
    rep = _report(db)
    assert rep.evidence.clv_n == 0 and rep.evidence.median_clv is None
    assert rep.exclusions.counts["clv_outside_window"] == 1
    assert rep.evidence.n_settled == 1


def test_a_line_the_model_does_not_back_has_no_clv_exclusion(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(
        _stub(db),
        [_row(model_prob=0.5, model_market_prob=0.55, close_status="", clv_prob=None)],
    )
    rep = _report(db)
    assert rep.exclusions.counts["clv_no_verified_close"] == 0  # nothing was backed


def test_the_ledger_file_is_not_modified_by_building_a_report(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row()])
    before = db.read_bytes()
    ev.build_report(db)
    assert db.read_bytes() == before


def test_a_ledger_with_no_view_gives_an_empty_report(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert ev.build_report(db) == {}


# ------------------------------------------------------ real ledger: keys + migration


def test_recorded_model_rows_carry_the_market_id_and_line(tmp_path) -> None:
    row = {
        "event_id": "e", "market_id": "m9", "outcome_id": "o", "side": "over",
        "as_of": "2026-10-21T17:30:00+00:00", "family": "team.TOTAL", "market": "TOTAL",
        "line": "221.5", "model_prob": "0.6", "model_win": "0.6", "model_push": "0",
        "model_loss": "0.4", "market_prob": "0.55", "model_weight": 0.0, "final_prob": "0.55",
        "flags": "SHADOW",
    }  # fmt: skip
    db = tmp_path / "ledger.sqlite3"
    model_ledger.record_model_probs(db, [row], "r1")
    with sqlite3.connect(db) as conn:
        got = conn.execute("SELECT market_id, side, line FROM nba_model_probs").fetchone()
    assert got == ("m9", "over", "221.5")


def test_an_old_model_table_without_key_columns_is_migrated_in_place(tmp_path) -> None:
    db = tmp_path / "ledger.sqlite3"
    old = model_ledger.MODEL_TABLE_SQL.replace("    market_id TEXT,\n", "").replace(
        "    side TEXT,\n", ""
    )
    assert "market_id" not in old
    with sqlite3.connect(db) as conn:
        conn.execute(old)
        conn.execute(
            "INSERT INTO nba_model_probs (event_id, outcome_id, run_id, as_of, family, market, "
            "line, model_prob, model_weight) VALUES ('e','o','r','t','team.ML','ML','',0.6,0.0)"
        )
        model_ledger.ensure_model_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nba_model_probs)")}
        got = conn.execute("SELECT market_id FROM nba_model_probs").fetchone()
    assert {"market_id", "side"} <= cols
    assert got == (None,)  # legacy rows stay unkeyed: they are excluded, not guessed


# ----------------------------------------------------------------- gate-status report


def _edge_rows(n: int = 4000, family: str = "team.ML"):
    rng = np.random.default_rng(11)
    truth = rng.uniform(0.2, 0.8, n)
    market = np.clip(truth + rng.normal(0, 0.06, n), 0.05, 0.95)
    won = rng.uniform(size=n) < truth
    base = datetime(2026, 11, 1, tzinfo=UTC)
    return [
        _row(
            family=family, event_id=f"e{i}", market_id=f"m{i}", model_market_id=f"m{i}", line="",
            model_line="", event_starts_at=(base + timedelta(days=i % 90)).isoformat(),
            model_prob=float(truth[i]), model_market_prob=float(market[i]),
            win_loss_push="W" if won[i] else "L",
            clv_prob=0.01 if truth[i] > market[i] else -0.01,
        )
        for i in range(n)
    ]  # fmt: skip


def test_an_empty_ledger_reports_insufficient_evidence_for_every_family(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    gates = gate_report.build_gates(db)
    assert [g.family for g in gates] == list(promotion.TEAM_FAMILIES)
    assert all(g.verdict == gate_report.INSUFFICIENT and g.n_settled == 0 for g in gates)
    assert all("n_settled" in g.failed_keys for g in gates)


def test_a_family_with_real_edge_passes_and_the_rest_stay_insufficient(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), _edge_rows())
    gates = {g.family: g for g in gate_report.build_gates(db)}
    ml = gates["team.ML"]
    assert ml.verdict == gate_report.PASS and ml.failed_keys == []
    assert ml.logloss_model < ml.logloss_market
    assert gates["team.SPREAD"].verdict == gate_report.INSUFFICIENT


def test_gate_failures_name_the_blocking_keys_and_separate_sample_from_quality(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    rows = _edge_rows()
    # a model that is the market + noise: enough sample, no skill
    rng = np.random.default_rng(5)
    rows = [r[:6] + (float(np.clip(r[7] + rng.normal(0, 0.1), 0.05, 0.95)),) + r[7:] for r in rows]
    _insert(_stub(db), rows)
    ml = {g.family: g for g in gate_report.build_gates(db)}["team.ML"]
    assert ml.verdict == gate_report.FAIL
    assert "oos_logloss_vs_market" in ml.failed_keys
    assert not (set(ml.failed_keys) & gate_report.SAMPLE_KEYS)
    assert ml.blocking_quality_keys == [
        k for k in ml.failed_keys if k not in gate_report.SAMPLE_KEYS
    ]


def test_exclusion_counts_ride_along_in_the_report(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), [_row(), _row(event_id="e2", model_market_id="X")])
    g = {g.family: g for g in gate_report.build_gates(db)}["team.TOTAL"]
    assert g.exclusions["key_mismatch"] == 1 and g.n_settled == 1


def test_the_text_report_lists_each_family_with_its_verdict(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    text = gate_report.format_gates(gate_report.build_gates(db))
    for fam in promotion.TEAM_FAMILIES:
        assert fam in text
    assert "insufficient evidence" in text and "n_settled" in text


def test_thresholds_come_from_a_valid_config_else_the_defaults(tmp_path) -> None:
    cfg = tmp_path / "promo.json"
    promotion.initialize(cfg)
    assert gate_report.load_thresholds(cfg) == promotion.THRESHOLDS
    assert gate_report.load_thresholds(tmp_path / "missing.json") == promotion.THRESHOLDS
    cfg.write_text("{}", encoding="utf-8")  # fails its hash: defaults, never the edited file
    assert gate_report.load_thresholds(cfg) == promotion.THRESHOLDS


def test_failed_keys_matches_what_the_evaluator_would_decide(tmp_path) -> None:
    cfg = tmp_path / "promo.json"
    promotion.initialize(cfg)
    e = promotion.Evidence(n_settled=10)
    assert (
        promotion.failed_keys(e) == promotion.preview({"team.ML": e}, cfg)["team.ML"]["failed_keys"]
    )


# --------------------------------------------------------------------------- CLI


def test_gate_status_command_prints_a_table_and_writes_nothing(tmp_path, capsys) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(_stub(db), _edge_rows(400))
    cfg = tmp_path / "promo.json"
    promotion.initialize(cfg)
    before_db, before_cfg = db.read_bytes(), cfg.read_bytes()
    assert cli.main(["gate-status", "--db", str(db), "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "team.ML" in out and "insufficient evidence" in out
    assert db.read_bytes() == before_db and cfg.read_bytes() == before_cfg
    assert promotion.model_weight("team.ML", cfg) == 0.0


def test_gate_status_json_is_machine_readable(tmp_path, capsys) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert cli.main(["gate-status", "--db", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["write_enabled"] is False
    assert {g["family"] for g in payload["families"]} == set(promotion.TEAM_FAMILIES)


def test_gate_status_on_a_missing_ledger_fails_loudly(tmp_path, capsys) -> None:
    assert cli.main(["gate-status", "--db", str(tmp_path / "nope.sqlite3")]) == 2
    assert "not found" in capsys.readouterr().out


def test_a_moneyline_placeholder_zero_matches_blank_but_a_pickem_spread_does_not(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    _insert(
        _stub(db),
        [
            _row(family="team.ML", line="0", model_line=""),
            _row(family="team.SPREAD", event_id="e2", line="0", model_line=""),
        ],
    )
    reports = ev.build_report(db)
    assert reports["team.ML"].evidence.n_settled == 1
    assert reports["team.SPREAD"].exclusions.counts["key_mismatch"] == 1
