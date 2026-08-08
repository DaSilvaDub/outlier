"""Tests for scripts/organize_today_run2.py replace-export + perfect-hit filters."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import organize_today_run2 as org  # noqa: E402
from filter_perfect_hit_props import FilterOptions  # noqa: E402


def _write_candidates(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "sport",
        "matchup",
        "selection",
        "board",
        "actionable",
        "team",
        "opponent",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _write_cards(path: Path, cards_a: list[dict], cards_b: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "league": "MLB",
        "board_a": cards_a,
        "board_b": cards_b or [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _side(side: str, l5: float, l10: float, l20: float) -> dict:
    return {
        "side": side,
        "line": "0.5",
        "hit_rates": {"l5_pct": l5, "l10_pct": l10, "l20_pct": l20},
    }


def test_generate_specific_packs_writes_empty_headers(tmp_path: Path):
    cand = tmp_path / "candidates.csv"
    _write_candidates(cand, [])  # header only
    out = tmp_path / "extra"
    org.generate_specific_packs(cand, out)
    mlb = out / "mlb_only.csv"
    wnba = out / "wnba_only.csv"
    assert mlb.exists() and wnba.exists()
    assert list(csv.DictReader(mlb.open(encoding="utf-8"))) == []
    assert list(csv.DictReader(wnba.open(encoding="utf-8"))) == []


def test_generate_specific_packs_overwrites_stale(tmp_path: Path):
    out = tmp_path / "extra"
    out.mkdir()
    stale = out / "mlb_only.csv"
    stale.write_text("sport,selection\nMLB,OLD STALE\n", encoding="utf-8")

    cand = tmp_path / "candidates.csv"
    _write_candidates(cand, [])  # empty board
    org.generate_specific_packs(cand, out)

    rows = list(csv.DictReader(stale.open(encoding="utf-8")))
    assert rows == []  # stale data gone


def test_replace_dir_wipes_contents(tmp_path: Path):
    d = tmp_path / "bucket"
    d.mkdir()
    leftover = d / "stale.md"
    leftover.write_text("old", encoding="utf-8")
    org.replace_dir(d)
    assert d.is_dir()
    assert list(d.iterdir()) == []


def test_copy_prompt_outputs_excludes_sequential_prompts_unless_opted_in(tmp_path: Path):
    out_dir = tmp_path / "today"
    desk2_src = out_dir / "prompts" / "Desk2_Manual"
    desk2_src.mkdir(parents=True)
    (desk2_src / "1_PhaseQ_pack.txt").write_text("Q", encoding="utf-8")

    generic = tmp_path / "generic"
    hitrate = tmp_path / "hitrate"
    totals = tmp_path / "totals"
    for path in (generic, hitrate, totals):
        path.mkdir()

    flat_q = out_dir / "Q_chatgpt_stale.txt"
    flat_numbered_q = out_dir / "1_PhaseQ_chatgpt_stale.txt"
    flat_q.write_text("STALE Q", encoding="utf-8")
    flat_numbered_q.write_text("STALE NUMBERED Q", encoding="utf-8")
    org.copy_prompt_outputs(out_dir, generic, hitrate, totals)
    assert all(not (path / "1_PhaseQ_pack.txt").exists() for path in (generic, hitrate, totals))
    assert not flat_q.exists()
    assert not flat_numbered_q.exists()

    included = tmp_path / "included-desk2"
    included.mkdir()
    org.copy_prompt_outputs(out_dir, generic, hitrate, totals, included)
    assert (included / "1_PhaseQ_pack.txt").read_text(encoding="utf-8") == "Q"


def test_parse_hit_rates_filters_hr_under_and_slate(tmp_path: Path):
    data = tmp_path / "data"
    cards = data / "MLB" / "cards" / "mlb_cards_latest.json"
    _write_cards(
        cards,
        [
            {
                "player": "HR Under Guy",
                "market_label": "HR Under Guy - Home Runs",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
            {
                "player": "On Slate",
                "market_label": "On Slate - Doubles",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
            {
                "player": "Off Slate",
                "market_label": "Off Slate - Doubles",
                "team": "CLE",
                "matchup": "CLE @ CIN",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
        ],
    )
    hit_full: dict[str, list] = {"MLB": [], "WNBA": []}
    opts = FilterOptions(allow_matchups=frozenset({"NYY @ PHI"}))
    stats = org.parse_hit_rates(hit_full, data_dirs=[data], filter_opts=opts)
    assert stats["MLB"]["raw_l5_l10_l20"] == 3
    assert len(hit_full["MLB"]) == 1
    assert hit_full["MLB"][0]["player"] == "On Slate"
    assert stats["MLB"]["reject_reasons"]["hr_under_forbidden"] == 1
    assert stats["MLB"]["reject_reasons"]["off_slate_matchup"] == 1


def test_organize_replace_export_no_stale_leftovers(tmp_path: Path):
    packs = tmp_path / "packs"
    pack = packs / "2026-07-26"
    pack.mkdir(parents=True)
    _write_candidates(pack / "candidates.csv", [])
    (pack / "briefing.md").write_text("ZERO CANDIDATES\n", encoding="utf-8")
    (pack / "dossiers").mkdir()
    # no dossier files — empty slate allowlist

    # Stale Drive-like leftovers from a prior afternoon run
    today = tmp_path / "today"
    extracted = today / "extracted_data_2026-07-26_latest"
    extracted.mkdir(parents=True)
    (extracted / "manual_betting_report.md").write_text("STALE REPORT", encoding="utf-8")
    (extracted / "dossiers").mkdir()
    (extracted / "dossiers" / "MLB_old_laa---sf.md").write_text("stale dossier", encoding="utf-8")
    extra = today / "extra_packs_2026-07-26_latest"
    extra.mkdir()
    (extra / "mlb_only.csv").write_text(
        "sport,selection\nMLB,STALE AFTERNOON ROW\n", encoding="utf-8"
    )
    hit_dir = today / "perfect_hit_props_2026-07-26_latest"
    hit_dir.mkdir()
    (hit_dir / "MLB_100_hit_rate.csv").write_text(
        "player,market_label,side,line,team,matchup\nBad,Bad - Home Runs,UNDER,0.5,NYY,CLE @ CIN\n",
        encoding="utf-8",
    )
    stale_desk2 = today / "desk2_prompts_2026-07-26_latest"
    stale_desk2.mkdir()
    (stale_desk2 / "1_PhaseQ_stale.txt").write_text("STALE Q", encoding="utf-8")
    stable_desk2 = today / "desk2_prompts"
    stable_desk2.mkdir()
    (stable_desk2 / "1_PhaseQ_stale.txt").write_text("STALE Q", encoding="utf-8")
    flat_desk2 = today / "Q_chatgpt_stale.txt"
    flat_numbered_desk2 = today / "1_PhaseQ_chatgpt_stale.txt"
    flat_desk2.write_text("STALE Q", encoding="utf-8")
    flat_numbered_desk2.write_text("STALE NUMBERED Q", encoding="utf-8")

    data = tmp_path / "data"
    _write_cards(
        data / "MLB" / "cards" / "mlb_cards_latest.json",
        [
            {
                "player": "Only",
                "market_label": "Only - Doubles",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
            {
                "player": "HRU",
                "market_label": "HRU - Home Runs",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
        ],
    )

    result = org.organize_today_additive(
        pack_search=[packs],
        data_dirs=[data],
        out_dirs=[today],
        run_generate_prompts=False,
    )
    assert result == pack

    # Stale report / dossier wiped by replace
    assert not (extracted / "manual_betting_report.md").exists()
    assert not (extracted / "dossiers" / "MLB_old_laa---sf.md").exists()
    assert (extracted / "briefing.md").read_text(encoding="utf-8").startswith("ZERO")
    assert not stale_desk2.exists()
    assert not stable_desk2.exists()
    assert not flat_desk2.exists()
    assert not flat_numbered_desk2.exists()

    # mlb_only rewritten empty (not stale afternoon row)
    mlb_rows = list(csv.DictReader((extra / "mlb_only.csv").open(encoding="utf-8")))
    assert mlb_rows == []

    # perfect hit rewritten: HR under dropped; with empty slate allowlist,
    # side policy still applies (no allowlist when candidates empty & no dossiers)
    hit_rows = list(csv.DictReader((hit_dir / "MLB_100_hit_rate.csv").open(encoding="utf-8")))
    assert all("Home Runs" not in r["market_label"] or r["side"] != "UNDER" for r in hit_rows)
    # Without slate allowlist, On-slate doubles may keep; HR under must be gone
    assert not any(r["player"] == "HRU" for r in hit_rows)
    assert any(r["player"] == "Only" for r in hit_rows)
