"""F27 persistence: unique temps, bounded writer locks, run-ID idempotence (#230)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

import pytest

from outlier_nfl import scorecard, tape_nflverse
from outlier_nfl.external import common as ext_common


def _occupy(path: Path) -> None:
    # A crashed or overlapping job holding the old fixed temp name.
    path.mkdir(parents=True)


def test_ledger_survives_a_taken_fixed_temp_name(tmp_path):
    path = tmp_path / "ledger.jsonl"
    _occupy(path.with_suffix(path.suffix + ".tmp"))
    scorecard.update_ledger(path, [], "2026-09-13", event_ids=[])
    assert path.exists()


def test_tape_survives_a_taken_fixed_temp_name(tmp_path):
    path = tmp_path / "tape" / "prior_week.json"
    path.parent.mkdir()
    path.write_text('{"season": 2025}', encoding="utf-8")
    _occupy(path.with_name(path.name + ".tmp"))
    tape_nflverse.write_tape(path, {"season": 2026})
    assert json.loads(path.read_text("utf-8")) == {"season": 2026}
    assert json.loads(path.with_name("prior_week.prev.json").read_text("utf-8")) == {"season": 2025}


def test_external_cache_survives_a_taken_fixed_temp_name(tmp_path, monkeypatch):
    client = ext_common.Client(cache_dir=tmp_path, ttl=0)
    url = "https://example.invalid/x.csv"
    _occupy(client._cache_path(url).with_suffix(".tmp"))
    monkeypatch.setattr(ext_common, "fetch_bytes", lambda u, t=120.0: b"a,b\n1,2\n")
    assert client.fetch_csv(url) == [{"a": "1", "b": "2"}]
    assert client._cache_path(url).read_bytes() == b"a,b\n1,2\n"


def test_no_temp_files_left_behind(tmp_path):
    path = tmp_path / "ledger.jsonl"
    scorecard.update_ledger(path, [], "2026-09-13", event_ids=[])
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.jsonl"]


# --- bounded writer locks ---------------------------------------------------


def test_held_lock_times_out_instead_of_hanging(tmp_path):
    from outlier_nfl.utils import LockTimeout, file_lock

    target = tmp_path / "ledger.jsonl"
    (tmp_path / "ledger.jsonl.lock").write_text("other job", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(LockTimeout):
        with file_lock(target, timeout=0.2):
            pass
    assert time.monotonic() - start < 2


def test_stale_lock_is_recovered_and_released(tmp_path):
    from outlier_nfl.utils import file_lock

    target = tmp_path / "ledger.jsonl"
    lock = tmp_path / "ledger.jsonl.lock"
    lock.write_text("crashed job", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    with file_lock(target, timeout=0.2, stale_after=60):
        assert lock.exists()
    assert not lock.exists()


def test_ledger_writer_waits_for_the_lock(tmp_path):
    from outlier_nfl.utils import file_lock

    path = tmp_path / "ledger.jsonl"
    done = threading.Event()
    with file_lock(path):
        t = threading.Thread(target=lambda: (scorecard.update_ledger(
            path, [], "2026-09-13", event_ids=[]), done.set()))
        t.start()
        time.sleep(0.3)
        assert not done.is_set()  # blocked behind the holder
    t.join(5)
    assert done.is_set()


def _graded(date, event):
    return scorecard.GradedSignal(
        date=date, week=1, event_id=event, tag="SCRIPT", player="P", team="KC",
        market="REC", side="OVER", prior_avg=4.0, actual=5.0, hit_vs_avg=True, line=4.5,
        hit_vs_line=True, run_id="RUN-1")


def test_concurrent_ledger_writers_lose_no_dates(tmp_path):
    path = tmp_path / "ledger.jsonl"
    real = scorecard.atomic_write_text

    def slow(p, text):  # widen the read-modify-write window
        time.sleep(0.02)
        real(p, text)

    scorecard.atomic_write_text = slow
    try:
        dates = [f"2026-09-{d:02d}" for d in range(1, 13)]
        threads = [threading.Thread(target=scorecard.update_ledger,
                                    args=(path, [_graded(d, "e1")], d)) for d in dates]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    finally:
        scorecard.atomic_write_text = real
    kept = sorted(json.loads(x)["date"] for x in path.read_text("utf-8").splitlines())
    assert kept == dates


# --- same run ID is one observation -----------------------------------------

def _prop(line=255.5, player="Patrick Mahomes"):
    return {"is_consensus_line": True, "scope": "full_game", "event_id": "e1",
            "player_name": player, "market": "PASS_YDS", "position": "OVER", "line": line,
            "best_odds": -110, "books": [{"book": "fanduel", "odds": -110}]}


def _rows(path):
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def test_same_run_id_never_duplicates_snapshot_rows(tmp_path):
    from outlier_nfl.snapshots import append_snapshot

    t = "2026-09-13T15:00:00+00:00"
    path = append_snapshot(tmp_path, "2026-09-13", [_prop()], t, run_id="RUN-1")
    append_snapshot(tmp_path, "2026-09-13", [_prop(), _prop(player="Josh Allen")], t,
                    run_id="RUN-1")
    assert [(r["run_id"], r["player_name"]) for r in _rows(path)] == [
        ("RUN-1", "Patrick Mahomes"), ("RUN-1", "Josh Allen")]


def test_changed_run_id_adds_one_observation(tmp_path):
    from outlier_nfl.snapshots import append_snapshot

    path = append_snapshot(tmp_path, "2026-09-13", [_prop()], "2026-09-13T15:00:00+00:00",
                           run_id="RUN-1")
    append_snapshot(tmp_path, "2026-09-13", [_prop(256.5)], "2026-09-13T16:00:00+00:00",
                    run_id="RUN-2")
    assert [(r["run_id"], r["line"]) for r in _rows(path)] == [("RUN-1", 255.5),
                                                               ("RUN-2", 256.5)]


def test_rows_without_run_id_keep_appending(tmp_path):
    from outlier_nfl.snapshots import append_snapshot

    for _ in range(2):
        path = append_snapshot(tmp_path, "2026-09-13", [_prop()], "2026-09-13T15:00:00+00:00")
    assert len(_rows(path)) == 2


def test_same_run_id_ledger_rows_are_replaced_not_duplicated(tmp_path):
    path = tmp_path / "ledger.jsonl"
    for _ in range(2):
        rows = scorecard.update_ledger(path, [_graded("2026-09-13", "e1")], "2026-09-13")
    assert len(rows) == 1 and len(path.read_text("utf-8").splitlines()) == 1


# --- replay --refresh-tape never rewrites the shared tape ---------------------

def _fake_refresh(calls):
    def fake(nfl_dir, season, **kw):
        target = Path(kw.get("path") or Path(nfl_dir) / "tape" / "prior_week.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"who": kw.get("run_mode")}), encoding="utf-8")
        calls.append(target)
        return target
    return fake


@pytest.mark.parametrize("mode", ["replay", "retrospective", "fixture", None])
def test_non_live_refresh_builds_its_own_tape(tmp_path, monkeypatch, mode):
    from outlier_nfl import pipeline

    shared = tmp_path / "tape" / "prior_week.json"
    shared.parent.mkdir()
    shared.write_text('{"who": "live"}', encoding="utf-8")
    calls: list[Path] = []
    monkeypatch.setattr(pipeline, "refresh_prior_week_tape", _fake_refresh(calls))
    root = pipeline._refresh_tape(tmp_path, "2026-10-04", None, "2026-10-04T16:00:00+00:00", mode)
    assert json.loads(shared.read_text("utf-8")) == {"who": "live"}
    assert root == tmp_path / "tape" / "replay" / "2026-10-04" / "20261004T160000Z"
    assert calls == [root / "tape" / "prior_week.json"]


def test_live_refresh_still_writes_the_shared_tape(tmp_path, monkeypatch):
    from outlier_nfl import pipeline

    calls: list[Path] = []
    monkeypatch.setattr(pipeline, "refresh_prior_week_tape", _fake_refresh(calls))
    root = pipeline._refresh_tape(tmp_path, "2026-10-04", None, "2026-10-04T16:00:00+00:00",
                                  "live")
    assert root == tmp_path and calls == [tmp_path / "tape" / "prior_week.json"]


def test_failed_refresh_falls_back_to_the_existing_tape_dir(tmp_path, monkeypatch):
    from outlier_nfl import pipeline

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(pipeline, "refresh_prior_week_tape", boom)
    assert pipeline._refresh_tape(tmp_path, "2026-10-04", None, None, "replay") == tmp_path


def test_refresh_prior_week_tape_honours_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tape_nflverse, "build_tape_payload",
                        lambda *a, **k: {"teams": {"KC": {}}, "week": [3]})
    target = tmp_path / "scoped" / "tape" / "prior_week.json"
    got = tape_nflverse.refresh_prior_week_tape(tmp_path, 2026, path=target)
    assert got == target and target.exists()
    assert not (tmp_path / "tape" / "prior_week.json").exists()


# --- review round 2: deadline always wins; one stale-lock thief ---------------

class _Clock:
    def __init__(self, step=0.05):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _stale_lock(tmp_path):
    target = tmp_path / "nfl_prop_snapshots_2026-09-07.jsonl"
    lock = tmp_path / (target.name + ".lock")
    lock.write_text("crashed", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    return target, lock


def test_stale_lock_that_cannot_be_moved_times_out(tmp_path, monkeypatch):
    from outlier_nfl import utils

    target, lock = _stale_lock(tmp_path)
    clock = _Clock()
    monkeypatch.setattr(utils, "_clock", clock)
    monkeypatch.setattr(utils, "_sleep", lambda s: None)
    real_replace = os.replace

    def refuse(src, dst):  # Windows: a scanner holds the stale lock open
        if Path(src) == lock:
            raise PermissionError("in use")
        return real_replace(src, dst)

    monkeypatch.setattr(utils.os, "replace", refuse)
    with pytest.raises(utils.LockTimeout) as err:
        with utils.file_lock(target, timeout=1.0, stale_after=60):
            pass
    assert clock.t < 1.2  # gave up at the deadline, no real sleeping
    assert str(lock) in str(err.value) and "delete" in str(err.value)
    assert lock.exists()


def test_failed_delete_of_moved_stale_lock_still_proceeds(tmp_path, monkeypatch):
    from outlier_nfl import utils

    target, lock = _stale_lock(tmp_path)
    monkeypatch.setattr(utils, "_unlink_with_retry", lambda p, **k: False)
    monkeypatch.setattr(utils, "_sleep", lambda s: None)
    with utils.file_lock(target, timeout=1.0, stale_after=60):
        assert lock.read_text("utf-8") != "crashed"


def test_stale_lock_warning_names_the_file(tmp_path, caplog):
    from outlier_nfl import utils

    target, lock = _stale_lock(tmp_path)
    with caplog.at_level("WARNING"), utils.file_lock(target, timeout=1.0, stale_after=60):
        pass
    assert str(lock) in caplog.text and "stale" in caplog.text


def test_lock_refreshed_between_stat_and_rename_is_handed_back(tmp_path, monkeypatch):
    from outlier_nfl import utils

    _, lock = _stale_lock(tmp_path)
    real_replace = os.replace

    def other_waiter_won(src, dst):
        if Path(src) == lock:
            lock.write_text("waiter B", encoding="utf-8")  # B replaced it with a fresh lock
        return real_replace(src, dst)

    monkeypatch.setattr(utils.os, "replace", other_waiter_won)
    assert utils._steal_if_stale(lock, 60) is False
    assert lock.read_text("utf-8") == "waiter B"
    assert sorted(p.name for p in tmp_path.iterdir()) == [lock.name]


def test_only_one_of_two_waiters_steals_a_stale_lock(tmp_path):
    from outlier_nfl import utils

    _, lock = _stale_lock(tmp_path)
    assert utils._steal_if_stale(lock, 60) is True  # waiter A moved it
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # and took the lock
    os.close(fd)
    # Waiter B also saw the old file as stale, but now finds A's fresh lock.
    assert utils._steal_if_stale(lock, 60) is False
    assert lock.exists()


def test_two_threads_on_a_stale_lock_never_overlap(tmp_path):
    from outlier_nfl import utils

    target, _ = _stale_lock(tmp_path)
    inside: list[int] = []
    overlap: list[bool] = []

    def worker():
        with utils.file_lock(target, timeout=5, stale_after=60, poll=0.01):
            inside.append(1)
            overlap.append(len(inside) > 1)
            time.sleep(0.05)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert overlap == [False, False]


def test_snapshot_lock_timeout_withdraws_card_and_names_the_lock(tmp_path, caplog, monkeypatch):
    from outlier_nfl import pipeline, utils

    lock = tmp_path / "x.jsonl.lock"

    def blocked(*a, **k):
        raise utils.LockTimeout(
            f"Lock file {lock} has been held for more than 30s. Another run may be writing; "
            f"if no other run is, delete {lock} and rerun.")

    monkeypatch.setattr(pipeline, "append_snapshot", blocked)
    nfl = pipeline.NflPipeline.__new__(pipeline.NflPipeline)
    nfl.nfl_dir = tmp_path
    nfl.normalized_dir = tmp_path
    with caplog.at_level("ERROR"):
        out = nfl._trace_best_bets(
            target_date="2026-09-13", window=None, now_utc="2026-09-13T15:00:00+00:00",
            as_of_utc=None, before_week=None, props_dict=[], scripts_records=[],
            external_metrics=[], usage_players=[], weather_records=[], tape=None,
            suffix="2026-09-13", publish=False, publish_latest=False, writer=type("W", (), {"run_id": "RUN-1"})())
    assert "error" in out
    assert str(lock) in caplog.text and "delete" in caplog.text


# --- Windows PermissionError on lock creation ---------------------------------

def _deny_open(monkeypatch, utils, times):
    real_open = os.open
    calls = {"n": 0}

    def fake(path, flags, *a):
        if str(path).endswith(".lock") and flags & os.O_EXCL and calls["n"] < times:
            calls["n"] += 1
            raise PermissionError(13, "Access is denied")
        return real_open(path, flags, *a)

    monkeypatch.setattr(utils.os, "open", fake)
    return calls


def test_permission_error_on_lock_create_is_retried(tmp_path, monkeypatch):
    from outlier_nfl import utils

    monkeypatch.setattr(utils, "_sleep", lambda s: None)
    calls = _deny_open(monkeypatch, utils, 3)
    target = tmp_path / "ledger.jsonl"
    with utils.file_lock(target, timeout=5):
        assert (tmp_path / "ledger.jsonl.lock").exists()
    assert calls["n"] == 3


def test_permanent_permission_error_times_out_by_the_deadline(tmp_path, monkeypatch):
    from outlier_nfl import utils

    clock = _Clock()
    monkeypatch.setattr(utils, "_clock", clock)
    monkeypatch.setattr(utils, "_sleep", lambda s: None)
    _deny_open(monkeypatch, utils, 10**9)
    with pytest.raises(utils.LockTimeout):
        with utils.file_lock(tmp_path / "ledger.jsonl", timeout=1.0):
            pass
    assert clock.t < 1.2
