"""Offline exporter regressions using the upstream generate_prop_edges CSV contract."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nba_export", ROOT / "scripts/export_nba_extra_pack.py")
assert SPEC is not None and SPEC.loader is not None
ex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ex)
DATE = "2026-11-05"


def seed(path: Path, rows=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict.fromkeys(ex.FIELDNAMES, "")
    row.update(slate_date=DATE, player="Fixture Player", market="PTS", line="18.5",
               edge_pct="5.5", game_time="2026-11-06T00:30:00")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=ex.FIELDNAMES)
        writer.writeheader()
        writer.writerows([row] if rows is None else [row | values for values in rows])
    return path


@pytest.fixture(autouse=True)
def no_host_disk_dependency(monkeypatch):
    monkeypatch.setattr(ex, "check_disk_space", lambda *_a, **_k: None)


def test_default_day_is_eastern_not_utc():
    assert ex.slate_day(None, datetime(2026, 11, 6, 2, tzinfo=timezone.utc)) == DATE


def test_actual_upstream_naive_display_time_does_not_override_explicit_slate(tmp_path):
    source = seed(tmp_path / "prop_edges_latest.csv")
    out = tmp_path / "new/result.csv"
    assert ex.export_nba_pack(source, out, DATE) == 1
    row = list(csv.DictReader(out.open()))[0]
    assert row["slate_date"] == DATE and row["game_time"] == "2026-11-06T00:30:00"
    assert row["rank"] == "1"


@pytest.mark.parametrize("date,tip", [
    ("2026-06-08", "2026-06-09T00:30:00Z"),
    ("2026-11-01", "2026-11-02T00:30:00+00:00"),
])
def test_offset_timestamp_rollover_uses_eastern_calendar(tmp_path, date, tip):
    source = seed(tmp_path / "props.csv", [{"slate_date": date, "game_time": tip}])
    assert len(ex.read_source(source, date)) == 1


def test_mixed_source_exports_only_exact_requested_date(tmp_path):
    source = seed(tmp_path / "props.csv", [{}, {"slate_date": "2026-11-04", "player": "Other"}])
    out = tmp_path / "result.csv"
    assert ex.export_nba_pack(source, out, DATE) == 1
    assert "Other" not in out.read_text()


def test_wrong_day_never_falls_back_or_truncates_prior_output(tmp_path):
    source = seed(tmp_path / "props.csv", [{"slate_date": "2026-11-04"}])
    out = tmp_path / "result.csv"
    out.write_bytes(b"immutable previous output")
    with pytest.raises(ex.ExportError, match="0 matching"):
        ex.export_nba_pack(source, out, DATE)
    assert out.read_bytes() == b"immutable previous output"
    assert not list(tmp_path.glob(".nba-export-*"))


@pytest.mark.parametrize("kind", ["missing", "empty", "malformed", "wrong_day", "stale", "future"])
def test_source_scan_skips_bad_first_source_and_finds_eligible_later_one(tmp_path, kind):
    bad = tmp_path / "first.csv"
    if kind != "missing":
        seed(bad, [] if kind == "empty" else [{"slate_date": "2026-11-04"}] if kind == "wrong_day" else None)
        if kind == "malformed":
            bad.write_text("player,line\nSomeone,1\n")
        if kind in {"stale", "future"}:
            when = datetime.now(timezone.utc) + timedelta(hours=-13 if kind == "stale" else 1)
            os.utime(bad, (when.timestamp(), when.timestamp()))
    good = seed(tmp_path / "second.csv")
    assert ex.resolve_source(slate_date=DATE, candidates=[bad, good]) == good


def test_explicit_bad_source_does_not_silently_switch_to_another(tmp_path, monkeypatch):
    bad = seed(tmp_path / "bad.csv", [])
    good = seed(tmp_path / "good.csv")
    monkeypatch.setattr(ex, "CANDIDATE_SOURCES", [good])
    with pytest.raises(ex.ExportError):
        ex.resolve_source(str(bad), slate_date=DATE)


def test_no_self_output_recycling_even_when_export_is_fresh(tmp_path):
    source = seed(tmp_path / "nba_only.csv")
    with pytest.raises(ex.ExportError, match="cannot serve"):
        ex.read_source(source, DATE)
    assert all(path.name != "nba_only.csv" for path in ex.CANDIDATE_SOURCES)


def test_copied_csv_does_not_refresh_old_producer_metadata(tmp_path):
    source = seed(tmp_path / "props.csv")
    source.with_suffix(".json").write_text(json.dumps({
        "generated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "slate_date": DATE,
    }))
    with pytest.raises(ex.ExportError, match="producer timestamp"):
        ex.read_source(source, DATE)


@pytest.mark.parametrize("quote_time", [
    "stale", "future", "not-a-time", "", None, "2026-11-05T20:00:00",
])
def test_refreshed_producer_cannot_launder_invalid_quote_timestamp(tmp_path, quote_time):
    now = datetime.now(timezone.utc)
    if quote_time in {"stale", "future"}:
        quote_time = (now + timedelta(hours=-13 if quote_time == "stale" else 1)).isoformat()
    source = seed(tmp_path / "props.csv")
    source.with_suffix(".json").write_text(json.dumps({
        "generated_at": now.isoformat(), "slate_date": DATE,
        "hardrock_fetched_at": quote_time,
    }))
    out = tmp_path / "result.csv"
    out.write_text("previous frozen output")
    with pytest.raises(ex.ExportError, match="hardrock_fetched_at"):
        ex.export_nba_pack(source, out, DATE)
    assert out.read_text() == "previous frozen output"


def test_fresh_aware_quote_timestamp_is_accepted(tmp_path):
    now = datetime.now(timezone.utc)
    source = seed(tmp_path / "props.csv")
    source.with_suffix(".json").write_text(json.dumps({
        "generated_at": now.isoformat(), "slate_date": DATE,
        "hardrock_fetched_at": (now - timedelta(hours=1)).isoformat(),
    }))
    assert len(ex.read_source(source, DATE)) == 1


def test_aware_tip_disagreement_is_not_relabelled(tmp_path):
    source = seed(tmp_path / "props.csv", [{"game_time": "2026-11-07T02:00:00Z"}])
    with pytest.raises(ex.ExportError, match="disagrees"):
        ex.read_source(source, DATE)


@pytest.mark.parametrize("arguments", [
    {"allow_latest_fallback": True}, {"min_edge_pct": 99}, {"min_edge_pct": float("nan")},
])
def test_rejected_export_never_creates_output_parent(tmp_path, arguments):
    source = seed(tmp_path / "props.csv")
    out = tmp_path / "uncreated/result.csv"
    with pytest.raises(ex.ExportError):
        ex.export_nba_pack(source, out, DATE, **arguments)
    assert not out.parent.exists()


def test_replace_failure_preserves_old_output_and_removes_partial(tmp_path, monkeypatch):
    source = seed(tmp_path / "props.csv")
    out = tmp_path / "result.csv"
    out.write_text("old")
    def locked(*_args):
        raise PermissionError("locked destination")
    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError):
        ex.export_nba_pack(source, out, DATE)
    assert out.read_text() == "old"
    assert not list(tmp_path.glob(".nba-export-*"))


def test_no_implicit_pipeline_or_network_fallback(tmp_path, monkeypatch):
    def forbidden(*_a, **_k):
        pytest.fail("Exporter must not start producer/network processes")
    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ex.ExportError, match="No fresh"):
        ex.resolve_source(slate_date=DATE, candidates=[])
    with pytest.raises(ex.ExportError, match="cannot launch"):
        ex.resolve_source(auto_refresh=True, slate_date=DATE)


def test_cli_wrong_date_exits_nonzero_and_creates_nothing(tmp_path):
    source = seed(tmp_path / "props.csv", [{"slate_date": "2026-11-04"}])
    out = tmp_path / "uncreated/result.csv"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/export_nba_extra_pack.py"),
                             "--date", DATE, "--src", str(source), "--out", str(out)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and "0 matching" in result.stderr
    assert not out.parent.exists()
