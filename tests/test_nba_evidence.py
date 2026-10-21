"""Model probabilities in the ledger, the join to settled outcomes, and promotion evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import outlier_nba.cli as cli
from outlier_nba import team_run
from outlier_nba.ratings.model import TeamParams
from outlier_nba.track import evidence as ev
from outlier_nba.track import model_ledger, promotion
from tests import nba_fixtures as fx
from tests.nba_synth import synth_history
from tests.test_nba_shadow_e2e import SETTLE_NOW, _fake_fetch

T1 = fx.STARTED_TIP + timedelta(minutes=30)  # 17:30Z, before both open tips
T3 = fx.TIP1 - timedelta(minutes=45)  # 22:45Z, inside the 60-minute close window for EV1
PARAMS = TeamParams(lam=20.0, tau=1e6, carry=0.5)


@pytest.fixture(scope="module")
def games():
    return synth_history()


@pytest.fixture
def promo(tmp_path: Path) -> Path:
    path = tmp_path / "promo.json"
    promotion.initialize(path)
    return path


def _capture(tmp_path: Path, moment: datetime):
    return cli.run_capture(
        fx.FakeClient(),
        db_path=tmp_path / "ledger.sqlite3",
        nba_dir=tmp_path / "nba",
        clock=fx.clock_at(moment),
    )


def _model_run(tmp_path: Path, games, promo: Path, moment: datetime, *, db=True, **kw):
    return team_run.run_team_shadow(
        fx.FakeClient(),
        games_loader=lambda: games,
        nba_dir=tmp_path / "nba_model",  # capture and model runs keep separate bundle roots
        clock=fx.clock_at(moment),
        params=PARAMS,
        n_draws=1500,
        promotion_path=promo,
        feed_receipt_loader=lambda: None,
        ledger_db=tmp_path / "ledger.sqlite3" if db else None,
        **kw,
    )


@pytest.fixture(autouse=True)
def _close_connections(monkeypatch):
    """Close every sqlite connection a test opened (the helpers hand them out unclosed)."""
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def tracked(*args, **kwargs):
        conn = real(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked)
    yield
    for conn in opened:
        conn.close()


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "ledger.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------ ledger + join


def test_model_probabilities_land_beside_the_market_capture(tmp_path, games, promo) -> None:
    _capture(tmp_path, T1)
    result = _model_run(tmp_path, games, promo, T1)
    assert result.exit_code == 0, result.withheld_reason
    conn = _db(tmp_path)
    n_model = conn.execute("SELECT COUNT(*) FROM nba_model_probs").fetchone()[0]
    assert n_model > 0
    joined = conn.execute("SELECT * FROM nba_model_eval").fetchall()
    assert 0 < len(joined) <= n_model
    assert all(0.0 < r["model_prob"] < 1.0 for r in joined)
    assert {r["family"] for r in joined} >= {"team.ML", "team.SPREAD", "team.TOTAL"}
    # one row per outcome, exactly as in the market-only view
    assert len(joined) == len({(r["event_id"], r["outcome_id"]) for r in joined})


def test_the_first_pre_tip_forecast_is_the_one_that_counts(tmp_path, games, promo) -> None:
    _capture(tmp_path, T1)
    _model_run(tmp_path, games, promo, T1)
    first = _db(tmp_path).execute("SELECT model_run_id, COUNT(*) FROM nba_model_eval").fetchone()
    _model_run(tmp_path, games, promo, T1 + timedelta(hours=1))  # a later re-run
    conn = _db(tmp_path)
    assert conn.execute("SELECT COUNT(DISTINCT run_id) FROM nba_model_probs").fetchone()[0] == 2
    again = conn.execute("SELECT model_run_id, COUNT(*) FROM nba_model_eval").fetchone()
    assert tuple(again) == tuple(first)  # the view still points at the first run


def test_recording_the_same_run_twice_is_idempotent(tmp_path) -> None:
    row = {
        "event_id": "e", "outcome_id": "o", "as_of": "2026-10-21T17:30:00+00:00",
        "family": "team.ML", "market": "ML", "line": "", "model_prob": "0.6",
        "model_win": "0.6", "model_push": "0", "model_loss": "0.4", "market_prob": "0.55",
        "model_weight": 0.0, "final_prob": "0.55", "flags": "SHADOW",
    }  # fmt: skip
    db = tmp_path / "ledger.sqlite3"
    assert model_ledger.record_model_probs(db, [row, {**row, "model_prob": ""}], "r1") == {
        "model_rows": 1  # a row with no model probability is not recorded
    }
    model_ledger.record_model_probs(db, [row], "r1")
    assert _count(db) == 1
    model_ledger.record_model_probs(db, [row], "r2")
    assert _count(db) == 2


def _count(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM nba_model_probs").fetchone()[0]


def test_failed_stage_or_bundle_only_run_never_touches_the_ledger(tmp_path, games, promo) -> None:
    stale = [g for g in games if g.season != "2026-27" or g.start_utc.day <= 9]
    result = _model_run(tmp_path, stale, promo, T1)
    assert result.exit_code == 3
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_run_without_a_ledger_db_records_nothing(tmp_path, games, promo) -> None:
    assert _model_run(tmp_path, games, promo, T1, db=False).exit_code == 0
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_settled_outcomes_flow_into_evidence_and_a_small_sample_cannot_promote(
    tmp_path, games, promo
) -> None:
    from outlier_nba.track.settle import settle_nba

    _capture(tmp_path, T1)
    _model_run(tmp_path, games, promo, T1)
    _capture(tmp_path, T3)  # a verified close for the EV1 outcomes
    settle_nba(tmp_path / "ledger.sqlite3", now=SETTLE_NOW, fetch_json=_fake_fetch)

    conn = _db(tmp_path)
    settled = conn.execute(
        "SELECT win_loss_push, model_prob, close_status, clv_prob, family FROM nba_model_eval "
        "WHERE win_loss_push != ''"
    ).fetchall()
    assert settled and {r["win_loss_push"] for r in settled} <= {"W", "L", "P"}

    evidence = ev.build_evidence(tmp_path / "ledger.sqlite3")
    assert "team.ML" in evidence
    for family, e in evidence.items():
        assert (
            e.n_settled is not None and e.n_settled < promotion.THRESHOLDS["min_settled_outcomes"]
        )
        assert e.distinct_dates == 1
        assert e.oos_logloss_beats_market is None  # fewer than 30 observations: unknown
    decisions = promotion.preview(evidence, promo)
    assert all(d["status"] == "shadow" for d in decisions.values())
    assert all("n_settled" in d["failed_keys"] for d in decisions.values())


# ------------------------------------------------------------------- evidence maths


def _obs(n, model_p, market_p, seed=0, clv=None):
    rng = np.random.default_rng(seed)
    truth = np.clip(model_p, 0.02, 0.98)
    won = (rng.uniform(size=n) < truth).astype(float)
    return [
        ev.Obs(("e", str(i), "1"), f"2026-11-{1 + i % 28:02d}", float(model_p[i]), float(market_p[i]),
               float(won[i]), clv)
        for i in range(n)
    ]  # fmt: skip


def test_a_model_with_real_edge_beats_the_market_and_earns_weight() -> None:
    rng = np.random.default_rng(1)
    truth = rng.uniform(0.2, 0.8, 4000)
    market = np.clip(truth + rng.normal(0, 0.06, 4000), 0.05, 0.95)  # a noisy market
    obs = _obs(4000, truth, market, seed=2)
    e = ev.family_evidence(obs)
    assert e.oos_logloss_beats_market is True
    assert e.fitted_weight is not None and e.fitted_weight > 0.1
    assert e.calibration_gap is not None and e.calibration_gap < 0.05


def test_a_model_that_copies_the_market_does_not_beat_it() -> None:
    rng = np.random.default_rng(3)
    p = rng.uniform(0.2, 0.8, 3000)
    e = ev.family_evidence(_obs(3000, p, p, seed=4))
    assert e.oos_logloss_beats_market is False
    assert e.fitted_weight == 0.0 or e.fitted_weight < 0.1


def test_a_worse_model_gets_zero_weight() -> None:
    rng = np.random.default_rng(5)
    truth = rng.uniform(0.2, 0.8, 3000)
    junk = rng.uniform(0.2, 0.8, 3000)  # unrelated to the outcomes
    won = (rng.uniform(size=3000) < truth).astype(float)
    obs = [
        ev.Obs(("e", str(i), "1"), "d", float(junk[i]), float(truth[i]), float(won[i]), None)
        for i in range(3000)
    ]  # the market is the informed one here
    e = ev.family_evidence(obs)
    assert e.oos_logloss_beats_market is False
    assert e.fitted_weight == 0.0


def test_calibration_gap_ignores_buckets_with_fewer_than_100_observations() -> None:
    model = np.concatenate([np.full(99, 0.9), np.full(150, 0.5)])
    won = np.concatenate([np.zeros(99), np.tile([0.0, 1.0], 75)])
    assert ev.calibration_gap(model, won) == pytest.approx(0.0, abs=1e-9)  # only 0.5 bucket counts
    assert ev.calibration_gap(model[:99], won[:99]) is None  # nothing large enough to judge


def test_median_clv_counts_only_the_side_the_model_backs_with_a_verified_close() -> None:
    base = [
        ev.Obs(("e", "1", "1"), "d1", 0.60, 0.55, 1.0, 0.02),  # backed, verified
        ev.Obs(("e", "2", "1"), "d2", 0.60, 0.55, 0.0, -0.01),  # backed, verified
        ev.Obs(("e", "3", "1"), "d3", 0.60, 0.55, 1.0, 0.04),  # backed, verified
        ev.Obs(("e", "4", "1"), "d4", 0.40, 0.55, 1.0, 0.50),  # model disagrees: ignored
        ev.Obs(("e", "5", "1"), "d5", 0.60, 0.55, 1.0, None),  # unverified close: ignored
    ]
    assert ev.family_evidence(base).median_clv == pytest.approx(0.02)
    assert ev.family_evidence(base[3:]).median_clv is None


def test_load_filters_pushes_unsettled_and_counts_one_side_per_line(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nba_model_eval (family TEXT, event_id TEXT, market_id TEXT, line TEXT, "
        "selection TEXT, event_starts_at TEXT, model_prob REAL, market_consensus_prob REAL, "
        "win_loss_push TEXT, close_status TEXT, clv_prob REAL)"
    )
    tip = "2026-10-22T02:00:00+00:00"
    rows = [
        ("team.TOTAL", "e1", "m1", "221.5", "A over", tip, 0.6, 0.55, "W", "verified", 0.01),
        ("team.TOTAL", "e1", "m1", "221.5", "B under", tip, 0.4, 0.45, "L", "verified", -0.01),
        ("team.TOTAL", "e1", "m2", "224", "A over", tip, 0.5, 0.5, "P", "verified", 0.0),  # push
        ("team.TOTAL", "e1", "m3", "225.5", "A over", tip, 0.5, 0.5, "", "", None),  # unsettled
        ("team.TOTAL", "e1", "m4", "226.5", "A over", tip, 0.5, "", "W", "", None),  # no market
        ("team.ML", "e2", "m5", "", "home", tip, 0.7, 0.65, "L", "close_unverified", 0.03),
    ]
    conn.executemany("INSERT INTO nba_model_eval VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    out = ev.build_evidence(db)
    assert out["team.TOTAL"].n_settled == 1  # both sides of one line are one observation
    assert out["team.TOTAL"].distinct_dates == 1
    assert out["team.ML"].n_settled == 1 and out["team.ML"].median_clv is None


def test_empty_ledger_has_no_evidence(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert ev.build_evidence(db) == {}


# ------------------------------------------------------------------------------ CLI


def _ledger_with_edge(tmp_path: Path, n: int = 4000) -> Path:
    """A ledger whose model has real edge over a noisy market on n independent games."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE nba_model_eval (family TEXT, event_id TEXT, market_id TEXT, line TEXT, "
        "selection TEXT, event_starts_at TEXT, model_prob REAL, market_consensus_prob REAL, "
        "win_loss_push TEXT, close_status TEXT, clv_prob REAL)"
    )
    rng = np.random.default_rng(11)
    truth = rng.uniform(0.2, 0.8, n)
    market = np.clip(truth + rng.normal(0, 0.06, n), 0.05, 0.95)
    won = rng.uniform(size=n) < truth
    base = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        ("team.ML", f"e{i}", f"m{i}", "", "home", (base + timedelta(days=i % 90)).isoformat(),
         float(truth[i]), float(market[i]), "W" if won[i] else "L", "verified",
         0.01 if truth[i] > market[i] else -0.01)
        for i in range(n)
    ]  # fmt: skip
    conn.executemany("INSERT INTO nba_model_eval VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def test_promote_command_previews_without_writing_then_applies(tmp_path, capsys) -> None:
    db = _ledger_with_edge(tmp_path)
    cfg = tmp_path / "promo.json"
    promotion.initialize(cfg)
    before = cfg.read_bytes()
    assert cli.main(["promote", "--db", str(db), "--config", str(cfg)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["decisions"]["team.ML"]["status"] == "promoted"
    assert cfg.read_bytes() == before  # a preview never writes
    assert promotion.status("team.ML", cfg) == "shadow"

    assert cli.main(["promote", "--db", str(db), "--config", str(cfg), "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert promotion.status("team.ML", cfg) == "promoted"
    assert 0 < promotion.model_weight("team.ML", cfg) <= promotion.MAX_MODEL_WEIGHT


def test_promote_command_with_nothing_settled_says_so(tmp_path, capsys) -> None:
    db = tmp_path / "ledger.sqlite3"
    sqlite3.connect(db).close()
    cfg = tmp_path / "promo.json"
    promotion.initialize(cfg)
    assert cli.main(["promote", "--db", str(db), "--config", str(cfg)]) == 0
    assert json.loads(capsys.readouterr().out)["families"] == 0


def test_run_command_refuses_fixture_runs_against_the_shared_ledger(capsys) -> None:
    assert cli.main(["run", "--fixture"]) == 2
    assert "explicit non-default --db" in capsys.readouterr().out


def test_run_command_threads_the_ledger_choice_through(tmp_path, monkeypatch) -> None:
    seen = {}

    def fake(client, **kwargs):
        seen.update(kwargs)
        return cli.PipelineResult("r", "OK", 0, True, None, [])

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(team_run, "run_team_shadow", fake)
    db = tmp_path / "l.sqlite3"
    cli.main(["run", "--db", str(db), "--nba-dir", str(tmp_path)])
    assert seen["ledger_db"] == db
    cli.main(["run", "--no-ledger", "--nba-dir", str(tmp_path)])
    assert seen["ledger_db"] is None
