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
from outlier_nba.track import metrics
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


def _obs(n, model_p, market_p, seed=0, per_game=1, clv=None, backed=None):
    """n lines, ``per_game`` of them sharing a game; outcomes drawn from ``model_p``."""
    rng = np.random.default_rng(seed)
    won = (rng.uniform(size=n) < np.clip(model_p, 0.02, 0.98)).astype(float)
    out = []
    for i in range(n):
        is_backed = model_p[i] > market_p[i] if backed is None else backed
        out.append(
            ev.Obs(
                event=f"g{i // per_game}",
                key=(f"g{i // per_game}", str(i), "1"),
                day=f"2026-11-{1 + (i // per_game) % 28:02d}",
                model=float(model_p[i]),
                market=float(market_p[i]),
                won=float(won[i]),
                backed=bool(is_backed),
                clv_prob=clv if is_backed else None,
            )
        )
    return out


def test_a_model_with_real_edge_beats_the_market_and_earns_validated_weight() -> None:
    rng = np.random.default_rng(1)
    truth = rng.uniform(0.2, 0.8, 4000)
    market = np.clip(truth + rng.normal(0, 0.06, 4000), 0.05, 0.95)  # a noisy market
    e = ev.family_evidence(_obs(4000, truth, market, seed=2, clv=0.01))
    assert e.oos_logloss_beats_market is True
    assert e.fitted_weight is not None and e.fitted_weight > 0.1
    assert e.calibration_gap is not None and e.calibration_gap < 0.05
    assert e.n_games == 4000 and e.clv_n > 1000 and e.clv_coverage == 1.0


def test_a_model_that_copies_the_market_does_not_beat_it() -> None:
    rng = np.random.default_rng(3)
    p = rng.uniform(0.2, 0.8, 3000)
    e = ev.family_evidence(_obs(3000, p, p, seed=4))
    assert e.oos_logloss_beats_market is False
    assert e.fitted_weight == 0.0


def test_a_worse_model_gets_zero_weight() -> None:
    rng = np.random.default_rng(5)
    truth = rng.uniform(0.2, 0.8, 3000)
    junk = rng.uniform(0.2, 0.8, 3000)  # unrelated to the outcomes
    won = (rng.uniform(size=3000) < truth).astype(float)
    obs = [
        ev.Obs(
            f"g{i}",
            (f"g{i}", "m", "1"),
            f"d{i % 30:02d}",
            float(junk[i]),
            float(truth[i]),
            float(won[i]),
        )
        for i in range(3000)
    ]  # the market is the informed one here
    e = ev.family_evidence(obs)
    assert e.oos_logloss_beats_market is False
    assert e.fitted_weight == 0.0


def test_a_weight_fitted_early_that_stops_working_is_not_reported() -> None:
    """Edge in the first 60% of dates only: the later, untouched dates veto the weight."""
    rng = np.random.default_rng(8)
    n = 4000
    truth = rng.uniform(0.2, 0.8, n)
    market = np.clip(truth + rng.normal(0, 0.06, n), 0.05, 0.95)
    day = np.array([f"2026-11-{1 + (i * 28) // n:02d}" for i in range(n)])
    late = np.array([d >= "2026-11-17" for d in day])  # the validation block
    model = np.where(late, market, truth)  # informed early, a market copy afterwards
    won = (rng.uniform(size=n) < np.where(late, market, truth)).astype(float)
    obs = [
        ev.Obs(
            f"g{i}", (f"g{i}", "m", "1"), day[i], float(model[i]), float(market[i]), float(won[i])
        )
        for i in range(n)
    ]
    weight, validated = ev.fit_weight(obs)
    assert weight == 0.0 and validated is False


def test_correlated_lines_of_one_game_widen_the_interval_and_cannot_fake_significance() -> None:
    rng = np.random.default_rng(9)
    games = 40
    truth = rng.uniform(0.3, 0.7, games)
    market = np.clip(truth + rng.normal(0, 0.05, games), 0.05, 0.95)
    outcome = (rng.uniform(size=games) < truth).astype(float)
    per_game = 25  # 25 lines per game, all settling together
    model = np.repeat(truth, per_game)
    mkt = np.repeat(market, per_game)
    won = np.repeat(outcome, per_game)
    groups = np.repeat(np.arange(games), per_game)
    naive = metrics.paired_log_loss_diff(mkt, model, won)
    clustered = metrics.clustered_log_loss_diff(mkt, model, won, groups.tolist())
    assert clustered["groups"] == games
    assert clustered["ci"][1] - clustered["ci"][0] > 2 * (naive["ci"][1] - naive["ci"][0])


