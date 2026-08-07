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
        ("league,event_id\nMLB,e1\n", ""),
        ("league,event_id\nMLB,e1\n", ""),
        False,
        spreads_data=(
            "league,event_id,proposition,selection,signed_line\n"
            "MLB,s1,SPREAD,HOME +3.5,+3.5\n",
            "",
        ),
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    totals = desk1 / "2_Master_Totals_pack_2099-12-31.txt"
    alt_total = desk1 / "3_Master_Alt_Total_MLB_pack_2099-12-31.txt"
    alt_player = desk1 / "4_Master_Alt_Player_Prop_pack_2099-12-31.txt"
    alt_spread = desk1 / "5_Master_Alt_Spread_MLB_pack_2099-12-31.txt"
    assert totals.exists()
    assert "### Game Totals Data" in totals.read_text(encoding="utf-8")
    assert not list(desk1.glob("2_Master_Totals_MLB_*.txt"))
    assert alt_total.exists()
    assert alt_player.exists()
    assert alt_spread.exists()
    assert "### Bankroll Alt Props Data (MLB)" in alt_total.read_text(encoding="utf-8")
    spread_text = alt_spread.read_text(encoding="utf-8")
    assert "### Alternate Spreads Data (MLB)" in spread_text
    assert "HOME +3.5,+3.5" in spread_text


def test_csv_has_data_rows_rejects_header_only_bankroll_csv():
    module = _load_module()
    assert not module.csv_has_data_rows("")
    assert not module.csv_has_data_rows("league,event_id\n")
    assert module.csv_has_data_rows("league,event_id\nMLB,e1\n")


def test_filter_bankroll_spreads_splits_prompt_payload_without_mutating_source():
    module = _load_module()
    source = (
        "league,event_id,proposition\n"
        "MLB,total,TOTAL\n"
        "MLB,spread,SPREAD\n"
        "MLB,moneyline,MONEYLINE\n"
    )

    totals = list(
        csv.DictReader(io.StringIO(module.filter_bankroll_spreads(source, keep_spreads=False)))
    )
    spreads = list(
        csv.DictReader(io.StringIO(module.filter_bankroll_spreads(source, keep_spreads=True)))
    )

    assert [row["event_id"] for row in totals] == ["total", "moneyline"]
    assert [row["event_id"] for row in spreads] == ["spread"]
    assert "spread,SPREAD" in source


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


def _master_card_row(sport, market_type, market_label, price, units="3.0"):
    return f"{sport},A,{units},{market_type},{market_label},{price}"


MASTER_CARD_HEADER = "sport,board,recommended_units_pre_news,market_type,market_label,price"


