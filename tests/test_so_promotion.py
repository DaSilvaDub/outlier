"""Independent-SO promotion gate + market-tempered sizing helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from outlier_scrapers.so_promotion import (
    clear_promotion_cache,
    independent_so_sizing_enabled,
    load_so_promotion_config,
    promotion_readiness,
    temper_independent_prob,
)
from outlier_scrapers.so_eval import evaluate_so_probs
from outlier_scrapers.slate_quality import (
    GAMELOG_FEATURE_HASH,
    INDEPENDENT_SO_SOURCE,
    apply_predictor_gates,
)


def _write_settled_so_db(
    path: Path,
    *,
    feature_hash: str,
    market: float,
    independent: float,
    result: str = "L",
) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            selection TEXT,
            market_type TEXT,
            market_consensus_prob REAL,
            independent_model_prob REAL,
            projection_feature_hash TEXT,
            model_prob_source TEXT
        );
        CREATE TABLE settlements (
            snapshot_id INTEGER,
            win_loss_push TEXT
        );
        INSERT INTO market_snapshots VALUES
            (1, 'Cam Schlittler - Strikeouts OVER 5.5', 'SO', {market}, {independent},
             '{feature_hash}', 'outlier_devig');
        INSERT INTO settlements VALUES (1, '{result}');
        """
    )
    conn.commit()
    conn.close()


def test_temper_independent_prob_blends_toward_market():
    assert temper_independent_prob(0.78, None) == 0.78
    blended = temper_independent_prob(0.78, 0.59, independent_weight=0.55)
    assert abs(blended - (0.55 * 0.78 + 0.45 * 0.59)) < 1e-9


def test_promotion_readiness_requires_v2_sample_and_prefer(tmp_path: Path):
    cfg = {
        "min_settled_gamelog": 20,
        "require_v2_hash": True,
        "require_prefer_independent": True,
        "prefer_tempered_over_market": True,
    }
    blocked = promotion_readiness(
        {
            "status": "ok",
            "n": 7,
            "gamelog_v2_rows": 0,
            "prefer_independent": False,
            "prefer_soft_independent": False,
            "prefer_tempered_independent": False,
        },
        cfg,
    )
    assert blocked["ready"] is False
    assert any("gamelog_v2_rows" in reason for reason in blocked["reasons"])

    soft_only = promotion_readiness(
        {
            "status": "ok",
            "n": 25,
            "gamelog_v2_rows": 22,
            "prefer_independent": False,
            "prefer_soft_independent": True,
            "prefer_tempered_independent": False,
        },
        cfg,
    )
    assert soft_only["ready"] is False
    assert any("soft_only" in reason for reason in soft_only["reasons"])

    ready = promotion_readiness(
        {
            "status": "ok",
            "n": 25,
            "gamelog_v2_rows": 22,
            "prefer_independent": False,
            "prefer_soft_independent": False,
            "prefer_tempered_independent": True,
        },
        cfg,
    )
    assert ready["ready"] is True


