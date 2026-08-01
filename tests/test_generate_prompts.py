from __future__ import annotations

import csv
import importlib.util
import io
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "export-manual-outlier-packs"
    / "scripts"
    / "generate_prompts.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("export_generate_prompts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_for_dir_preserves_totals_name_and_writes_bankroll_prompts(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"

    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        "board,recommended_units_pre_news\nA,3.0\n",
        ("sport,event_id\nMLB,e1\n", "sport,event_id\nMLB,e1\n", ""),
        {"all3": "", "l10_l5": "", "l5_thresh": ""},
        ("league,event_id\nMLB,e1\n", ""),
        ("league,event_id\nMLB,e1\n", ""),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    totals = desk1 / "3_Master_Totals_pack_2099-12-31.txt"
    bankroll = desk1 / "5_Master_Alt_Bankroll_MLB_pack_2099-12-31.txt"
    alt_player = desk1 / "4_Master_Alt_Player_Props_pack_2099-12-31.txt"
    assert totals.exists()
    assert "### Game Totals Data" in totals.read_text(encoding="utf-8")
    assert not list(desk1.glob("3_Master_Totals_MLB_*.txt"))
    assert bankroll.exists()
    assert alt_player.exists()
    assert "### Bankroll Alt Props Data (MLB)" in bankroll.read_text(encoding="utf-8")


def test_csv_has_data_rows_rejects_header_only_bankroll_csv():
    module = _load_module()
    assert not module.csv_has_data_rows("")
    assert not module.csv_has_data_rows("league,event_id\n")
    assert module.csv_has_data_rows("league,event_id\nMLB,e1\n")


def test_master_cards_filter_uses_two_unit_floor():
    module = _load_module()
    candidates = (
        "candidate_id,board,recommended_units_pre_news\n"
        "below,A,1.99\n"
        "two,A,2.0\n"
        "above,A,2.5\n"
        "three,A,3.0\n"
    )

    filtered = module.filter_min_unit_candidates(candidates)
    rows = list(csv.DictReader(io.StringIO(filtered)))

    assert [row["candidate_id"] for row in rows] == ["two", "above", "three"]


def test_master_cards_prompt_labels_two_plus_unit_candidates(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    candidates = (
        "candidate_id,board,recommended_units_pre_news\n"
        "below,A,1.99\n"
        "two,A,2.0\n"
    )

    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        candidates,
        ("", "", ""),
        {"all3": "", "l10_l5": "", "l5_thresh": ""},
        ("", ""),
        ("", ""),
        False,
    )

    master_cards = (
        out_dir
        / "prompts"
        / "Desk1_Automated"
        / "1_Master_Cards_pack_2099-12-31.txt"
    ).read_text(encoding="utf-8")
    assert "### 2+ Unit Candidates Data" in master_cards
    assert "two,A,2.0" in master_cards
    assert "below,A,1.99" not in master_cards


def test_generate_for_dir_omits_header_only_alt_prompts(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        "board,recommended_units_pre_news\nA,3.0\n",
        ("", "", ""),
        {"all3": "", "l10_l5": "", "l5_thresh": ""},
        ("league,event_id\n", "league,event_id\n"),
        ("league,event_id\n", "league,event_id\n"),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    assert not (desk1 / "4_Master_Alt_Player_Props_pack_2099-12-31.txt").exists()
    assert not list(desk1.glob("5_Master_Alt_Bankroll_*_pack_2099-12-31.txt"))


def test_find_all_pack_dirs_is_scoped_to_canonical_root(tmp_path):
    module = _load_module()
    canonical = tmp_path / "canonical" / "packs"
    mirror = tmp_path / "mirror" / "packs"
    (canonical / "2099-12-31").mkdir(parents=True)
    (mirror / "2100-01-01").mkdir(parents=True)

    assert module.find_all_pack_dirs(canonical) == [canonical / "2099-12-31"]