def test_master_cards_prompt_splits_mlb_wnba_and_both(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    candidates = "\n".join(
        [
            MASTER_CARD_HEADER,
            _master_card_row("MLB", "SO", "", "-150"),
            _master_card_row("MLB", "TB", "", "-200"),
            _master_card_row("WNBA", "PTS", "", "-120"),
            _master_card_row("WNBA", "GAMELINE", "Moneyline", "-210"),
            "",
        ]
    )

    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        candidates,
        ("", "", ""),
        ("", ""),
        ("", ""),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    mlb = (desk1 / "1_Master_Cards_MLB_pack_2099-12-31.txt").read_text(encoding="utf-8")
    wnba = (desk1 / "1_Master_Cards_WNBA_pack_2099-12-31.txt").read_text(encoding="utf-8")
    both = (desk1 / "1_Master_Cards_Both_pack_2099-12-31.txt").read_text(encoding="utf-8")

    assert "MLB,A,3.0,SO" in mlb and "MLB,A,3.0,TB" in mlb
    assert "WNBA" not in mlb.split("### 2+ Unit Candidates Data")[1]
    assert "WNBA,A,3.0,PTS" in wnba and "Moneyline" in wnba
    assert "MLB" not in wnba.split("### 2+ Unit Candidates Data")[1]
    assert "SO" in both and "PTS" in both


def test_master_cards_prompt_omits_a_variant_with_no_qualifying_rows(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    candidates = "\n".join(
        [
            MASTER_CARD_HEADER,
            _master_card_row("MLB", "SO", "", "-150"),
            "",
        ]
    )

    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        candidates,
        ("", "", ""),
        ("", ""),
        ("", ""),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    assert (desk1 / "1_Master_Cards_MLB_pack_2099-12-31.txt").exists()
    assert not (desk1 / "1_Master_Cards_WNBA_pack_2099-12-31.txt").exists()


def test_filter_master_card_candidates_market_whitelist_and_odds_window():
    module = _load_module()
    candidates = "\n".join(
        [
            MASTER_CARD_HEADER,
            _master_card_row("MLB", "SO", "", "-150"),  # eligible
            _master_card_row("MLB", "TB", "", "-200"),  # eligible
            _master_card_row("MLB", "H", "", "-150"),  # not on MLB whitelist
            _master_card_row("MLB", "SO", "", "-300"),  # too far favorite (< -250)
            _master_card_row("MLB", "SO", "", "150"),  # +150-or-longer rejected
            _master_card_row("MLB", "GAMELINE", "Total O/U", "-110"),  # not ML/Spread
            "",
        ]
    )

    filtered = module.filter_master_card_candidates(candidates, ("MLB",))
    rows = list(csv.DictReader(io.StringIO(filtered)))

    assert [(r["market_type"], r["price"]) for r in rows] == [
        ("SO", "-150"),
        ("TB", "-200"),
    ]


def test_filter_master_card_candidates_matches_player_prop_catchall_and_label_variants():
    """The pack sometimes tags props with the generic PLAYER_PROP market_type instead of
    SO/TB/PTS/etc, and spells gameline labels inconsistently (Money Line vs Moneyline,
    MLB spread as Run Line). The whitelist must still catch these real-world variants."""
    module = _load_module()
    candidates = "\n".join(
        [
            MASTER_CARD_HEADER,
            _master_card_row("MLB", "PLAYER_PROP", "Riley Greene - Bases", "145"),  # TB fallback
            _master_card_row("MLB", "PLAYER_PROP", "Framber Valdez - Strikeouts", "-120"),  # SO fallback
            _master_card_row("MLB", "GAMELINE", "Run Line", "-110"),  # MLB spread synonym
            _master_card_row("WNBA", "GAMELINE", "Money Line", "-130"),  # spacing variant
            _master_card_row("WNBA", "PLAYER_PROP", "Kelsey Plum - Assists", "-143"),  # AST fallback
            _master_card_row("WNBA", "TEAM_PROP", "Points", "-115"),  # team prop, must NOT match
            _master_card_row("MLB", "PLAYER_PROP", "Fernando Tatis Jr. - Hits", "-200"),  # off-whitelist prop
            "",
        ]
    )

    mlb = list(csv.DictReader(io.StringIO(module.filter_master_card_candidates(candidates, ("MLB",)))))
    wnba = list(csv.DictReader(io.StringIO(module.filter_master_card_candidates(candidates, ("WNBA",)))))

    assert [r["market_label"] for r in mlb] == ["Riley Greene - Bases", "Framber Valdez - Strikeouts", "Run Line"]
    assert [r["market_label"] for r in wnba] == ["Money Line", "Kelsey Plum - Assists"]


def test_generate_for_dir_omits_header_only_alt_prompts(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        "board,recommended_units_pre_news\nA,3.0\n",
        ("", "", ""),
        ("league,event_id\n", "league,event_id\n"),
        ("league,event_id\n", "league,event_id\n"),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    assert not (desk1 / "4_Master_Alt_Player_Prop_pack_2099-12-31.txt").exists()
    assert not list(desk1.glob("3_Master_Alt_Total_*_pack_2099-12-31.txt"))
    assert not list(desk1.glob("5_Master_Alt_Spread_*_pack_2099-12-31.txt"))


def test_generate_for_dir_removes_spreads_from_alt_total_and_uses_fallback_lane(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "today"
    mixed = (
        "league,event_id,proposition,selection,signed_line\n"
        "MLB,total,TOTAL,,\n"
        "MLB,spread,SPREAD,HOME +3.5,+3.5\n"
    )

    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        "board,recommended_units_pre_news\nA,3.0\n",
        ("", "", ""),
        ("", ""),
        (mixed, ""),
        False,
        spreads_data=(None, ""),
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    alt_total = (desk1 / "3_Master_Alt_Total_MLB_pack_2099-12-31.txt").read_text(
        encoding="utf-8"
    )
    alt_spread = (desk1 / "5_Master_Alt_Spread_MLB_pack_2099-12-31.txt").read_text(
        encoding="utf-8"
    )
    assert "total,TOTAL" in alt_total
    assert "spread,SPREAD" not in alt_total
    assert "HOME +3.5,+3.5" in alt_spread


def test_generate_for_dir_no_longer_writes_hitrate_prompts(tmp_path):
    """HitRate prompts were intentionally dropped pending a redesign."""
    module = _load_module()
    assert not hasattr(module, "get_hitrate_data_buckets")

    out_dir = tmp_path / "today"
    module.generate_for_dir(
        out_dir,
        "2099-12-31",
        "briefing",
        "board,recommended_units_pre_news\nA,3.0\n",
        ("", "", ""),
        ("", ""),
        ("", ""),
        False,
    )

    desk1 = out_dir / "prompts" / "Desk1_Automated"
    assert not list(desk1.glob("*Master_HitRate*"))


def test_find_all_pack_dirs_is_scoped_to_canonical_root(tmp_path):
    module = _load_module()
    canonical = tmp_path / "canonical" / "packs"
    mirror = tmp_path / "mirror" / "packs"
    (canonical / "2099-12-31").mkdir(parents=True)
    (mirror / "2100-01-01").mkdir(parents=True)

    assert module.find_all_pack_dirs(canonical) == [canonical / "2099-12-31"]
