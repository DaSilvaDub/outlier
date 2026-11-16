"""Tests for scripts/export_nba_extra_pack.py (slate-date fallback + min-edge gate)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_nba_extra_pack as exp


def _write_src(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["slate_date", "player", "market", "line", "edge_pct", "recommended_side"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _row(slate_date: str, player: str, edge_pct: str) -> dict[str, str]:
    return {
        "slate_date": slate_date,
        "player": player,
        "market": "PTS",
        "line": "20.5",
        "edge_pct": edge_pct,
        "recommended_side": "OVER",
    }


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    p = tmp_path / "prop_edges_latest.csv"
    _write_src(p, [_row("2026-11-06", "Stale Guy", "7.5"), _row("2026-11-06", "Stale Two", "-9.0")])
    return p


def test_requested_date_wins_when_it_has_rows(tmp_path: Path, src: Path) -> None:
    _write_src(src, [_row("2026-11-06", "Yesterday", "7.5"), _row("2026-11-07", "Today", "6.0")])
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-07") == 1
    assert [r["player"] for r in _read(out)] == ["Today"]


def test_fallback_to_another_slate_is_reported_on_stderr(
    tmp_path: Path, src: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fallback substitutes another slate's rows; it must not do so silently."""
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-07") == 2
    assert all(r["slate_date"] == "2026-11-06" for r in _read(out))
    err = capsys.readouterr().err
    assert "No rows match slate_date '2026-11-07'" in err
    assert "may belong to another slate" in err


def test_fallback_can_be_refused(tmp_path: Path, src: Path) -> None:
    out = tmp_path / "nba_only.csv"

    count = exp.export_nba_pack(src, out, slate_date="2026-11-07", allow_latest_fallback=False)

    assert count == 0
    assert _read(out) == []


def test_no_allow_latest_fallback_flag_exists(tmp_path: Path, src: Path, monkeypatch) -> None:
    """Regression: action="store_true" with default=True made the off-switch unreachable."""
    out = tmp_path / "nba_only.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_nba_extra_pack.py",
            "--date",
            "2026-11-07",
            "--src",
            str(src),
            "--out",
            str(out),
            "--no-auto-refresh",
            "--no-allow-latest-fallback",
        ],
    )

    assert exp.main() == 0
    assert _read(out) == []


def test_min_edge_excludes_negative_edge(tmp_path: Path, src: Path) -> None:
    """Regression: abs(edge) let a -9.0% edge clear --min-edge 5."""
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-06", min_edge_pct=5.0) == 1
    kept = _read(out)
    assert [r["player"] for r in kept] == ["Stale Guy"]
    assert kept[0]["edge_pct"] == "7.5"


def test_min_edge_drops_unreadable_edge(tmp_path: Path) -> None:
    """A non-numeric edge has not cleared the bar, so it is not passed through."""
    src = tmp_path / "src.csv"
    _write_src(src, [_row("2026-11-06", "Pct Guy", "7.5%"), _row("2026-11-06", "Good Guy", "8.0")])
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-06", min_edge_pct=5.0) == 1
    assert [r["player"] for r in _read(out)] == ["Good Guy"]


def test_kept_rows_are_reranked_from_one(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    _write_src(
        src,
        [
            _row("2026-11-06", "Low", "1.0"),
            _row("2026-11-06", "High", "9.0"),
            _row("2026-11-06", "Mid", "6.0"),
        ],
    )
    out = tmp_path / "nba_only.csv"

    exp.export_nba_pack(src, out, slate_date="2026-11-06", min_edge_pct=5.0)

    assert [(r["rank"], r["player"]) for r in _read(out)] == [("1", "High"), ("2", "Mid")]


def test_empty_pack_warns_and_can_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "src.csv"
    _write_src(src, [_row("2026-11-06", "Only Guy", "1.0")])
    out = tmp_path / "nba_only.csv"

    assert exp.export_nba_pack(src, out, slate_date="2026-11-06", min_edge_pct=5.0) == 0
    assert "0 candidate rows" in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="0 candidate rows"):
        exp.export_nba_pack(src, out, slate_date="2026-11-06", min_edge_pct=5.0, fail_on_empty=True)
