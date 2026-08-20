from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from outlier_scrapers import feedback, pack, t30_reprice
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
from outlier_scrapers.schema import ValidationError


NOW = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)


def _candidate(**overrides):
    row = {field: "" for field in pack.CANDIDATES_HEADER}
    row.update(
        {
            "sport": "MLB",
            "event_id": "evt-1",
            "_event_starts_at": (NOW + timedelta(minutes=30)).isoformat(),
            "market_id": "market-1",
            "outcome_id": "outcome-over-75",
            "market_type": "PLAYER_PROP",
            "matchup": "AAA @ BBB",
            "team": "AAA",
            "opponent": "BBB",
            "selection": "Pitcher OVER 7.5 SO",
            "line": "7.5",
            "price": "-110",
            "decimal_price": "1.91",
            "book": "Book A",
            "as_of": (NOW - timedelta(hours=2)).isoformat(),
            "model_prob": "0.56",
            "market_consensus_prob": "0.56",
            "final_blended_prob": "0.56",
            "push_prob": "0",
            "max_units": "3",
            "recommended_units_pre_news": "1",
            "edge_pct": "0.05",
            "board": "A",
            "actionable": "true",
            "signal_flags": "insight_support",
            "model_prob_source": "outlier_devig",
        }
    )
    row.update(overrides)
    return row


def _record(**overrides):
    row = {
        "market_id": "market-1",
        "outcome_id": "outcome-over-75",
        "side": "OVER",
        "current_line": 7.5,
        "is_active": True,
        "book": "Book A",
        "book_odds": -110,
        "book_decimal_odds": 1.91,
        "devig_decimal": 1.8,
        "calculated_ev_pct": 6.0,
        "ev_source": "NATIVE",
    }
    row.update(overrides)
    return row


def _state(*records, status="ok", generated_at=None):
    return t30_reprice._StreamState(
        records=list(records),
        ev_records=list(records),
        status={"status": status, "generated_at": generated_at or NOW.isoformat()},
    )


def _context(*, injury="", pitcher="Starter A", confirmed=True):
    return {
        "injuries_by_league": {"MLB": {"evt-1": injury} if injury else {}},
        "probable_pitchers_by_league": {
            "MLB": {
                "AAA": {"pitcher": pitcher, "confirmed": confirmed},
                "BBB": {"pitcher": "Starter B", "confirmed": True},
            }
        },
    }


def _reprice(
    *,
    candidate=None,
    state=None,
    original_context=None,
    current_context=None,
    refresh_failed=False,
):
    return t30_reprice.reprice_row(
        candidate or _candidate(),
        state=state or _state(_record()),
        original_context=original_context or _context(),
        current_context=current_context or _context(),
        blend_artifact=None,
        now=NOW,
        refresh_failed=refresh_failed,
    )


def test_reprice_emits_keep_and_never_increases_exposure():
    result = _reprice(candidate=_candidate(recommended_units_pre_news="1"))

    assert result["t30_status"] == "KEEP"
    assert result["recommended_units_pre_news"] == 1.0
    assert result["actionable"] == "true"
    assert result["board"] == "A"


def test_reprice_sizes_on_market_not_blend_and_kills_dead_edge():
    artifact = {
        "schema_version": 1,
        "status": "active",
        "generated_at": "2026-07-19T00:00:00+00:00",
        "model_version": "blend-test",
        "prior_strength": 30,
        "global": {"market_weight": 0.1, "n": 100},
        "dimensions": {},
    }
    candidate = _candidate(
        independent_model_prob="0.80",
        market_consensus_prob="0.52",
        model_prob="0.52",
    )
    result = t30_reprice.reprice_row(
        candidate,
        state=_state(_record(devig_decimal=1.91, book_decimal_odds=1.91, book_odds=-110)),
        original_context=_context(),
        current_context=_context(),
        blend_artifact=artifact,
        now=NOW,
    )
    assert result["model_prob"] == pytest.approx(1.0 / 1.91)
    assert result["t30_status"] == "KILL_PRICE_MOVED"
    assert result["actionable"] == "false"


