"""Tests for scripts/filter_perfect_hit_props.py hygiene helpers."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import filter_perfect_hit_props as fphp  # noqa: E402


def test_drop_hr_under():
    rows = [
        {
            "player": "A",
            "market_label": "A - Home Runs",
            "side": "UNDER",
            "line": "0.5",
            "team": "NYY",
            "matchup": "NYY @ PHI",
        },
        {
            "player": "B",
            "market_label": "B - Home Runs",
            "side": "OVER",
            "line": "0.5",
            "team": "NYY",
            "matchup": "NYY @ PHI",
        },
        {
            "player": "C",
            "market_label": "C - Doubles",
            "side": "UNDER",
            "line": "0.5",
            "team": "PHI",
            "matchup": "NYY @ PHI",
        },
    ]
    opts = fphp.FilterOptions(allow_matchups=None)
    kept, rejected, reasons = fphp.filter_rows(rows, opts)
    assert len(kept) == 2
    assert reasons["hr_under_forbidden"] == 1
    assert all(r["player"] != "A" for r in kept)


def test_off_slate_matchup():
    rows = [
        {
            "player": "X",
            "market_label": "X - Doubles",
            "side": "UNDER",
            "line": "0.5",
            "team": "CLE",
            "matchup": "CLE @ CIN",
        },
        {
            "player": "Y",
            "market_label": "Y - Doubles",
            "side": "UNDER",
            "line": "0.5",
            "team": "NYY",
            "matchup": "NYY @ PHI",
        },
    ]
    opts = fphp.FilterOptions(allow_matchups=frozenset({"NYY @ PHI"}))
    kept, rejected, reasons = fphp.filter_rows(rows, opts)
    assert len(kept) == 1
    assert kept[0]["player"] == "Y"
    assert reasons["off_slate_matchup"] == 1


def test_team_not_in_matchup():
    row = {
        "player": "Z",
        "market_label": "Z - Doubles",
        "side": "UNDER",
        "line": "0.5",
        "team": "BOS",
        "matchup": "NYY @ PHI",
    }
    opts = fphp.FilterOptions(require_team_in_matchup=True, allow_matchups=None)
    assert fphp.reject_reason(row, opts) == "team_not_in_matchup"


def test_optional_prohibited_market_filter_uses_current_mlb_contract():
    opts = fphp.FilterOptions(
        drop_prohibited_markets=True,
        require_team_in_matchup=False,
    )

    for market in ("Pitcher - Hits Allowed", "Pitcher - Walks Allowed"):
        row = {"market_label": market, "side": "OVER", "matchup": "NYY @ PHI"}
        assert fphp.reject_reason(row, opts) == f"prohibited_market_forbidden:{fphp.market_token(market)}"

    total_bases = {
        "market_label": "Batter - Total Bases",
        "side": "OVER",
        "matchup": "NYY @ PHI",
    }
    assert fphp.reject_reason(total_bases, opts) is None


def test_allowlist_from_dossiers(tmp_path: Path):
    d = tmp_path / "dossiers"
    d.mkdir()
    (d / "MLB_abc_laa---sf.md").write_text(
        "## MLB game dossier — Los Angeles Angels @ San Francisco Giants (LAA @ SF)\n",
        encoding="utf-8",
    )
    (d / "MLB_def_nyy---phi.md").write_text(
        "## NYY @ PHI\n",
        encoding="utf-8",
    )
    allow = fphp.allowlist_from_dossiers_dir(d)
    assert "LAA @ SF" in allow
    assert "NYY @ PHI" in allow


def test_process_file_writes(tmp_path: Path):
    src = tmp_path / "MLB_100_hit_rate.csv"
    with open(src, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["player", "market_label", "side", "line", "team", "matchup"],
        )
        w.writeheader()
        w.writerow(
            {
                "player": "A",
                "market_label": "A - Home Runs",
                "side": "UNDER",
                "line": "0.5",
                "team": "NYY",
                "matchup": "NYY @ PHI",
            }
        )
        w.writerow(
            {
                "player": "B",
                "market_label": "B - Doubles",
                "side": "UNDER",
                "line": "0.5",
                "team": "NYY",
                "matchup": "NYY @ PHI",
            }
        )
    out = tmp_path / "out"
    summary = fphp.process_file(
        src,
        out,
        fphp.FilterOptions(allow_matchups=frozenset({"NYY @ PHI"})),
    )
    assert summary["kept"] == 1
    assert summary["rejected"] == 1
    kept_path = Path(summary["kept_path"])
    rows = list(csv.DictReader(kept_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["player"] == "B"