def test_beats_market_needs_the_game_and_the_date_intervals_to_clear_zero() -> None:
    rng = np.random.default_rng(10)
    truth = rng.uniform(0.2, 0.8, 4000)
    market = np.clip(truth + rng.normal(0, 0.06, 4000), 0.05, 0.95)
    obs = _obs(4000, truth, market, seed=11)
    assert ev.beats_market(obs) is True
    one_day = [ev.Obs(o.event, o.key, "2026-11-01", o.model, o.market, o.won) for o in obs]
    assert ev.beats_market(one_day) is None  # one date is one cluster: no interval exists
    assert (
        metrics.clustered_log_loss_diff([0.5, 0.5], [0.6, 0.6], [1, 1], ["a", "a"])["groups"] == 1
    )
    assert ev.beats_market(obs[:10]) is None


def test_calibration_gap_ignores_buckets_with_fewer_than_100_observations() -> None:
    model = np.concatenate([np.full(99, 0.9), np.full(150, 0.5)])
    won = np.concatenate([np.zeros(99), np.tile([0.0, 1.0], 75)])
    assert ev.calibration_gap(model, won) == pytest.approx(0.0, abs=1e-9)  # only 0.5 bucket counts
    assert ev.calibration_gap(model[:99], won[:99]) is None  # nothing large enough to judge


def _o(i, backed, clv, model=0.6, market=0.55, day="d1"):
    return ev.Obs(f"g{i}", (f"g{i}", "m", "1"), day, model, market, 1.0, backed, clv)


def test_median_clv_counts_only_backed_lines_with_a_verified_close() -> None:
    base = [
        _o(1, True, 0.02),
        _o(2, True, -0.01),
        _o(3, True, 0.04),
        _o(4, False, 0.50),  # the model does not back this line: ignored
        _o(5, True, None),  # backed, but the close is unverified
    ]
    e = ev.family_evidence(base)
    assert e.median_clv == pytest.approx(0.02)
    assert e.clv_n == 3 and e.clv_coverage == pytest.approx(3 / 4)
    assert ev.family_evidence(base[3:4]).median_clv is None


def test_a_single_favourable_clv_cannot_satisfy_the_promotion_gate(tmp_path) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    one = promotion.Evidence(
        n_settled=5000, distinct_dates=90, n_games=2000, calibration_gap=0.01,
        oos_logloss_beats_market=True, median_clv=0.05, clv_n=1, clv_dates=1,
        clv_coverage=1.0, fitted_weight=0.2,
    )  # fmt: skip
    decision = promotion.preview({"team.ML": one}, path)["team.ML"]
    assert decision["status"] == "shadow" and "clv_sample" in decision["failed_keys"]
    sparse = promotion.Evidence(
        **{**one.__dict__, "clv_n": 400, "clv_dates": 40, "clv_coverage": 0.1}
    )
    assert "clv_sample" in promotion.preview({"team.ML": sparse}, path)["team.ML"]["failed_keys"]
    enough = promotion.Evidence(
        **{**one.__dict__, "clv_n": 400, "clv_dates": 40, "clv_coverage": 0.9}
    )
    assert promotion.preview({"team.ML": enough}, path)["team.ML"]["status"] == "promoted"


def test_too_few_independent_games_blocks_promotion(tmp_path) -> None:
    path = tmp_path / "p.json"
    promotion.initialize(path)
    e = promotion.Evidence(
        n_settled=5000, distinct_dates=90, n_games=40, calibration_gap=0.01,
        oos_logloss_beats_market=True, median_clv=0.01, clv_n=400, clv_dates=40,
        clv_coverage=0.9, fitted_weight=0.2,
    )  # fmt: skip
    failed = promotion.preview({"team.ML": e}, path)["team.ML"]["failed_keys"]
    assert failed == ["independent_games"]


def _model_eval_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE nba_model_eval (family TEXT, event_id TEXT, market_id TEXT, line TEXT, "
        "selection TEXT, event_starts_at TEXT, model_prob REAL, model_market_prob REAL, "
        "win_loss_push TEXT, close_status TEXT, clv_prob REAL)"
    )


_ROW = "INSERT INTO nba_model_eval VALUES (?,?,?,?,?,?,?,?,?,?,?)"