def test_reprice_emits_reduce_for_smaller_positive_size():
    result = _reprice(candidate=_candidate(recommended_units_pre_news="3"))

    assert result["t30_status"] == "REDUCE"
    assert result["recommended_units_pre_news"] == 1.0
    assert result["actionable"] == "true"


@pytest.mark.parametrize(
    ("state", "original_context", "current_context", "expected"),
    [
        (_state(_record(devig_decimal=2.0)), _context(), _context(), "KILL_PRICE_MOVED"),
        (
            _state(_record(current_line=8.0)),
            _context(),
            _context(),
            "KILL_LINE_MOVED",
        ),
        (_state(_record()), _context(), _context(injury="Late scratch"), "KILL_INJURY"),
        (
            _state(_record()),
            _context(pitcher="Starter A"),
            _context(pitcher="Starter C"),
            "KILL_STARTER_CHANGE",
        ),
        (_state(_record(), status="error"), _context(), _context(), "KILL_STALE_SOURCE"),
        (_state(), _context(), _context(), "MARKET_MISSING"),
    ],
)
def test_reprice_kill_statuses_clear_units(
    state, original_context, current_context, expected
):
    result = _reprice(
        state=state,
        original_context=original_context,
        current_context=current_context,
    )

    assert result["t30_status"] == expected
    assert result["recommended_units_pre_news"] == ""
    assert result["actionable"] == "false"
    assert result["board"] == "A_FLAGGED"


def test_exact_identity_does_not_accept_another_outcome_or_line():
    state = _state(
        _record(outcome_id="other-outcome", book_decimal_odds=9.0),
        _record(current_line=8.0, book_decimal_odds=9.0),
        _record(book_decimal_odds=1.91),
    )

    result = _reprice(state=state)

    assert result["t30_status"] == "KEEP"
    assert result["decimal_price"] == 1.91


def test_original_book_disappearance_is_price_kill():
    result = _reprice(state=_state(_record(book="Other Book")))

    assert result["t30_status"] == "KILL_PRICE_MOVED"
    assert result["recommended_units_pre_news"] == ""


def test_only_failed_market_is_stale_on_partial_refresh():
    stale = _state(_record())
    stale.status.update(
        {
            "status": "partial",
            "fetch_errors": [{"market_id": "market-1", "error": "403"}],
        }
    )
    healthy = _state(_record())
    healthy.status.update(
        {
            "status": "partial",
            "fetch_errors": [{"market_id": "different-market", "error": "403"}],
        }
    )

    assert _reprice(state=stale)["t30_status"] == "KILL_STALE_SOURCE"
    assert _reprice(state=healthy)["t30_status"] == "KEEP"


def test_stale_late_news_context_kills_candidate():
    current = {**_context(), "injury_stale_leagues": ["MLB"]}

    result = _reprice(current_context=current)

    assert result["t30_status"] == "KILL_STALE_SOURCE"
    assert result["recommended_units_pre_news"] == ""


def test_nonzero_refresh_exit_kills_instead_of_reusing_cached_prices():
    result = _reprice(refresh_failed=True)

    assert result["t30_status"] == "KILL_STALE_SOURCE"
    assert result["recommended_units_pre_news"] == ""


def test_partial_matchup_failure_kills_affected_event():
    current = {**_context(), "lineup_stale_events": ["WNBA:evt-1"]}

    result = _reprice(candidate=_candidate(sport="WNBA"), current_context=current)

    assert result["t30_status"] == "KILL_STALE_SOURCE"


def test_late_lineup_change_emits_starter_change():
    candidate = _candidate(sport="WNBA")
    original = {
        **_context(),
        "lineups_by_league": {"WNBA": {"evt-1": {"home": {"players": ["A"]}}}},
    }
    current = {
        **_context(),
        "lineups_by_league": {"WNBA": {"evt-1": {"home": {"players": ["B"]}}}},
    }

    result = _reprice(
        candidate=candidate,
        original_context=original,
        current_context=current,
    )

    assert result["t30_status"] == "KILL_STARTER_CHANGE"