def test_bool_config_rejects_string_false(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "so_promotion.json"
    cfg_path.write_text(
        json.dumps({"auto_promote": "false", "require_v2_hash": "true"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.so_promotion.DEFAULT_CONFIG_PATH", cfg_path)
    cfg = load_so_promotion_config(cfg_path)
    assert cfg["auto_promote"] is False
    assert cfg["require_v2_hash"] is True


def test_independent_so_sizing_enabled_force_and_auto(monkeypatch, tmp_path: Path):
    clear_promotion_cache()
    monkeypatch.delenv("OUTLIER_PROMOTE_INDEPENDENT_SO", raising=False)
    monkeypatch.delenv("OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO", raising=False)
    assert independent_so_sizing_enabled() is False

    monkeypatch.setenv("OUTLIER_PROMOTE_INDEPENDENT_SO", "1")
    assert independent_so_sizing_enabled() is True

    monkeypatch.delenv("OUTLIER_PROMOTE_INDEPENDENT_SO", raising=False)
    cfg_path = tmp_path / "so_promotion.json"
    cfg_path.write_text(
        json.dumps(
            {
                "auto_promote": False,
                "min_settled_gamelog": 1,
                "require_v2_hash": False,
                "require_prefer_independent": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "outlier_scrapers.so_promotion.DEFAULT_CONFIG_PATH", cfg_path
    )
    monkeypatch.setenv("OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO", "1")
    clear_promotion_cache()
    assert independent_so_sizing_enabled(db_path=tmp_path / "missing.sqlite3") is False


def test_auto_promote_enables_when_v2_tempered_beats_market(monkeypatch, tmp_path: Path):
    """Synthetic v2 ledger where tempered beats market → auto path True."""
    clear_promotion_cache()
    monkeypatch.delenv("OUTLIER_PROMOTE_INDEPENDENT_SO", raising=False)
    monkeypatch.setenv("OUTLIER_AUTO_PROMOTE_INDEPENDENT_SO", "1")

    db = tmp_path / "ready.sqlite3"
    # Loss with overconfident indep: tempered closer to market than raw.
    # Build 20 identical rows where tempered < market Brier.
    # Use W with underconfident indep so tempered improves on market.
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            selection TEXT,
            market_type TEXT,
            market_consensus_prob REAL,
            independent_model_prob REAL,
            projection_feature_hash TEXT,
            model_prob_source TEXT
        )
        """
    )
    conn.execute("CREATE TABLE settlements (snapshot_id INTEGER, win_loss_push TEXT)")
    for i in range(20):
        # Market 0.55, indep 0.80, outcome W → tempered closer/better than market? 
        # For W: brier(m)=0.2025, brier(i)=0.04, tempered=0.55*0.8+0.45*0.55=0.6875 → brier=0.0977
        # prefer tempered and raw both true.
        conn.execute(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?)",
            (
                i + 1,
                "Pitcher - Strikeouts OVER 5.5",
                "SO",
                0.55,
                0.80,
                GAMELOG_FEATURE_HASH,
                "outlier_devig",
            ),
        )
        conn.execute("INSERT INTO settlements VALUES (?,?)", (i + 1, "W"))
    conn.commit()
    conn.close()

    cfg_path = tmp_path / "so_promotion.json"
    cfg_path.write_text(
        json.dumps(
            {
                "auto_promote": False,
                "min_settled_gamelog": 20,
                "require_v2_hash": True,
                "require_prefer_independent": True,
                "prefer_tempered_over_market": True,
                "temper_independent_weight": 0.55,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("outlier_scrapers.so_promotion.DEFAULT_CONFIG_PATH", cfg_path)
    clear_promotion_cache()
    assert independent_so_sizing_enabled(db_path=db) is True


def test_promote_via_auto_gate_without_enable_flag(monkeypatch, tmp_path: Path):
    import outlier_scrapers.slate_quality as sq

    monkeypatch.setattr(sq, "ENABLE_INDEPENDENT_SO_SIZING", False)
    monkeypatch.setattr(
        "outlier_scrapers.so_promotion.independent_so_sizing_enabled",
        lambda: True,
    )
    row = {
        "market_type": "SO",
        "selection": "Cam Schlittler - Strikeouts OVER 5.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 1.0,
        "edge_pct": 0.05,
        "decimal_price": 2.1,
        "push_prob": 0.0,
        "independent_model_prob": 0.78,
        "market_consensus_prob": 0.59,
        "independent_push_prob": 0.0,
        "projection_feature_hash": GAMELOG_FEATURE_HASH,
        "signal_flags": "insight_support;movement_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["model_prob_source"] == INDEPENDENT_SO_SOURCE
    expected = 0.55 * 0.78 + 0.45 * 0.59
    assert abs(float(row["model_prob"]) - expected) < 1e-9


def test_promote_refuses_without_market_consensus(monkeypatch):
    import outlier_scrapers.slate_quality as sq

    monkeypatch.setattr(sq, "ENABLE_INDEPENDENT_SO_SIZING", True)
    row = {
        "market_type": "SO",
        "selection": "Cam Schlittler - Strikeouts OVER 5.5",
        "model_prob_source": "outlier_devig",
        "recommended_units_pre_news": 3.0,
        "edge_pct": 0.05,
        "decimal_price": 2.1,
        "push_prob": 0.0,
        "independent_model_prob": 0.78,
        "projection_feature_hash": GAMELOG_FEATURE_HASH,
        "signal_flags": "insight_support;movement_support",
        "sizing_flags": "",
        "actionable": "true",
        "board": "A",
        "_board": "board_a",
    }
    apply_predictor_gates(row)
    assert row["model_prob_source"] == "outlier_devig"
    assert "independent_gamelog_so_sizing" not in row["sizing_flags"]


def test_so_eval_include_tempered_fields(tmp_path: Path):
    db = tmp_path / "fb.sqlite3"
    _write_settled_so_db(
        db,
        feature_hash="so-starter-gamelog-v1",
        market=0.59,
        independent=0.78,
        result="L",
    )

    report = evaluate_so_probs(db, require_gamelog_hash=True, include_tempered=True)
    assert report["status"] == "ok"
    assert report["n"] == 1
    assert "soft_brier" in report
    assert "tempered_brier" in report
    assert report["prefer_independent"] is False
    # Tempered matches live: temper(raw), so tempered_brier < raw on this loss.
    assert report["tempered_brier"] < report["independent_brier"]

    v2_only = evaluate_so_probs(db, require_v2_hash=True, include_tempered=True)
    assert v2_only["status"] == "insufficient_settled_so"
    assert v2_only["n"] == 0


def test_v1_prefer_cannot_ready_sparse_v2(tmp_path: Path):
    """v1-only wins must not unlock when require_v2 and v2 count is sparse."""
    cfg = {
        "min_settled_gamelog": 5,
        "require_v2_hash": True,
        "require_prefer_independent": True,
        "prefer_tempered_over_market": True,
    }
    blocked = promotion_readiness(
        {
            "status": "ok",
            "n": 20,  # pooled-looking, but v2 sparse
            "gamelog_v2_rows": 2,
            "prefer_independent": True,
            "prefer_tempered_independent": True,
        },
        cfg,
    )
    assert blocked["ready"] is False
    assert any("gamelog_v2_rows" in reason for reason in blocked["reasons"])


def test_default_config_keeps_auto_promote_off():
    cfg = load_so_promotion_config()
    assert cfg["auto_promote"] is False
    assert cfg["require_v2_hash"] is True
    assert cfg["min_settled_gamelog"] >= 20
