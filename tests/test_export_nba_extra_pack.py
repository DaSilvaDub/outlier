"""Tests for scripts/export_nba_extra_pack.py slate-date handling.

Regression guard: `--allow-latest-fallback` was `action="store_true", default=True`,
so it could only ever be True and the fail-closed branch was unreachable from the
CLI. `refresh_nba_pack.ps1` passes `--date $Date`, so a source CSV still holding the
previous slate was published as that date's pack with no warning, and `--fail-on-empty`
could not catch it because the pack was not empty.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_nba_extra_pack as exp  # noqa: E402


@pytest.fixture(autouse=True)
def _no_disk_check(monkeypatch):
    # check_disk_space() probes "C:\\"; keep these tests off the host filesystem.
    monkeypatch.setattr(exp, "check_disk_space", lambda min_gb=5.0: None)


def _source(tmp_path: Path, *dates: str) -> Path:
    src = tmp_path / "prop_edges_latest.csv"
    with open(src, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["slate_date", "player", "market", "line", "edge_pct"])
        w.writeheader()
        for i, d in enumerate(dates, start=1):
            w.writerow(
                {
                    "slate_date": d,
                    "player": f"Player {i}",
                    "market": "PTS",
                    "line": "20.5",
                    "edge_pct": "7.0",
                }
            )
    return src


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_requested_date_rows_are_used(tmp_path):
    src = _source(tmp_path, "2026-11-08", "2026-11-09")
    out = tmp_path / "nba_only.csv"
    assert exp.export_nba_pack(src, out, slate_date="2026-11-09") == 1
    assert [r["slate_date"] for r in _rows(out)] == ["2026-11-09"]


def test_fallback_disabled_publishes_empty_pack(tmp_path):
    """The fail-closed branch must be reachable, not dead code."""
    src = _source(tmp_path, "2026-11-08")
    out = tmp_path / "nba_only.csv"
    assert (
        exp.export_nba_pack(src, out, slate_date="2026-11-09", allow_latest_fallback=False) == 0
    )
    assert _rows(out) == []


def test_fallback_disabled_with_fail_on_empty_raises(tmp_path):
    src = _source(tmp_path, "2026-11-08")
    out = tmp_path / "nba_only.csv"
    with pytest.raises(RuntimeError, match="0 candidate rows"):
        exp.export_nba_pack(
            src,
            out,
            slate_date="2026-11-09",
            allow_latest_fallback=False,
            fail_on_empty=True,
        )


def test_stale_slate_fallback_warns_on_stderr(tmp_path, capsys):
    """Publishing another slate's rows under today's date must never be silent."""
    src = _source(tmp_path, "2026-11-08", "2026-11-08")
    out = tmp_path / "nba_only.csv"
    assert exp.export_nba_pack(src, out, slate_date="2026-11-09") == 2
    err = capsys.readouterr().err
    assert "no row for slate_date '2026-11-09'" in err
    assert "2026-11-08" in err
    # The rows really are the other slate's: the warning is the only signal downstream gets.
    assert {r["slate_date"] for r in _rows(out)} == {"2026-11-08"}


def test_no_allow_latest_fallback_is_accepted_by_the_cli(tmp_path, monkeypatch, capsys):
    src = _source(tmp_path, "2026-11-08")
    out = tmp_path / "nba_only.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_nba_extra_pack.py",
            "--date",
            "2026-11-09",
            "--src",
            str(src),
            "--out",
            str(out),
            "--no-auto-refresh",
            "--no-allow-latest-fallback",
            "--fail-on-empty",
        ],
    )
    assert exp.main() == 1  # SystemExit(2) if argparse did not know the flag
    assert _rows(out) == []
