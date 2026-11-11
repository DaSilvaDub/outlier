"""Tests for scripts/export_nba_extra_pack.py (slate-date fallback must be visible and
switchable; it publishes rows under the requested date)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_nba_extra_pack as exp  # noqa: E402

SRC_FIELDS = ["rank", "slate_date", "player", "market", "line", "edge_pct"]


def _write_source(path: Path, rows: list[tuple[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SRC_FIELDS)
        w.writeheader()
        for i, (day, player) in enumerate(rows, start=1):
            w.writerow(
                {
                    "rank": i,
                    "slate_date": day,
                    "player": player,
                    "market": "PTS",
                    "line": "20.5",
                    "edge_pct": "8",
                }
            )


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_rows_for_the_requested_date_are_used_without_any_fallback(tmp_path, capsys) -> None:
    src = tmp_path / "prop_edges_latest.csv"
    _write_source(src, [("2026-11-09", "Old Player"), ("2026-11-11", "Today Player")])
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-11") == 1
    assert [r["player"] for r in _read(out)] == ["Today Player"]
    assert "PIPELINE WARNING" not in capsys.readouterr().err


def test_falling_back_to_another_slate_is_announced_on_stderr(tmp_path, capsys) -> None:
    src = tmp_path / "prop_edges_latest.csv"
    _write_source(src, [("2026-11-09", "Old Player")])
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-11", fail_on_empty=True) == 1
    assert [r["slate_date"] for r in _read(out)] == ["2026-11-09"]  # not the requested date
    err = capsys.readouterr().err
    assert "PIPELINE WARNING" in err and "2026-11-09" in err and "2026-11-11" in err


def test_the_fallback_can_be_declined_and_then_the_pack_is_empty(tmp_path) -> None:
    src = tmp_path / "prop_edges_latest.csv"
    _write_source(src, [("2026-11-09", "Old Player")])
    out = tmp_path / "nba_only.csv"

    assert (
        exp.export_nba_pack(src, out, slate_date="2026-11-11", allow_latest_fallback=False) == 0
    )
    assert _read(out) == []


def test_declining_the_fallback_with_fail_on_empty_halts(tmp_path) -> None:
    src = tmp_path / "prop_edges_latest.csv"
    _write_source(src, [("2026-11-09", "Old Player")])
    with pytest.raises(RuntimeError, match="0 candidate rows"):
        exp.export_nba_pack(
            src,
            tmp_path / "nba_only.csv",
            slate_date="2026-11-11",
            allow_latest_fallback=False,
            fail_on_empty=True,
        )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [([], True), (["--allow-latest-fallback"], True), (["--no-allow-latest-fallback"], False)],
)
def test_the_cli_can_express_both_sides_of_the_fallback_switch(
    tmp_path, monkeypatch, argv, expected
) -> None:
    """``store_true`` with ``default=True`` made the documented opt-out unreachable."""
    src = tmp_path / "prop_edges_latest.csv"
    _write_source(src, [("2026-11-09", "Old Player")])
    out = tmp_path / "nba_only.csv"
    seen: dict[str, object] = {}

    def fake_export(src_path, out_path, **kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(exp, "export_nba_pack", fake_export)
    monkeypatch.setattr(
        sys,
        "argv",
        ["export_nba_extra_pack.py", "--src", str(src), "--out", str(out), "--date", "2026-11-11"]
        + argv,
    )
    assert exp.main() == 0
    assert seen["allow_latest_fallback"] is expected
