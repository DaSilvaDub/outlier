"""F27 persistence: unique temps, bounded writer locks, run-ID idempotence (#230)."""

from __future__ import annotations

import json
from pathlib import Path

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
