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