def test_timing_gate_uses_first_slate_lock_and_is_idempotent(tmp_path):
    pack_dir = tmp_path / "2026-08-12"
    pack_dir.mkdir()
    rows = [_candidate()]
    first_lock = NOW + timedelta(hours=1)
    context = {"event_starts": {"slate-first": first_lock.isoformat()}}

    should_run, reason, actual_lock, due = t30_reprice.should_run_t30(
        pack_dir,
        rows,
        context,
        now=NOW,
    )
    assert not should_run
    assert reason == "not_due"
    assert actual_lock == first_lock
    assert due == first_lock - timedelta(minutes=30)

    should_run, reason, _, _ = t30_reprice.should_run_t30(
        pack_dir,
        rows,
        context,
        now=due,
    )
    assert should_run and reason == "due"

    (pack_dir / "manifest.json").write_text(
        json.dumps({"t30_reprice": {"status": "completed"}}), encoding="utf-8"
    )
    should_run, reason, _, _ = t30_reprice.should_run_t30(
        pack_dir,
        rows,
        context,
        now=due,
    )
    assert not should_run and reason == "already_completed"


def test_timing_gate_fails_closed_after_first_lock(tmp_path):
    with pytest.raises(t30_reprice.T30Error, match="window has closed"):
        t30_reprice.should_run_t30(
            tmp_path,
            [_candidate()],
            {"event_starts": {"evt": NOW.isoformat()}},
            now=NOW,
        )


def test_first_lock_ignores_off_date_event_starts(tmp_path):
    pack_dir = tmp_path / "2026-08-16"
    pack_dir.mkdir()
    first_lock = datetime(2026, 8, 16, 16, 15, tzinfo=timezone.utc)
    rows = [_candidate(_event_starts_at=first_lock.isoformat())]
    context = {
        "event_starts": {
            "leftover-2025": "2025-08-26T22:35:00+00:00",
            "slate-first": first_lock.isoformat(),
            "later-same-day": "2026-08-16T21:00:00+00:00",
        }
    }

    should_run, reason, actual_lock, due = t30_reprice.should_run_t30(
        pack_dir,
        rows,
        context,
        now=first_lock - timedelta(hours=2),
    )

    assert not should_run
    assert reason == "not_due"
    assert actual_lock == first_lock
    assert due == first_lock - timedelta(minutes=30)


def test_first_lock_fails_closed_when_only_off_date_starts_exist(tmp_path):
    pack_dir = tmp_path / "2026-08-16"
    pack_dir.mkdir()
    with pytest.raises(t30_reprice.T30Error, match="no event starts on slate date"):
        t30_reprice.should_run_t30(
            pack_dir,
            [_candidate(_event_starts_at="2025-08-26T22:35:00+00:00")],
            {"event_starts": {"leftover-2025": "2025-08-26T22:35:00+00:00"}},
            now=NOW,
        )


def test_freeze_originals_is_immutable_and_rejects_partial_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda _league: {"AAA": {"pitcher": "Starter A", "confirmed": True}},
    )
    rows = [_candidate(), _candidate(market_id="not-actionable", actionable="false")]
    pack._freeze_t30_originals(
        tmp_path,
        rows,
        games_norm_by_league={"MLB": {}},
        props_norm_by_league={"MLB": {}},
    )
    original_bytes = (tmp_path / "original_recommendations.csv").read_bytes()
    context_bytes = (tmp_path / "original_t30_context.json").read_bytes()

    pack._freeze_t30_originals(
        tmp_path,
        [_candidate(price="+500")],
        games_norm_by_league={"MLB": {}},
        props_norm_by_league={"MLB": {}},
    )

    assert (tmp_path / "original_recommendations.csv").read_bytes() == original_bytes
    assert (tmp_path / "original_t30_context.json").read_bytes() == context_bytes
    assert len(list(csv.DictReader(original_bytes.decode().splitlines()))) == 1

    (tmp_path / "original_t30_context.json").unlink()
    with pytest.raises(ValidationError, match="Incomplete T-30 original snapshot"):
        pack._freeze_t30_originals(
            tmp_path,
            rows,
            games_norm_by_league={"MLB": {}},
            props_norm_by_league={"MLB": {}},
        )


