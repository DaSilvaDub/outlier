"""Tests for scripts/export_nba_extra_pack.py (the --date fallback must be switchable off)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_nba_extra_pack as exp  # noqa: E402


@pytest.fixture(autouse=True)
def _no_host_disk_check(monkeypatch):
    """NBA-SYS-001 checks C:\\, which does not exist off Windows."""
    monkeypatch.setattr(exp, "check_disk_space", lambda min_gb=5.0: None)


def _write_src(path: Path, slate_dates: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=exp.FIELDNAMES)
        w.writeheader()
        for i, d in enumerate(slate_dates, start=1):
            row = dict.fromkeys(exp.FIELDNAMES, "")
            row.update(slate_date=d, player=f"Player {i}", market="PTS", line="20.5")
            w.writerow(row)


def _out_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_date_match_wins_over_fallback(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    _write_src(src, ["2026-11-01", "2026-11-08"])

    assert exp.export_nba_pack(src, out, slate_date="2026-11-08") == 1
    rows = _out_rows(out)
    assert [r["slate_date"] for r in rows] == ["2026-11-08"]
    assert [r["rank"] for r in rows] == ["1"]


def test_fallback_on_exports_another_slate(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    _write_src(src, ["2026-11-01"])

    assert exp.export_nba_pack(src, out, slate_date="2026-11-08", allow_latest_fallback=True) == 1
    assert [r["slate_date"] for r in _out_rows(out)] == ["2026-11-01"]


def test_fallback_off_exports_nothing_for_an_unmatched_date(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    _write_src(src, ["2026-11-01"])

    assert exp.export_nba_pack(src, out, slate_date="2026-11-08", allow_latest_fallback=False) == 0
    assert _out_rows(out) == []


def test_fallback_off_can_fail_closed(tmp_path):
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    _write_src(src, ["2026-11-01"])

    with pytest.raises(RuntimeError, match="0 candidate rows"):
        exp.export_nba_pack(
            src, out, slate_date="2026-11-08", allow_latest_fallback=False, fail_on_empty=True
        )


@pytest.mark.parametrize(
    ("argv", "expected_rows"),
    [
        ([], 1),
        (["--allow-latest-fallback"], 1),
        (["--no-allow-latest-fallback"], 0),
    ],
)
def test_cli_exposes_both_sides_of_the_fallback_toggle(tmp_path, monkeypatch, argv, expected_rows):
    """store_true + default=True left --no-... undefined, so the off switch was unreachable."""
    src, out = tmp_path / "src.csv", tmp_path / "out.csv"
    _write_src(src, ["2026-11-01"])

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_nba_extra_pack.py",
            "--src",
            str(src),
            "--out",
            str(out),
            "--date",
            "2026-11-08",
            *argv,
        ],
    )
    assert exp.main() == 0
    assert len(_out_rows(out)) == expected_rows
