"""A fixture slate captured, settled and viewed end-to-end (offline, temp ledger)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from outlier_nba.cli import run_capture, run_settle
from outlier_nba.track.clv import UNVERIFIED, VERIFIED
from tests import nba_fixtures as fx

T1 = fx.STARTED_TIP + timedelta(minutes=30)  # 17:30Z, first capture
T2 = T1 + timedelta(hours=1)  # 18:30Z
T3 = fx.TIP1 - timedelta(minutes=45)  # 22:45Z: inside EV1's 60-min window, 195 min before EV2


def _capture(tmp_path: Path, moment: datetime, client=None, **kw):
    return run_capture(
        client or fx.FakeClient(),
        db_path=tmp_path / "ledger.sqlite3",
        nba_dir=tmp_path / "nba",
        clock=fx.clock_at(moment),
        **kw,
    )


@pytest.fixture
def _open_conns():
    conns: list[sqlite3.Connection] = []
    yield conns
    for conn in conns:
        conn.close()


@pytest.fixture(autouse=True)
def _track_db(_open_conns, monkeypatch):
    real = sqlite3.connect

    def tracked(*a, **kw):
        conn = real(*a, **kw)
        _open_conns.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracked)


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "ledger.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def test_capture_records_shadow_snapshots_and_stand_down_decisions(tmp_path):
    result = _capture(tmp_path, T1)
    assert result.exit_code == 0 and result.published and result.status == "OK"
    conn = _db(tmp_path)
    snaps = conn.execute("SELECT * FROM market_snapshots WHERE sport = 'NBA'").fetchall()
    assert len(snaps) == 23  # every in-scope outcome of the two open games
    assert {s["board"] for s in snaps} == {"SHADOW"}
    assert all("SHADOW" in s["data_quality_flags"] for s in snaps)
    decisions = conn.execute(
        "SELECT d.* FROM decisions d JOIN market_snapshots s USING(snapshot_id) "
        "WHERE s.sport = 'NBA'"
    ).fetchall()
    assert len(decisions) == 23
    assert {d["pipeline_verdict"] for d in decisions} == {"STAND_DOWN"}
    assert {d["units"] for d in decisions} == {0.0}
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM pack_snapshot_memberships WHERE actionable = 1"
        ).fetchone()[0]
        == 0
    )
    # every devig method kept next to each priced snapshot
    assert (
        conn.execute("SELECT COUNT(DISTINCT method) FROM nba_consensus_methods").fetchone()[0] == 3
    )
    # raw archive and manifest live in the immutable run bundle
    run_dir = tmp_path / "nba" / "runs" / result.run_id
    assert (run_dir / "raw" / "outlier_nba_raw.json").exists()
    assert (run_dir / "shadow_candidates.csv").exists()


def test_failed_required_feed_withholds_and_never_touches_the_ledger(tmp_path):
    client = fx.FakeClient(fail_markets_for={fx.EV1, fx.EV2})
    result = _capture(tmp_path, T1, client)
    assert result.exit_code == 3 and not result.published
    assert "game_markets=FAILED" in (result.withheld_reason or "")
    assert not (tmp_path / "ledger.sqlite3").exists()
    ledger_receipt = [r for r in result.receipts if r.name == "ledger_capture"][0]
    assert "skipped" in (ledger_receipt.reason or "")


def test_incomplete_pagination_withholds(tmp_path):
    payload = fx.player_props()
    payload["_page_summary"]["stopped_reason"] = "token_repeat"
    result = _capture(tmp_path, T1, fx.FakeClient(props_payload=payload))
    assert result.exit_code == 3 and not (tmp_path / "ledger.sqlite3").exists()


def test_run_after_first_tip_is_bundle_only_and_writes_no_ledger_rows(tmp_path):
    after = fx.TIP1 + timedelta(minutes=5)  # EV1 underway, EV2 still open
    sched = fx.schedule()
    result = _capture(tmp_path, after, fx.FakeClient(sched=sched))
    # only the still-open game is on the slate, so this is a live run of that game ...
    assert result.published
    conn = _db(tmp_path)
    events = {r[0] for r in conn.execute("SELECT DISTINCT event_id FROM market_snapshots")}
    assert events == {fx.EV2}  # ... and the started games are never recorded


def test_replay_style_run_before_tip_with_stale_clock_is_not_recorded(tmp_path):
    from outlier_nba.ingest.outlier import fetch_raw, normalize_slate
    from outlier_nba.pipeline import NbaPipeline
    from outlier_nba.cli import _capture_stages
    from outlier_nba.run_context import make_run_context

    bundle = fetch_raw(fx.FakeClient(), clock=fx.clock_at(T1))
    slate = normalize_slate(bundle, bundle.fetch_finished)
    ctx = make_run_context(
        "2026-10-21",
        T1 - timedelta(hours=3),
        slate_events=slate.events,
        run_started_utc=bundle.fetch_finished,
    )
    assert ctx.mode == "replay"
    pipe = NbaPipeline(nba_dir=tmp_path / "nba", published_dir=tmp_path / "pub")
    result = pipe.run(ctx, _capture_stages(bundle, slate, tmp_path / "ledger.sqlite3", "shin"))
    assert result.exit_code == 0 and not result.published
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_recapture_is_idempotent_for_the_same_instant(tmp_path):
    _capture(tmp_path, T1)
    # same clock => same snapshot ids; the second run is refused as a duplicate run id
    with pytest.raises(FileExistsError):
        _capture(tmp_path, T1)
    assert _db(tmp_path).execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 23


# ---- settlement ------------------------------------------------------------
def _athlete(name, pts, reb, ast, stl=0, blk=0):
    return {
        "athlete": {"displayName": name},
        "stats": ["34", "9-18", "3-7", str(reb), str(ast), str(pts), str(stl), str(blk)],
    }


def _summary(*athletes):
    return {
        "boxscore": {
            "players": [
                {
                    "statistics": [
                        {
                            "name": "statistics",
                            "labels": ["MIN", "FG", "3PT", "REB", "AST", "PTS", "STL", "BLK"],
                            "athletes": list(athletes),
                        }
                    ]
                }
            ]
        }
    }


def _espn(away, home, away_score, home_score, event_id):
    return {
        "id": event_id,
        "status": {"type": {"completed": True}},
        "competitions": [
            {
                "id": event_id,
                "competitors": [
                    {"homeAway": "away", "score": str(away_score), "team": {"abbreviation": away}},
                    {"homeAway": "home", "score": str(home_score), "team": {"abbreviation": home}},
                ],
            }
        ],
    }


def _fake_fetch(url: str) -> dict:
    assert "/basketball/nba/" in url
    if "/summary?" in url:
        if "event=espn-1" in url:
            return _summary(
                _athlete("Jalen Brunson", 31, 3, 6), _athlete("Karl-Anthony Towns", 18, 11, 2)
            )
        return _summary(_athlete("Stephen Curry", 30, 6, 7))
    return {
        "events": [_espn("NY", "BOS", 101, 118, "espn-1"), _espn("GS", "LAL", 110, 112, "espn-2")]
    }


SETTLE_NOW = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)


def test_capture_settle_view_end_to_end(tmp_path):
    _capture(tmp_path, T1)
    _capture(tmp_path, T2)
    _capture(tmp_path, T3)
    db = tmp_path / "ledger.sqlite3"
    summary = _settle_now(db)
    assert summary["settlement_rows"] == 69 == 23 * 3
    assert summary["unmatched_event_count"] == 0

    conn = _db(tmp_path)
    # the evaluation view holds exactly one row per (event, outcome): the FIRST capture
    view = conn.execute("SELECT * FROM nba_outcome_eval").fetchall()
    assert len(view) == 23
    assert {r["captured_at"] for r in view} == {T1.isoformat()}
    by_sel = {r["selection"]: r for r in view}
    assert by_sel["Jalen Brunson - PTS OVER 26.5"]["win_loss_push"] == "W"
    assert by_sel["Jalen Brunson - PTS OVER 26.5"]["actual_result"] == "31"
    assert by_sel["Jalen Brunson - PTS UNDER 26.5"]["win_loss_push"] == "L"
    assert by_sel["Karl-Anthony Towns - REB OVER 11.5"]["win_loss_push"] == "L"
    assert by_sel["Stephen Curry - TD OVER 0.5"]["win_loss_push"] == "L"
    assert by_sel["NYK @ BOS Total O/U UNDER 221.5"]["win_loss_push"] == "W"
    assert by_sel["NYK @ BOS Total O/U OVER 221"]["win_loss_push"] == "L"
    assert by_sel["NYK @ BOS Spread HOME -4.5"]["win_loss_push"] == "W"
    assert by_sel["NYK @ BOS Spread AWAY 4.5"]["win_loss_push"] == "L"
    assert by_sel["NYK @ BOS Money Line HOME"]["win_loss_push"] == "W"
    assert by_sel["BOS Team Total OVER 112.5"]["win_loss_push"] == "W"
    assert by_sel["GSW @ LAL Total O/U OVER 229.5"]["win_loss_push"] == "L"
    assert all(r["units"] == 0.0 for r in view)


def _settle_now(db):
    from outlier_nba.track.settle import settle_nba

    return settle_nba(db, now=SETTLE_NOW, fetch_json=_fake_fetch)


def test_clv_only_from_a_verified_close_within_sixty_minutes_of_tip(tmp_path):
    _capture(tmp_path, T1)
    _capture(tmp_path, T2)
    _capture(tmp_path, T3)
    db = tmp_path / "ledger.sqlite3"
    _settle_now(db)
    conn = _db(tmp_path)

    def clv_for(selection, captured_at):
        return conn.execute(
            """
            SELECT c.*, x.closing_price, x.clv_price FROM nba_clv c
            JOIN decisions d ON d.decision_id = c.decision_id
            JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
            JOIN settlements x ON x.decision_id = d.decision_id
            WHERE s.selection = ? AND s.captured_at = ?
            """,
            (selection, captured_at.isoformat()),
        ).fetchone()

    # EV1: the 22:45 capture is 45 min before the 23:30 tip -> verified close for earlier takes
    early = clv_for("NYK @ BOS Total O/U OVER 221.5", T1)
    assert early["status"] == VERIFIED
    assert early["minutes_before_tip"] == 45.0
    assert early["close_captured_at"] == T3.isoformat()
    assert early["closing_price"] == -110
    # the last capture has no later snapshot: never its own close
    last = clv_for("NYK @ BOS Total O/U OVER 221.5", T3)
    assert last["status"] == UNVERIFIED and last["closing_price"] is None
    assert last["clv_price"] is None
    # EV2 tips at 02:00; the only later captures are >60 min out -> close_unverified
    ev2 = clv_for("GSW @ LAL Total O/U OVER 229.5", T1)
    assert ev2["status"] == UNVERIFIED and ev2["closing_price"] is None
    # a verified close carries a consensus-probability CLV
    assert early["clv_prob"] is not None


def test_settlement_is_idempotent(tmp_path):
    _capture(tmp_path, T1)
    db = tmp_path / "ledger.sqlite3"
    first = _settle_now(db)
    second = _settle_now(db)
    assert first["settlement_rows"] == 23
    assert second["settlement_rows"] == 0  # already-settled decisions are not pending
    assert _db(tmp_path).execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 23


def test_run_settle_wrapper_delegates(tmp_path, monkeypatch):
    calls = {}

    def fake(db, *, lookback_days):
        calls["args"] = (db, lookback_days)
        return {"ok": True}

    monkeypatch.setattr("outlier_nba.track.settle.settle_nba", fake)
    assert run_settle(tmp_path / "x.sqlite3", lookback_days=5) == {"ok": True}
    assert calls["args"] == (tmp_path / "x.sqlite3", 5)


def test_nightly_clv_repair_never_rewrites_an_nba_verified_close(tmp_path):
    from outlier_scrapers.feedback_settlement import recompute_settlement_clv

    _capture(tmp_path, T1)
    _capture(tmp_path, T3)
    db = tmp_path / "ledger.sqlite3"
    _settle_now(db)
    conn = _db(tmp_path)
    before = conn.execute(
        "SELECT settlement_id, closing_line, closing_price, clv_price FROM settlements "
        "ORDER BY settlement_id"
    ).fetchall()
    # unchanged fixture prices make close == take, the repair job's "bogus close" fingerprint
    assert any(r["closing_price"] is not None for r in before)
    summary = recompute_settlement_clv(db)
    assert summary["inspected"] == 0 and summary["cleared"] == 0 and summary["corrected"] == 0
    after = conn.execute(
        "SELECT settlement_id, closing_line, closing_price, clv_price FROM settlements "
        "ORDER BY settlement_id"
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_price_clv_requires_the_same_book_but_probability_clv_does_not(tmp_path):
    _capture(tmp_path, T1)
    _capture(tmp_path, T3)
    db = tmp_path / "ledger.sqlite3"
    conn = _db(tmp_path)
    conn.execute(
        "UPDATE market_snapshots SET book = 'SomeOtherBook' WHERE captured_at = ?",
        (T3.isoformat(),),
    )
    conn.commit()
    _settle_now(db)
    row = conn.execute(
        """
        SELECT c.status, c.clv_prob, x.closing_price, x.clv_price, x.closing_line
        FROM nba_clv c JOIN decisions d ON d.decision_id = c.decision_id
        JOIN market_snapshots s ON s.snapshot_id = d.snapshot_id
        JOIN settlements x ON x.decision_id = d.decision_id
        WHERE s.selection = 'NYK @ BOS Total O/U OVER 221.5' AND s.captured_at = ?
        """,
        (T1.isoformat(),),
    ).fetchone()
    assert row["status"] == VERIFIED and row["clv_prob"] is not None
    assert row["closing_price"] is None and row["clv_price"] is None
    assert row["closing_line"] is not None


def test_post_cutoff_records_are_dropped_by_the_normalize_stage(tmp_path):
    import dataclasses

    from outlier_nba.cli import _capture_stages
    from outlier_nba.ingest.outlier import fetch_raw, normalize_slate
    from outlier_nba.pipeline import NbaPipeline
    from outlier_nba.run_context import make_run_context

    bundle = fetch_raw(fx.FakeClient(), clock=fx.clock_at(T1))
    slate = normalize_slate(bundle, bundle.fetch_finished)
    # a record whose tip is already behind the fetch stamp must never be recorded
    slate.records[0] = dataclasses.replace(slate.records[0], tip=T1 - timedelta(minutes=1))
    ctx = make_run_context(
        "2026-10-21",
        bundle.fetch_finished,
        slate_events=slate.events,
        run_started_utc=bundle.fetch_finished,
    )
    pipe = NbaPipeline(nba_dir=tmp_path / "nba", published_dir=tmp_path / "pub")
    result = pipe.run(ctx, _capture_stages(bundle, slate, tmp_path / "ledger.sqlite3", "shin"))
    assert result.exit_code == 0
    summary = json.loads(
        (tmp_path / "nba" / "runs" / ctx.run_id / "slate_summary.json").read_text()
    )
    assert summary["rejected_post_cutoff"] == 1
    assert _db(tmp_path).execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 22


def test_eval_view_and_clv_table_exist_right_after_a_capture(tmp_path):
    _capture(tmp_path, T1)
    conn = _db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM nba_outcome_eval").fetchone()[0] == 23
    assert conn.execute("SELECT COUNT(*) FROM nba_clv").fetchone()[0] == 0
