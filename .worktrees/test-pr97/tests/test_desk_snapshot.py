"""Desk snapshot, readiness, lock recovery, and retention (step 12)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from outlier_scrapers import desk_snapshot as ds
from outlier_scrapers import pack, pack_index
from outlier_scrapers import runner_common as rc
from outlier_scrapers import verdict_report


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir: Path, *, totals_extra: str = "") -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-08-14\n", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "_event_starts_at": _future(),
                "market_id": "m1",
                "outcome_id": "out1",
                "market_type": "PLAYER_PROP",
                "player_id": "p1",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "board": "A",
                "actionable": "true",
                "market_label": "SO",
                "max_units": "2.0",
                "recommended_units_pre_news": "1.5",
                "edge_pct": "4.2",
                "model_prob_source": "blend",
            }
        )
        writer.writerow(row)
    if totals_extra:
        (pack_dir / "game_totals.csv").write_text(totals_extra, encoding="utf-8")


def _index(pack_dir: Path, tmp_path: Path):
    return pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")


def _verdict_envelope(index, pass_name: str, units: float = 1.0):
    return {
        "schema_version": "1.0",
        "pass": pass_name,
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [
            {
                "market_id": "m1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "confidence": 0.7,
                "recommended_units": units,
                "evidence": [],
                "contradictions": [],
                "kill_triggers": [],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }


def _publish_verdict(pack_dir, index, pass_name, units=1.0):
    return rc.publish_verdict_pass(
        pack_dir,
        json.dumps(_verdict_envelope(index, pass_name, units)),
        pass_=pass_name,
        request_sha256=pass_name * 16 + str(units),
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        model="fake",
    )


def _publish_adb(pack_dir, index):
    pubs = {}
    for name, units in (("A", 1.5), ("D", 0.5), ("B", 1.0)):
        result = _publish_verdict(pack_dir, index, name, units)
        data = json.loads((result.path / "verdicts.json").read_text(encoding="utf-8"))
        pubs[name] = {
            "publication_id": result.publication_id,
            "record_id": data["verdicts"][0]["record_id"],
        }
    return pubs


def _publish_e(pack_dir, index, pubs):
    env = {
        "schema_version": "1.0",
        "pass": "E",
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {
            name: info["publication_id"] for name, info in pubs.items()
        },
        "reconciliations": [
            {
                "market_id": "m1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "recommended_units": 0.5,
                "narrative": "A, D, and B agree.",
                "cites": [
                    {
                        "pass": name,
                        "publication_id": info["publication_id"],
                        "record_id": info["record_id"],
                    }
                    for name, info in pubs.items()
                ],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    return rc.publish_reconciliation_pass(
        pack_dir,
        json.dumps(env),
        request_sha256="E" * 32,
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        model="fake",
    )


def test_snapshot_from_e_pins_e_own_id_and_upstream(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    pubs = _publish_adb(pack_dir, index)
    e_result = _publish_e(pack_dir, index, pubs)
    payload = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert payload is not None
    assert payload["synthesis_source"] == "claude_e"
    assert payload["publications"]["E"] == e_result.publication_id
    assert payload["publications"]["A"] == pubs["A"]["publication_id"]
    assert payload["publications"]["D"] == pubs["D"]["publication_id"]
    assert payload["publications"]["B"] == pubs["B"]["publication_id"]
    e_data = json.loads((e_result.path / "verdicts.json").read_text(encoding="utf-8"))
    assert "E" not in e_data["upstream_publication_ids"]

    old_a = payload["publications"]["A"]
    _publish_verdict(pack_dir, index, "A", units=1.0)
    reread = ds.read_desk_snapshot(pack_dir)
    assert reread is not None
    assert reread["publications"]["A"] == old_a
    current_a = json.loads(
        (pack_dir / "verdicts" / "A" / "current.json").read_text(encoding="utf-8")
    )
    assert current_a["publication_id"] != old_a


def test_fallback_snapshot_when_e_missing(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    pubs = _publish_adb(pack_dir, index)
    payload = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert payload is not None
    assert payload["synthesis_source"] == "fallback_no_e"
    assert payload["publications"]["E"] is None
    assert payload["publications"]["A"] == pubs["A"]["publication_id"]
    assert payload["publications"]["D"] == pubs["D"]["publication_id"]
    assert payload["publications"]["B"] == pubs["B"]["publication_id"]
    fallback = verdict_report.compute_no_e_fallback(
        pack_dir, policy_path=tmp_path / "no-policy.json"
    )
    assert payload["publications"]["A"] == fallback.publications["A"]


def test_flat_files_alone_are_not_ready(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    _publish_verdict(pack_dir, index, "A", units=1.5)
    (pack_dir / "gemini_b.md").write_text("old B\n", encoding="utf-8")
    (pack_dir / "claude_d.md").write_text("old D\n", encoding="utf-8")
    ready = ds.desk_publication_ready(pack_dir, policy_path=tmp_path / "no-policy.json")
    assert ready.ready is False
    assert ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    ) is None


def test_stale_fingerprint_blocks_then_republish_advances(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    _publish_adb(pack_dir, index)
    first = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert first is not None
    raw = (pack_dir / "candidates.csv").read_text(encoding="utf-8")
    (pack_dir / "candidates.csv").write_text(raw.replace("4.2", "4.9"), encoding="utf-8")
    index2 = _index(pack_dir, tmp_path)
    _publish_verdict(pack_dir, index2, "A", units=1.5)
    ready = ds.desk_publication_ready(pack_dir, policy_path=tmp_path / "no-policy.json")
    assert ready.ready is False
    assert "B" in ready.reason or "D" in ready.reason
    unchanged = ds.read_desk_snapshot(pack_dir)
    assert unchanged is not None
    assert unchanged["publications"]["A"] == first["publications"]["A"]

    _publish_verdict(pack_dir, index2, "D", units=0.5)
    _publish_verdict(pack_dir, index2, "B", units=1.0)
    second = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert second is not None
    assert second["publications"]["A"] != first["publications"]["A"]
    assert second["synthesis_source"] == "fallback_no_e"


def test_e_rejected_when_a_rerun_without_pack_change(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    pubs = _publish_adb(pack_dir, index)
    _publish_e(pack_dir, index, pubs)
    first = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert first is not None
    assert first["synthesis_source"] == "claude_e"
    _publish_verdict(pack_dir, index, "A", units=1.0)
    second = ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    assert second is not None
    assert second["synthesis_source"] == "fallback_no_e"
    assert second["publications"]["E"] is None
    assert second["publications"]["A"] != first["publications"]["A"]
    assert second["publications"]["B"] == first["publications"]["B"]


def test_commit_then_history_crash_ordering(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    _publish_adb(pack_dir, index)
    ready = ds.desk_publication_ready(pack_dir, policy_path=tmp_path / "no-policy.json")
    payload = ds.build_snapshot_payload(
        pack_dir, ready, policy_path=tmp_path / "no-policy.json"
    )
    ds.publish_desk_snapshot(pack_dir, payload, append_history=False)
    snap = ds.read_desk_snapshot(pack_dir)
    assert snap is not None
    assert snap["synthesis_source"] == payload["synthesis_source"]
    history = pack_dir / "verdicts" / ds.HISTORY_NAME
    assert not history.exists()

    ds.publish_desk_snapshot(pack_dir, payload, append_history=True)
    lines = history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["publications"] == payload["publications"]


def test_writer_lock_stale_same_host_dead_pid_is_breakable(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    lock_dir = ds.writer_lock_dir(pack_dir)
    lock_dir.mkdir(parents=True)
    stale_time = datetime.now().astimezone() - timedelta(hours=1)
    (lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "pid": 424242,
                "hostname": "testhost",
                "acquired_at": stale_time.isoformat(),
                "operation": "desk_publish",
            }
        ),
        encoding="utf-8",
    )
    breakable, reason = ds.lock_breakable(
        lock_dir,
        hostname="testhost",
        pid_is_running=lambda _pid: False,
        stale_after=timedelta(minutes=30),
    )
    assert breakable is True
    assert "dead" in reason or "expired" in reason

    acquired = ds.acquire_writer_lock(
        pack_dir,
        operation="desk_publish",
        hostname="testhost",
        pid_is_running=lambda _pid: False,
        stale_after=timedelta(minutes=30),
        retries=2,
    )
    try:
        assert acquired.exists()
    finally:
        ds.release_writer_lock(acquired)


def test_writer_lock_other_host_never_auto_breaks(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    lock_dir = ds.writer_lock_dir(pack_dir)
    lock_dir.mkdir(parents=True)
    stale_time = datetime.now().astimezone() - timedelta(days=2)
    (lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "pid": 7,
                "hostname": "other-machine",
                "acquired_at": stale_time.isoformat(),
                "operation": "desk_publish",
            }
        ),
        encoding="utf-8",
    )
    breakable, reason = ds.lock_breakable(
        lock_dir,
        hostname="testhost",
        pid_is_running=lambda _pid: False,
        stale_after=timedelta(minutes=1),
    )
    assert breakable is False
    assert "other host" in reason
    with pytest.raises(ds.LockNotBreakable):
        ds.acquire_writer_lock(
            pack_dir,
            operation="desk_publish",
            hostname="testhost",
            pid_is_running=lambda _pid: False,
            stale_after=timedelta(minutes=1),
            retries=1,
        )


def test_writer_lock_live_pid_not_broken(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    lock_dir = ds.writer_lock_dir(pack_dir)
    lock_dir.mkdir(parents=True)
    stale_time = datetime.now().astimezone() - timedelta(hours=2)
    (lock_dir / "owner.json").write_text(
        json.dumps(
            {
                "pid": 99,
                "hostname": "testhost",
                "acquired_at": stale_time.isoformat(),
                "operation": "publish_A",
            }
        ),
        encoding="utf-8",
    )
    breakable, reason = ds.lock_breakable(
        lock_dir,
        hostname="testhost",
        pid_is_running=lambda _pid: True,
        stale_after=timedelta(minutes=1),
    )
    assert breakable is False
    assert "still running" in reason


def test_no_owner_json_breakable_after_stale(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    lock_dir = ds.writer_lock_dir(pack_dir)
    lock_dir.mkdir(parents=True)
    old = datetime.now().astimezone() - timedelta(hours=2)
    __import__("os").utime(lock_dir, (old.timestamp(), old.timestamp()))
    breakable, reason = ds.lock_breakable(
        lock_dir, stale_after=timedelta(minutes=30)
    )
    assert breakable is True
    assert reason == "no_owner_expired"


def test_retention_keeps_snapshot_and_current_deletes_old_unreachable(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    pubs = _publish_adb(pack_dir, index)
    ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    old_a = pubs["A"]["publication_id"]
    newer = pack_dir / "verdicts" / "A" / "newerpub"
    newer.mkdir()
    (newer / "verdicts.json").write_text("{}", encoding="utf-8")
    pointer = pack_dir / "verdicts" / "A" / "current.json"
    current = json.loads(pointer.read_text(encoding="utf-8"))
    current["publication_id"] = "newerpub"
    pointer.write_text(json.dumps(current), encoding="utf-8")
    orphan = pack_dir / "verdicts" / "A" / "orphanpub"
    orphan.mkdir()
    (orphan / "verdicts.json").write_text("{}", encoding="utf-8")
    old = datetime.now().astimezone() - timedelta(days=30)
    __import__("os").utime(orphan, (old.timestamp(), old.timestamp()))

    deleted = ds.prune_publications(
        pack_dir, max_age=timedelta(days=7), hold_lock=False
    )
    assert "orphanpub" in deleted
    assert (pack_dir / "verdicts" / "A" / old_a).is_dir()
    assert newer.is_dir()


def test_legacy_projection_and_latest_preview(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = _index(pack_dir, tmp_path)
    pubs = _publish_adb(pack_dir, index)
    ds.advance_desk_snapshot(
        pack_dir, policy_path=tmp_path / "no-policy.json", hold_locks=False
    )
    ds.project_legacy_from_snapshot(pack_dir)
    assert (pack_dir / "chatgpt_a.md").is_file()
    status_name = "reason" + "ing_status.json"
    status = json.loads((pack_dir / status_name).read_text(encoding="utf-8"))
    assert status["final_report"]["source"] == "fallback_no_e"
    ds.write_latest_preview(pack_dir, "A")
    preview = (pack_dir / "verdicts" / "A" / "latest_preview.md").read_text(encoding="utf-8")
    assert preview.startswith(ds.PREVIEW_BANNER)
    meta = json.loads(
        (pack_dir / "verdicts" / "A" / "latest_preview.json").read_text(encoding="utf-8")
    )
    assert meta["authoritative"] is False
    assert meta["publication_id"] == pubs["A"]["publication_id"]