def test_freeze_originals_drops_off_date_event_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda _league: {},
    )
    out_dir = tmp_path / "2026-08-16"
    out_dir.mkdir()
    games = {
        "context": {
            "events": {
                "stale": {"starts_at": "2025-08-26T22:35:00+00:00"},
                "evt-1": {"starts_at": "2026-08-16T16:15:00+00:00"},
            }
        }
    }

    pack._freeze_t30_originals(
        out_dir,
        [_candidate(_event_starts_at="2026-08-16T16:15:00+00:00")],
        games_norm_by_league={"MLB": games},
        props_norm_by_league={"MLB": {}},
        target_date="2026-08-16",
    )

    context = json.loads((out_dir / "original_t30_context.json").read_text(encoding="utf-8"))
    assert context["event_starts"] == {"evt-1": "2026-08-16T16:15:00+00:00"}


def test_freeze_originals_includes_authoritative_actionable_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "outlier_scrapers.probable_pitchers.load_probable_pitcher_lookup",
        lambda _league: {},
    )
    raw_candidate = _candidate(
        market_id="total-market",
        outcome_id="total-outcome",
        market_type="GAMELINE",
        selection="AAA @ BBB Total OVER 8.5",
        line="8.5",
    )
    total = {
        "sport": "MLB",
        "event_id": "evt-1",
        "market_id": "total-market",
        "outcome_id": "totals:internal-key",
        "total_kind": "game",
        "selection": "AAA @ BBB Total OVER 8.5",
        "line": "8.5",
        "price": "-105",
        "decimal_price": "1.95",
        "book": "Book B",
        "final_blended_prob": "0.57",
        "push_prob": "0",
        "edge_pct": "0.06",
        "recommended_units_pre_news": "1.5",
        "actionable": "true",
    }
    games = {
        "context": {
            "events": {"evt-1": {"starts_at": (NOW + timedelta(minutes=30)).isoformat()}}
        }
    }

    pack._freeze_t30_originals(
        tmp_path,
        [raw_candidate],
        games_norm_by_league={"MLB": games},
        props_norm_by_league={"MLB": {}},
        totals_rows=[total],
    )

    frozen = list(
        csv.DictReader((tmp_path / "original_recommendations.csv").read_text().splitlines())
    )
    assert len(frozen) == 1
    assert frozen[0]["book"] == "Book B"
    assert frozen[0]["market_type"] == "GAMELINE"
    assert frozen[0]["outcome_id"] == "total-outcome"
    assert frozen[0]["_event_starts_at"] == (NOW + timedelta(minutes=30)).isoformat()


