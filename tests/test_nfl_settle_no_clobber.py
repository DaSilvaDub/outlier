"""settle refuses existing --out-* paths unless --overwrite (#230, deferred no-clobber)."""

from __future__ import annotations

import json

import pytest

from outlier_nfl import settle

PRED = {"records": [{"event_id": "e1", "player_name": "Travis Kelce", "team": "KC",
                     "market": "REC", "position": "OVER", "line": 4.5, "tier": 1}]}
BOX = {"events": []}


@pytest.fixture
def inputs(tmp_path):
    (tmp_path / "pred.json").write_text(json.dumps(PRED), encoding="utf-8")
    (tmp_path / "box.json").write_text(json.dumps(BOX), encoding="utf-8")
    return tmp_path


def _args(d, *extra):
    return ["--predictions", str(d / "pred.json"), "--boxscores", str(d / "box.json"),
            "--source", "calibrated", "--all-calibrated", *extra]


@pytest.mark.parametrize("flag,name", [("--out-json", "s.json"), ("--out-md", "s.md")])
def test_existing_output_is_refused_and_kept(inputs, capsys, flag, name):
    out = inputs / name
    out.write_text("previous", encoding="utf-8")
    assert settle.main(_args(inputs, flag, str(out))) == settle.EXIT_OUTPUT_EXISTS
    assert out.read_text("utf-8") == "previous"
    err = capsys.readouterr().err
    assert "already exists" in err and "--overwrite" in err


def test_overwrite_replaces(inputs):
    out = inputs / "s.json"
    out.write_text("previous", encoding="utf-8")
    assert settle.main(_args(inputs, "--out-json", str(out), "--overwrite")) == 0
    assert "previous" not in out.read_text("utf-8")


def test_new_paths_still_write(inputs):
    out = inputs / "new.json"
    assert settle.main(_args(inputs, "--out-json", str(out))) == 0
    assert out.exists()


def test_refusal_happens_before_any_output(inputs):
    md = inputs / "s.md"
    js = inputs / "s.json"
    js.write_text("previous", encoding="utf-8")
    assert settle.main(_args(inputs, "--out-md", str(md), "--out-json", str(js))) == 2
    assert not md.exists()
