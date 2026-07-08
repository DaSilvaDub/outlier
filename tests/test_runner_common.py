import csv
import hashlib

import pytest

from outlier_scrapers import pack, runner_common


def test_sha256_bytes_matches_hashlib():
    data = b"hello"
    assert runner_common.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_compute_request_hash_is_order_independent():
    a = runner_common.compute_request_hash({"x": 1, "y": 2})
    b = runner_common.compute_request_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64


def test_extract_yaml_request_hash_reads_front_matter():
    content = "---\nmodel: m\nrequest_sha256: abc123\n---\n\nbody"
    assert runner_common.extract_yaml_request_hash(content) == "abc123"


def test_extract_yaml_request_hash_none_when_no_front_matter():
    assert runner_common.extract_yaml_request_hash("no front matter") is None


def test_validate_candidates_returns_bytes_and_hash(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(pack.CANDIDATES_HEADER)
        w.writerow(["data", "row"])
    raw, digest = runner_common.validate_candidates(tmp_path)
    assert digest == hashlib.sha256(raw).hexdigest()
    assert b"data,row" in raw


def test_validate_candidates_rejects_bad_header(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["bad", "header"])
        w.writerow(["x", "y"])
    with pytest.raises(runner_common.RunnerError):
        runner_common.validate_candidates(tmp_path)


def test_atomic_write_writes_front_matter_then_body_and_leaves_no_tmp(tmp_path):
    runner_common.atomic_write(tmp_path, "out.md", "---\nk: v\n---\n\n", "BODY")
    out = tmp_path / "out.md"
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert text.endswith("BODY")
    assert list(tmp_path.glob("out_tmp_*")) == []


def test_load_game_totals_missing_returns_empty_hash(tmp_path):
    raw, digest = runner_common.load_game_totals(tmp_path)
    assert raw is None
    assert digest == runner_common.empty_game_totals_hash()


def test_build_reasoning_data_block_includes_totals(tmp_path):
    (tmp_path / runner_common.GAME_TOTALS_NAME).write_text(
        "sport,market_id\nMLB,gm1\n", encoding="utf-8"
    )
    totals_bytes, _ = runner_common.load_game_totals(tmp_path)
    block = runner_common.build_reasoning_data_block(b"a,b\n1,2", totals_bytes)
    assert "candidates.csv:" in block
    assert "GAME_TOTALS.CSV" in block
    assert "gm1" in block
