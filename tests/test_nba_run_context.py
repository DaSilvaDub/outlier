from datetime import UTC, datetime, timedelta

import pytest

from outlier_nba.run_context import (
    AsOfReader,
    LeakageError,
    make_run_context,
    parse_utc,
)

TIP = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)
AS_OF = TIP - timedelta(hours=3)


def _events():
    return [{"startTime": TIP.isoformat()}, {"startTime": (TIP + timedelta(hours=3)).isoformat()}]


def test_parse_utc_rejects_naive():
    with pytest.raises(ValueError):
        parse_utc("2026-10-21T12:00:00")


def test_pregame_run_at_now_is_live_and_publishes():
    ctx = make_run_context("2026-10-21", AS_OF, slate_events=_events(), run_started_utc=AS_OF)
    assert ctx.mode == "live" and ctx.publishes and ctx.publication_reason is None


def test_run_after_first_tip_is_retrospective_and_bundle_only():
    ctx = make_run_context(
        "2026-10-21", AS_OF, slate_events=_events(), run_started_utc=TIP + timedelta(minutes=1)
    )
    assert ctx.mode == "retrospective" and not ctx.publishes
    assert "first tip" in (ctx.publication_reason or "")


def test_as_of_after_first_tip_is_retrospective():
    ctx = make_run_context("2026-10-21", TIP, slate_events=_events(), run_started_utc=TIP)
    assert ctx.mode == "retrospective"


def test_stale_as_of_before_tip_is_replay_and_bundle_only():
    ctx = make_run_context(
        "2026-10-21", AS_OF - timedelta(hours=2), slate_events=_events(), run_started_utc=AS_OF
    )
    assert ctx.mode == "replay" and not ctx.publishes and ctx.replay


def test_fixture_mode_publishes_even_after_tip():
    ctx = make_run_context(
        "2026-10-21", TIP + timedelta(hours=9), slate_events=_events(), fixture=True
    )
    assert ctx.mode == "fixture" and ctx.publishes


def test_post_cutoff_row_is_rejected_and_counted():
    ctx = make_run_context("2026-10-21", AS_OF, slate_events=_events(), run_started_utc=AS_OF)
    reader = AsOfReader(ctx)
    rows = [
        {"id": "early", "captured_at": (AS_OF - timedelta(minutes=5)).isoformat()},
        {"id": "late", "captured_at": (AS_OF + timedelta(minutes=5)).isoformat()},
        {"id": "exact", "captured_at": AS_OF.isoformat()},
    ]
    kept = reader.admit(rows, stamp_key="captured_at")
    assert [r["id"] for r in kept] == ["early"]
    assert reader.rejected_post_cutoff == 2


def test_game_scoped_row_stamped_after_its_tip_is_rejected():
    ctx = make_run_context("2026-10-21", TIP + timedelta(hours=1), fixture=True)
    reader = AsOfReader(ctx)
    row = {"captured_at": (TIP + timedelta(minutes=10)).isoformat(), "tip": TIP.isoformat()}
    assert reader.admit([row], stamp_key="captured_at", tip_key="tip") == []


def test_unstamped_row_raises_in_non_live_modes_but_not_live():
    live = make_run_context("2026-10-21", AS_OF, slate_events=_events(), run_started_utc=AS_OF)
    assert AsOfReader(live).admit([{"id": 1}], stamp_key="captured_at") == [{"id": 1}]
    replay = make_run_context(
        "2026-10-21", AS_OF - timedelta(hours=2), slate_events=_events(), run_started_utc=AS_OF
    )
    with pytest.raises(LeakageError):
        AsOfReader(replay).admit([{"id": 1}], stamp_key="captured_at")


def test_unparseable_stamp_is_treated_as_unstamped():
    replay = make_run_context(
        "2026-10-21", AS_OF - timedelta(hours=2), slate_events=_events(), run_started_utc=AS_OF
    )
    with pytest.raises(LeakageError):
        AsOfReader(replay).admit([{"captured_at": "garbage"}], stamp_key="captured_at")


def test_run_id_is_deterministic_and_context_serializes():
    a = make_run_context("2026-10-21", AS_OF, slate_events=_events(), run_started_utc=AS_OF)
    b = make_run_context("2026-10-21", AS_OF, slate_events=_events(), run_started_utc=AS_OF)
    assert a.run_id == b.run_id
    assert a.to_dict()["mode"] == "live"
    assert a.season == "2026-27"
