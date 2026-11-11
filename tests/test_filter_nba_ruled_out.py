"""Tests for scripts/filter_nba_ruled_out.py (fail closed when the roster registry is unusable)."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import filter_nba_ruled_out as flt  # noqa: E402

FIELDS = ["rank", "player", "market", "line"]


def _write_pack(path: Path, players: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i, p in enumerate(players, start=1):
            w.writerow({"rank": i, "player": p, "market": "PTS", "line": "20.5"})


def _write_registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "teams": {
                    "LAL": {
                        "roster": [
                            {"name": "Out Player", "status": "OUT"},
                            {"name": "Hurt Player", "status": "ACTIVE", "injuries": ["ankle"]},
                            {"name": "Healthy Player", "status": "ACTIVE", "injuries": []},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _players(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [r["player"] for r in csv.DictReader(f)]


def test_removes_ruled_out_players_and_reranks(tmp_path):
    pack, reg, summary = tmp_path / "pack.csv", tmp_path / "reg.json", tmp_path / "summary.json"
    _write_pack(pack, ["Out Player", "Healthy Player", "Hurt Player", "Other"])
    _write_registry(reg)

    before, after, removed = flt.filter_csv(pack, pack, summary, reg)

    assert (before, after) == (4, 2)
    assert sorted(removed) == ["Hurt Player", "Out Player"]
    assert _players(pack) == ["Healthy Player", "Other"]
    with open(pack, encoding="utf-8") as f:
        assert [r["rank"] for r in csv.DictReader(f)] == ["1", "2"]
    assert json.loads(summary.read_text(encoding="utf-8"))["removed_count"] == 2


def test_missing_registry_fails_instead_of_filtering_nothing(tmp_path):
    pack = tmp_path / "pack.csv"
    _write_pack(pack, ["Out Player"])

    with pytest.raises(flt.RegistryError, match="not found"):
        flt.filter_csv(pack, pack, None, tmp_path / "missing.json")
    assert _players(pack) == ["Out Player"]  # input left untouched


def test_corrupt_registry_fails(tmp_path):
    pack, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    _write_pack(pack, ["Out Player"])
    reg.write_text('{"teams": {', encoding="utf-8")

    with pytest.raises(flt.RegistryError, match="unreadable"):
        flt.filter_csv(pack, pack, None, reg)


def test_registry_with_no_players_fails(tmp_path):
    pack, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    _write_pack(pack, ["Out Player"])
    reg.write_text(json.dumps({"teams": {}}), encoding="utf-8")

    with pytest.raises(flt.RegistryError, match="no players"):
        flt.filter_csv(pack, pack, None, reg)


def test_missing_input_csv_fails(tmp_path):
    reg = tmp_path / "reg.json"
    _write_registry(reg)

    with pytest.raises(FileNotFoundError):
        flt.filter_csv(tmp_path / "nope.csv", tmp_path / "out.csv", None, reg)


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    pack, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    _write_pack(pack, ["Out Player", "Other"])
    _write_registry(reg)

    argv = ["filter_nba_ruled_out.py", "--csv", str(pack), "--out", str(pack)]
    monkeypatch.setattr(sys, "argv", argv + ["--registry", str(reg)])
    assert flt.main() == 0

    monkeypatch.setattr(sys, "argv", argv + ["--registry", str(tmp_path / "missing.json")])
    assert flt.main() == 2
    assert "roster registry not found" in capsys.readouterr().err


def test_a_truncated_pack_row_is_filtered_not_a_crash(tmp_path):
    """A short row leaves None in the player cell; it must not abort the whole filter."""
    pack, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    _write_registry(reg)
    pack.write_text(
        "rank,player,market,line\n1,Out Player,PTS,20.5\n2,Healthy Player,PTS,18.5\n3\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"

    before, after, removed = flt.filter_csv(pack, out, None, reg)
    assert (before, after, removed) == (3, 2, ["Out Player"])
    with open(out, newline="", encoding="utf-8") as f:
        kept = list(csv.DictReader(f))
    assert [r["player"] for r in kept] == ["Healthy Player", ""]  # the short row is kept, blank
    assert [r["rank"] for r in kept] == ["1", "2"]


def test_a_pack_row_with_extra_cells_is_written_not_rejected(tmp_path):
    """Cells past the header land under DictReader's restkey; DictWriter must not reject them."""
    pack, reg = tmp_path / "pack.csv", tmp_path / "reg.json"
    _write_registry(reg)
    pack.write_text(
        "rank,player,market,line\n1,Healthy Player,PTS,20.5,STRAY\n", encoding="utf-8"
    )
    out = tmp_path / "out.csv"

    assert flt.filter_csv(pack, out, None, reg) == (1, 1, [])
    with open(out, newline="", encoding="utf-8") as f:
        kept = list(csv.DictReader(f))
    assert [r["player"] for r in kept] == ["Healthy Player"]