def test_clv_comes_from_the_side_the_model_backs_not_the_first_side_alphabetically(
    tmp_path,
) -> None:
    db = tmp_path / "l.sqlite3"
    conn = sqlite3.connect(db)
    _model_eval_table(conn)
    tip = "2026-10-22T02:00:00+00:00"
    conn.executemany(
        _ROW,
        [  # "A over" sorts first but the model backs "B under"
            ("team.TOTAL", "e1", "m1", "221.5", "A over", tip, 0.40, 0.45, "L", "verified", -0.07),
            ("team.TOTAL", "e1", "m1", "221.5", "B under", tip, 0.60, 0.55, "W", "verified", 0.07),
        ],
    )
    conn.commit()
    conn.close()
    e = ev.build_evidence(db)["team.TOTAL"]
    assert e.n_settled == 1  # one observation for the line...
    assert e.median_clv == pytest.approx(0.07)  # ...but CLV is the backed side's
    assert e.clv_n == 1 and e.clv_coverage == 1.0


def test_load_filters_pushes_unsettled_and_missing_market(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    conn = sqlite3.connect(db)
    _model_eval_table(conn)
    tip = "2026-10-22T02:00:00+00:00"
    conn.executemany(
        _ROW,
        [
            ("team.TOTAL", "e1", "m1", "221.5", "A over", tip, 0.6, 0.55, "W", "verified", 0.01),
            ("team.TOTAL", "e1", "m1", "221.5", "B under", tip, 0.4, 0.45, "L", "verified", -0.01),
            ("team.TOTAL", "e1", "m2", "224", "A over", tip, 0.5, 0.5, "P", "verified", 0.0),
            ("team.TOTAL", "e1", "m3", "225.5", "A over", tip, 0.5, 0.5, "", "", None),
            ("team.TOTAL", "e1", "m4", "226.5", "A over", tip, 0.5, None, "W", "", None),
            ("team.ML", "e2", "m5", "", "home", tip, 0.7, 0.65, "L", "close_unverified", 0.03),
        ],
    )
    conn.commit()
    conn.close()
    out = ev.build_evidence(db)
    assert out["team.TOTAL"].n_settled == 1 and out["team.TOTAL"].distinct_dates == 1
    assert out["team.ML"].n_settled == 1 and out["team.ML"].median_clv is None
    assert out["team.ML"].clv_coverage == 0.0  # backed, but the close was not verified


def test_empty_ledger_has_no_evidence(tmp_path) -> None:
    db = tmp_path / "l.sqlite3"
    sqlite3.connect(db).close()
    assert ev.build_evidence(db) == {}


# ---------------------------------------------------- market snapshot alignment


def test_a_forecast_is_matched_to_the_snapshot_current_when_it_was_made(tmp_path, games, promo):
    _capture(tmp_path, T1)  # 17:30
    _capture(tmp_path, T1 + timedelta(minutes=5))  # 17:35
    _model_run(tmp_path, games, promo, T1 + timedelta(minutes=8))  # 17:38
    _capture(tmp_path, T1 + timedelta(minutes=20))  # later: must not be used
    rows = _db(tmp_path).execute("SELECT captured_at, snapshot_gap_seconds FROM nba_model_eval")
    got = {(r["captured_at"], round(r["snapshot_gap_seconds"])) for r in rows}
    assert got == {((T1 + timedelta(minutes=5)).isoformat(), 180)}


def test_a_stale_market_snapshot_is_not_compared_with_a_later_forecast(tmp_path, games, promo):
    _capture(tmp_path, T1)  # 17:30
    _model_run(tmp_path, games, promo, T1 + timedelta(hours=2))  # forecast 19:30, quote 2h old
    conn = _db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM nba_model_probs").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM nba_model_eval").fetchone()[0] == 0
    assert ev.build_evidence(tmp_path / "ledger.sqlite3") == {}


def test_a_forecast_with_no_earlier_snapshot_is_not_compared(tmp_path, games, promo) -> None:
    _model_run(tmp_path, games, promo, T1)  # nothing captured before it
    _capture(tmp_path, T1 + timedelta(minutes=1))  # a snapshot that postdates the forecast
    assert _db(tmp_path).execute("SELECT COUNT(*) FROM nba_model_eval").fetchone()[0] == 0


# ------------------------------------------------------------------------------ CLI


def _ledger_with_edge(tmp_path: Path, n: int = 4000) -> Path:
    """A ledger whose model has real edge over a noisy market on n independent games."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    _model_eval_table(conn)
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
    conn.executemany(_ROW, rows)
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
