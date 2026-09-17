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
    unrelated = out_dir / "A_notes.txt"
    flat_q.write_text("STALE Q", encoding="utf-8")
    flat_numbered_q.write_text("STALE NUMBERED Q", encoding="utf-8")
    unrelated.write_text("KEEP ME", encoding="utf-8")
    org.copy_prompt_outputs(out_dir, generic, hitrate, totals)
    assert all(not (path / "1_PhaseQ_pack.txt").exists() for path in (generic, hitrate, totals))
    assert not flat_q.exists()
    assert not flat_numbered_q.exists()
    assert unrelated.read_text(encoding="utf-8") == "KEEP ME"

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
            {
                "player": "DD Under Girl",
                "market_label": "DD Under Girl - Double-Double",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"UNDER": _side("UNDER", 100.0, 100.0, 100.0)},
            },
        ],
    )
    hit_full: dict[str, list] = {"MLB": [], "WNBA": []}
    opts = FilterOptions(allow_matchups=frozenset({"NYY @ PHI"}))
    stats = org.parse_hit_rates(hit_full, data_dirs=[data], filter_opts=opts)
    assert stats["MLB"]["raw_l5_l10_l20"] == 4
    assert len(hit_full["MLB"]) == 1
    assert hit_full["MLB"][0]["player"] == "On Slate"
    assert stats["MLB"]["reject_reasons"]["hr_under_forbidden"] == 1
    assert stats["MLB"]["reject_reasons"]["double_under_forbidden"] == 1
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
            {
                "player": "HA",
                "market_label": "HA - Hits Allowed",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"OVER": _side("OVER", 100.0, 100.0, 100.0)},
            },
            {
                "player": "TB",
                "market_label": "TB - Total Bases",
                "team": "NYY",
                "matchup": "NYY @ PHI",
                "sides": {"OVER": _side("OVER", 100.0, 100.0, 100.0)},
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
    assert not any(r["player"] == "HA" for r in hit_rows)
    assert any(r["player"] == "Only" for r in hit_rows)
    assert any(r["player"] == "TB" for r in hit_rows)


def test_safe_copy_reports_a_copy_it_had_to_give_up_on(tmp_path, monkeypatch, capsys):
    """A locked file must not abort the export, but it must not vanish silently.

    ``safe_copy`` exhausts its retries and then swallows the failure so one
    cloud-sync lock cannot take the whole run down. Without a message the run
    reports success while the Desktop / Drive ``today`` folder is short a file.
    """
    src = tmp_path / "briefing.md"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "out" / "briefing.md"

    def _always_locked(*_args, **_kwargs):
        raise OSError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr(org.shutil, "copy2", _always_locked)
    monkeypatch.setattr(org.shutil, "copyfile", _always_locked)
    monkeypatch.setattr(org.time, "sleep", lambda _seconds: None)

    org.safe_copy(src, dst, retries=2, delay=0)

    assert not dst.exists()
    err = capsys.readouterr().err
    assert "briefing.md" in err
    assert "WARNING" in err


def test_a_loose_prompt_survives_a_copy_that_gave_up(tmp_path, monkeypatch, capsys):
    """Filing a loose prompt into its bucket is a move, so a failed copy keeps the source.

    ``safe_copy`` is non-fatal on purpose -- one cloud-sync lock must not abort
    the export -- so it can return having copied nothing. Removing the loose
    file regardless leaves the prompt in neither place: not in the bucket the
    copy never reached, and no longer in ``today`` where it was written.
    """
    out_dir = tmp_path / "today"
    out_dir.mkdir()
    loose = out_dir / "1_Master_Cards.txt"
    loose.write_text("CARDS", encoding="utf-8")
    generic = tmp_path / "generic"
    hitrate = tmp_path / "hitrate"
    totals = tmp_path / "totals"

    def _always_locked(*_args, **_kwargs):
        raise OSError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr(org.shutil, "copy2", _always_locked)
    monkeypatch.setattr(org.shutil, "copyfile", _always_locked)
    monkeypatch.setattr(org.time, "sleep", lambda _seconds: None)

    org.copy_prompt_outputs(out_dir, generic, hitrate, totals)

    assert loose.exists(), "the only remaining copy of the prompt was deleted"
    assert loose.read_text(encoding="utf-8") == "CARDS"
    assert "WARNING" in capsys.readouterr().err


def test_a_loose_prompt_is_still_moved_when_the_copy_lands(tmp_path):
    """The failure path must not come at the cost of the move itself."""
    out_dir = tmp_path / "today"
    out_dir.mkdir()
    loose = out_dir / "1_Master_Cards.txt"
    loose.write_text("CARDS", encoding="utf-8")
    generic = tmp_path / "generic"
    hitrate = tmp_path / "hitrate"
    totals = tmp_path / "totals"

    org.copy_prompt_outputs(out_dir, generic, hitrate, totals)

    assert not loose.exists()
    assert (generic / "1_Master_Cards.txt").read_text(encoding="utf-8") == "CARDS"


def test_a_copy_that_dies_partway_does_not_truncate_the_previous_export(
    tmp_path, monkeypatch, capsys
):
    """shutil's copies truncate the destination before writing.

    Copying straight onto the destination means a lock or a disconnect partway
    through leaves a truncated export sitting in the ``today`` folder. That is
    worse than the missing file ``safe_copy`` already reports, because nothing
    flags it: the file is there, it is just short. Staging the bytes into a
    sibling and moving them into place keeps the previous export whole until
    the new one is complete.
    """
    src = tmp_path / "briefing.md"
    src.write_text("NEW COMPLETE PAYLOAD" * 50, encoding="utf-8")
    dst = tmp_path / "out" / "briefing.md"
    dst.parent.mkdir()
    dst.write_text("PREVIOUS COMPLETE EXPORT", encoding="utf-8")

    def _dies_partway(_src, destination, **_kwargs):
        Path(destination).write_text("TRUNC", encoding="utf-8")
        raise OSError(32, "The process cannot access the file because it is being used")

    monkeypatch.setattr(org.shutil, "copy2", _dies_partway)
    monkeypatch.setattr(org.shutil, "copyfile", _dies_partway)
    monkeypatch.setattr(org.time, "sleep", lambda _seconds: None)

    assert org.safe_copy(src, dst, retries=2, delay=0) is False
    assert dst.read_text(encoding="utf-8") == "PREVIOUS COMPLETE EXPORT"
    # The staging file must not be left sitting in the export folder either.
    assert [p.name for p in dst.parent.iterdir()] == ["briefing.md"]
    assert "WARNING" in capsys.readouterr().err
