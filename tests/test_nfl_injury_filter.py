"""Injury filter must fail closed when a requested source fails and no other has rows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import URLError

from outlier_nfl import injuries
from scripts.filter_nfl_ruled_out import main


def _run(monkeypatch, args: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["filter_nfl_ruled_out.py", *args])
    return main()


def _pack(tmp_path: Path) -> tuple[Path, bytes, Path]:
    nfl = tmp_path / "nfl"
    (nfl / "tape").mkdir(parents=True)
    csv_path = tmp_path / "nfl_only.csv"
    original = b"player,team,market\nJahmyr Gibbs,DET,RUSH_YDS\n"
    csv_path.write_bytes(original)
    return nfl, original, csv_path


def _espn_down(**_kwargs):
    raise URLError("espn down")


def test_espn_down_and_empty_tape_does_not_rewrite_and_exits_nonzero(tmp_path, monkeypatch):
    nfl, original, csv_path = _pack(tmp_path)
    (nfl / "tape" / "prior_week.json").write_text(json.dumps({"inactive": {}}), encoding="utf-8")
    monkeypatch.setattr(injuries, "fetch_espn_injuries", _espn_down)
    out = tmp_path / "filtered.csv"
    cache = tmp_path / "cache.json"
    summary = tmp_path / "summary.json"
    bets = tmp_path / "nfl_best_bets.json"
    bets_original = b'{"picks":[]}\n'
    bets.write_bytes(bets_original)
    bets_md = tmp_path / "nfl_best_bets.md"
    bets_md.write_text("keep\n", encoding="utf-8")

    rc = _run(
        monkeypatch,
        [
            "--csv",
            str(csv_path),
            "--out",
            str(out),
            "--nfl-dir",
            str(nfl),
            "--cache",
            str(cache),
            "--summary-json",
            str(summary),
            "--best-bets-json",
            str(bets),
            "--best-bets-md",
            str(bets_md),
        ]
    )

    assert rc != 0
    assert not out.exists()
    assert not cache.exists()
    assert not summary.exists()
    assert csv_path.read_bytes() == original
    assert bets.read_bytes() == bets_original
    assert bets_md.read_text(encoding="utf-8") == "keep\n"


def test_espn_down_with_tape_rows_still_filters(tmp_path, monkeypatch, capsys):
    nfl, _original, csv_path = _pack(tmp_path)
    (nfl / "tape" / "prior_week.json").write_text(
        json.dumps({"inactive": {"DET": ["Jahmyr Gibbs"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(injuries, "fetch_espn_injuries", _espn_down)

    rc = _run(monkeypatch, ["--csv", str(csv_path), "--nfl-dir", str(nfl)])

    assert rc == 0
    text = csv_path.read_text(encoding="utf-8")
    assert "Jahmyr Gibbs" not in text
    assert "player" in text
    err = capsys.readouterr().err
    assert "ESPN fetch failed" in err


def test_espn_ok_and_empty_tape_still_filters(tmp_path, monkeypatch):
    nfl, _original, csv_path = _pack(tmp_path)
    (nfl / "tape" / "prior_week.json").write_text(json.dumps({"inactive": {}}), encoding="utf-8")

    def _espn(**_kwargs):
        return {"DET": [{"name": "Jahmyr Gibbs", "status": "Out", "source": "espn"}]}

    monkeypatch.setattr(injuries, "fetch_espn_injuries", _espn)

    rc = _run(monkeypatch, ["--csv", str(csv_path), "--nfl-dir", str(nfl)])

    assert rc == 0
    assert "Jahmyr Gibbs" not in csv_path.read_text(encoding="utf-8")


def test_skipped_espn_and_empty_tape_is_not_a_source_failure(tmp_path, monkeypatch):
    nfl, original, csv_path = _pack(tmp_path)
    (nfl / "tape" / "prior_week.json").write_text(json.dumps({"inactive": {}}), encoding="utf-8")
    monkeypatch.setattr(injuries, "fetch_espn_injuries", _espn_down)

    rc = _run(monkeypatch, ["--csv", str(csv_path), "--nfl-dir", str(nfl), "--no-espn"])

    assert rc == 0
    assert "Jahmyr Gibbs" in csv_path.read_text(encoding="utf-8")
    assert original.splitlines()[1] in csv_path.read_bytes().splitlines()