def _write_pack_fixture(pack_dir: Path) -> None:
    pack_dir.mkdir()
    row = _candidate()
    _write_rows(pack_dir / "candidates.csv", pack.CANDIDATES_HEADER, [row])
    _write_rows(pack_dir / "original_recommendations.csv", pack.CANDIDATES_HEADER, [row])
    (pack_dir / "original_t30_context.json").write_text(
        json.dumps(
            {
                **_context(),
                "event_starts": {"evt-1": (NOW + timedelta(minutes=30)).isoformat()},
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "manifest.json").write_text(json.dumps({"run_id": "morning"}), encoding="utf-8")


def _write_rows(path: Path, fields, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_run_publishes_pack_and_ledger_atomically_and_then_skips(tmp_path, monkeypatch):
    pack_dir = tmp_path / "2026-08-12"
    _write_pack_fixture(pack_dir)
    original_bytes = (pack_dir / "original_recommendations.csv").read_bytes()
    state = _state(_record())
    monkeypatch.setattr(t30_reprice, "_load_stream_state", lambda _league, _stream: state)
    monkeypatch.setattr(
        t30_reprice, "_build_current_context", lambda _leagues, *, now: _context()
    )
    monkeypatch.setattr(
        t30_reprice.probability_blend, "load_weight_artifact", lambda _path: None
    )
    refresh_calls = []

    result = t30_reprice.run_t30_reprice(
        pack_dir,
        feedback_db=tmp_path / "feedback.sqlite3",
        now=NOW,
        refresh_runner=lambda argv: refresh_calls.append(argv) or 0,
    )

    assert result.completed
    assert result.status_counts == {"KEEP": 1}
    assert len(refresh_calls) == 1
    assert "--date" in refresh_calls[0]
    assert "2026-08-12" in refresh_calls[0]
    assert (pack_dir / "t30_reprice.csv").exists()
    assert (pack_dir / "t30_decisions.csv").exists()
    assert (pack_dir / "original_recommendations.csv").read_bytes() == original_bytes
    with sqlite3.connect(tmp_path / "feedback.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT news_override FROM decisions").fetchone()[0] == "KEEP"

    skipped = t30_reprice.run_t30_reprice(
        pack_dir,
        feedback_db=tmp_path / "feedback.sqlite3",
        now=NOW,
        refresh_runner=lambda _argv: pytest.fail("completed pass refreshed again"),
    )
    assert not skipped.completed
    assert skipped.reason == "already_completed"


def test_ledger_failure_leaves_published_pack_unchanged(tmp_path, monkeypatch):
    pack_dir = tmp_path / "2026-08-12"
    _write_pack_fixture(pack_dir)
    candidate_bytes = (pack_dir / "candidates.csv").read_bytes()
    state = _state(_record())
    monkeypatch.setattr(t30_reprice, "_load_stream_state", lambda _league, _stream: state)
    monkeypatch.setattr(
        t30_reprice, "_build_current_context", lambda _leagues, *, now: _context()
    )
    monkeypatch.setattr(
        t30_reprice.probability_blend, "load_weight_artifact", lambda _path: None
    )
    monkeypatch.setattr(
        t30_reprice.feedback,
        "capture_t30_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ledger failed")),
    )

    with pytest.raises(RuntimeError, match="ledger failed"):
        t30_reprice.run_t30_reprice(
            pack_dir,
            feedback_db=tmp_path / "feedback.sqlite3",
            now=NOW,
            refresh_runner=lambda _argv: 0,
        )

    assert (pack_dir / "candidates.csv").read_bytes() == candidate_bytes
    assert not (pack_dir / "t30_reprice.csv").exists()
    assert not list(tmp_path.glob(".2026-08-12.t30-staging-*"))


def test_swap_failure_rolls_back_t30_ledger_and_preserves_pack(tmp_path, monkeypatch):
    pack_dir = tmp_path / "2026-08-12"
    _write_pack_fixture(pack_dir)
    candidate_bytes = (pack_dir / "candidates.csv").read_bytes()
    state = _state(_record())
    monkeypatch.setattr(t30_reprice, "_load_stream_state", lambda _league, _stream: state)
    monkeypatch.setattr(
        t30_reprice, "_build_current_context", lambda _leagues, *, now: _context()
    )
    monkeypatch.setattr(
        t30_reprice.probability_blend, "load_weight_artifact", lambda _path: None
    )
    monkeypatch.setattr(
        t30_reprice.pack,
        "_swap_staged_pack",
        lambda *_args: (_ for _ in ()).throw(OSError("swap failed")),
    )

    with pytest.raises(OSError, match="swap failed"):
        t30_reprice.run_t30_reprice(
            pack_dir,
            feedback_db=tmp_path / "feedback.sqlite3",
            now=NOW,
            refresh_runner=lambda _argv: 0,
        )

    assert (pack_dir / "candidates.csv").read_bytes() == candidate_bytes
    assert not (pack_dir / "t30_reprice.csv").exists()
    with sqlite3.connect(tmp_path / "feedback.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_current_context_marks_old_news_and_starter_sources_stale(tmp_path, monkeypatch):
    normalized = tmp_path / "normalized"
    reports = tmp_path / "reports"
    normalized.mkdir()
    reports.mkdir()
    probable_path = normalized / "mlb_probable_pitchers_latest.json"
    (normalized / "mlb_games_latest.json").write_text(
        json.dumps(
            {
                "context": {
                    "events": {
                        "evt-1": {
                            "lineups": {"home": {"players": ["A"]}},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (reports / "games_status_latest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "generated_at": (NOW - timedelta(hours=1)).isoformat(),
                "fetch_errors": [{"step": "matchup", "event_id": "evt-1"}],
            }
        ),
        encoding="utf-8",
    )
    probable_path.write_text(
        json.dumps(
            {
                "generated_at": (NOW - timedelta(hours=1)).isoformat(),
                "by_team": {"AAA": {"pitcher": "Starter A", "confirmed": True}},
            }
        ),
        encoding="utf-8",
    )
    fake_paths = SimpleNamespace(
        normalized=normalized,
        reports=reports,
        probable_pitchers_latest=lambda: probable_path,
    )
    monkeypatch.setattr(t30_reprice.paths, "league_paths", lambda _league: fake_paths)

    context = t30_reprice._build_current_context(["MLB"], now=NOW)

    assert context["injury_stale_leagues"] == ["MLB"]
    assert context["starter_stale_leagues"] == ["MLB"]
    assert context["lineup_stale_events"] == ["MLB:evt-1"]
    assert context["injury_known_events_by_league"] == {"MLB": ["evt-1"]}
    assert context["starter_known_teams_by_league"] == {"MLB": ["AAA"]}


def test_capture_t30_writes_status_to_t30_decisions_only(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    row = {
        **_candidate(),
        "t30_status": "KILL_PRICE_MOVED",
        "recommended_units_pre_news": "",
        "actionable": "false",
    }
    _write_rows(pack_dir / "t30_reprice.csv", t30_reprice.T30_FIELDS, [row])

    first = feedback.capture_t30_pack(pack_dir, tmp_path / "feedback.sqlite3")
    second = feedback.capture_t30_pack(pack_dir, tmp_path / "feedback.sqlite3")

    assert first.snapshots == second.snapshots == 1
    assert (pack_dir / "t30_decisions.csv").exists()
    assert not (pack_dir / "decisions.csv").exists()
    with sqlite3.connect(tmp_path / "feedback.sqlite3") as conn:
        decision = conn.execute(
            "SELECT pipeline_verdict, units, kill_reason, news_override FROM decisions"
        ).fetchone()
    assert decision == ("STAND_DOWN", 0.0, "KILL_PRICE_MOVED", "KILL_PRICE_MOVED")


def test_t30_updates_specialized_totals_without_changing_internal_totals_id(tmp_path):
    total = {field: "" for field in GAME_TOTALS_HEADER}
    total.update(
        {
            "totals_id": "totals:internal-key",
            "sport": "MLB",
            "event_id": "evt-1",
            "market_id": "market-1",
            "outcome_id": "totals:internal-key",
            "selection": "AAA @ BBB Total O/U OVER 7.5",
            "line": "7.5",
            "price": "-110",
            "book": "Book A",
            "actionable": "true",
            "recommended_units_pre_news": "1",
        }
    )
    _write_rows(tmp_path / "game_totals.csv", GAME_TOTALS_HEADER, [total])
    repriced = t30_reprice._kill(
        _candidate(), "KILL_PRICE_MOVED", NOW.isoformat(), "edge gone"
    )

    t30_reprice._update_specialized_totals(tmp_path, [repriced])

    current = list(
        csv.DictReader((tmp_path / "game_totals.csv").read_text().splitlines())
    )[0]
    assert current["outcome_id"] == "totals:internal-key"
    assert current["actionable"] == "false"
    assert current["recommended_units_pre_news"] == ""
    assert "t30:KILL_PRICE_MOVED" in current["quality_flags"]
