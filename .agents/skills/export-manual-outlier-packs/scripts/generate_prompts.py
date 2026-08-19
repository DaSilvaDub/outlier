import argparse
import csv
import io
import os
import re
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ARCHIVE_DIRNAME = "archive"
PACK_DATE_RE = re.compile(r"_pack_(\d{4}-\d{2}-\d{2})")
DEFAULT_OUT_DIRS = [
    r"C:\Users\dasil\OneDrive\Desktop\today",
    r"G:\My Drive\today",
]


def filter_candidates_text_for_ai(candidates: str) -> str:
    """Apply the canonical AI-facing candidate projection in standalone runs."""
    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from outlier_scrapers.runner_common import filter_candidates_for_ai

    return filter_candidates_for_ai(candidates.encode("utf-8")).decode("utf-8-sig")


def safe_rmtree(path: Path, max_retries: int = 5, delay: float = 0.5) -> None:
    """Safely remove a directory tree with retries for cloud-sync file locks."""
    if not path.exists():
        return
    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt < max_retries - 1:
                time.sleep(delay)
            else:
                for p in list(path.rglob("*")):
                    if p.is_file():
                        try:
                            p.unlink()
                        except OSError:
                            pass
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass


def clean_stray_files(out_dir: Path) -> None:
    """Remove non-pack files/dirs from out_dir, preserving the archive and prompts folders."""
    for f in out_dir.glob("*"):
        if f.name in (ARCHIVE_DIRNAME, "prompts"):
            continue
        if f.is_file() and not ("_pack_" in f.name and f.suffix == ".txt"):
            try:
                f.unlink()
            except OSError:
                pass
        elif f.is_dir():
            safe_rmtree(f)


ANY_DATE_RE = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")


def archive_old_packs(out_dir: Path, date_str: str) -> None:
    """Move pack files older than current date into out_dir/archive/ and purge data older than 1 day."""
    archive_dir = out_dir / ARCHIVE_DIRNAME
    archive_dir.mkdir(exist_ok=True)

    current_date = date.fromisoformat(date_str)
    one_day_old = (current_date - timedelta(days=1)).isoformat()
    keep_dates = {date_str, one_day_old}

    # 1. Move old files not in keep_dates into archive
    for f in list(out_dir.rglob("*_pack_*.txt")):
        if ARCHIVE_DIRNAME in f.parts:
            continue
        match = PACK_DATE_RE.search(f.name)
        if match and match.group(1) in keep_dates:
            continue
        try:
            target_path = archive_dir / f.name
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(f), str(target_path))
        except OSError:
            pass

    # 2. Purge archive folder: ONLY keep data that is 1 day old (date_str or current_date - 1 day)
    for item in list(archive_dir.iterdir()):
        if item.name.startswith("."):
            continue
        match = ANY_DATE_RE.search(item.name)
        should_delete = False
        if match:
            item_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            if item_date not in keep_dates:
                should_delete = True
        else:
            should_delete = True

        if should_delete:
            try:
                if item.is_file():
                    try:
                        os.chmod(item, 0o777)
                    except OSError:
                        pass
                    item.unlink()
                elif item.is_dir():
                    safe_rmtree(item)
            except OSError:
                pass

    if archive_dir.exists() and not list(archive_dir.iterdir()):
        try:
            archive_dir.rmdir()
        except OSError:
            pass


def filter_min_unit_candidates(candidates_csv_text: str, min_units: float = 2.0) -> str:
    """Filter candidate rows to recommended candidates (recommended_units_pre_news >= min_units, default 2.0, or Board A fallback)."""
    f_in = io.StringIO(candidates_csv_text)
    reader = csv.DictReader(f_in)
    fieldnames = reader.fieldnames or []
    rows = list(reader)

    kept = []
    for r in rows:
        val_str = r.get("recommended_units_pre_news", "").strip()
        try:
            val = float(val_str) if val_str else 0.0
        except ValueError:
            val = 0.0
        if val >= min_units:
            kept.append(r)

    if not kept:
        kept = [r for r in rows if r.get("board") == "A"]

    f_out = io.StringIO()
    writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(kept)
    return f_out.getvalue()


MASTER_CARD_MIN_PRICE = -250
MASTER_CARD_MAX_PRICE = 150  # exclusive: +150-or-longer stays rejected (A.md 5.3)

_MASTER_CARD_TOKEN_RE = re.compile(r"[^A-Z0-9]+")

# GAMELINE market_label spelling varies by day/source ("Moneyline" vs "Money
# Line"; MLB's spread market is sometimes labeled "Run Line" instead of
# "Spread"), so gameline eligibility is matched on a normalized token.
MASTER_CARD_MONEYLINE_TOKEN = "MONEYLINE"
MASTER_CARD_SPREAD_TOKENS = {
    "MLB": frozenset({"SPREAD", "RUNLINE"}),
    "WNBA": frozenset({"SPREAD"}),
}

# Specific market_type codes the pack sometimes assigns directly.
MASTER_CARD_MLB_MARKET_TYPES = frozenset({"SO"})
MASTER_CARD_WNBA_MARKET_TYPES = frozenset({"PTS", "AST", "REB", "PA", "PR", "RA", "PRA"})

# Fallback for rows the pack tags with the generic PLAYER_PROP catch-all
# instead of a specific code: match on the "Player Name - <Prop>" label
# suffix. TEAM_PROP is intentionally excluded — team Points/Steals labels
# are a different market than the player props in the Master Card whitelist.
MASTER_CARD_MLB_PROP_LABEL_SUFFIXES = frozenset({"STRIKEOUTS"})
MASTER_CARD_WNBA_PROP_LABEL_SUFFIXES = frozenset(
    {
        "POINTS",
        "ASSISTS",
        "REBOUNDS",
        "POINTSASSISTS",
        "POINTSREBOUNDS",
        "REBOUNDSASSISTS",
        "POINTSASSISTSREBOUNDS",
    }
)


def _normalize_token(text: str) -> str:
    return _MASTER_CARD_TOKEN_RE.sub("", str(text or "").upper())


def _prop_label_suffix(market_label: str) -> str:
    """ "Player Name - Bases" -> "BASES"; falls back to the whole label if unstructured."""
    label = str(market_label or "")
    if " - " in label:
        label = label.rsplit(" - ", 1)[1]
    return _normalize_token(label)


def _master_card_price_ok(price_str: str) -> bool:
    try:
        price = float(str(price_str).replace("−", "-").strip())
    except (TypeError, ValueError):
        return False
    return MASTER_CARD_MIN_PRICE <= price < MASTER_CARD_MAX_PRICE


def _is_master_card_row(row: dict, sport: str) -> bool:
    """Master Card market whitelist for one sport: gameline ML/Spread plus the sport's prop set."""
    if str(row.get("sport", "")).strip().upper() != sport:
        return False
    market_type = str(row.get("market_type", "")).strip().upper()
    market_label = row.get("market_label", "")

    if market_type == "GAMELINE":
        token = _normalize_token(market_label)
        if token != MASTER_CARD_MONEYLINE_TOKEN and token not in MASTER_CARD_SPREAD_TOKENS.get(
            sport, frozenset()
        ):
            return False
    elif sport == "MLB":
        if market_type not in MASTER_CARD_MLB_MARKET_TYPES and not (
            market_type == "PLAYER_PROP"
            and _prop_label_suffix(market_label) in MASTER_CARD_MLB_PROP_LABEL_SUFFIXES
        ):
            return False
    elif sport == "WNBA":
        if market_type not in MASTER_CARD_WNBA_MARKET_TYPES and not (
            market_type == "PLAYER_PROP"
            and _prop_label_suffix(market_label) in MASTER_CARD_WNBA_PROP_LABEL_SUFFIXES
        ):
            return False
    else:
        return False
    return _master_card_price_ok(row.get("price", ""))


def filter_master_card_candidates(candidates_csv_text: str, sports: tuple[str, ...]) -> str:
    """Filter candidate rows to the Master Card market whitelist / odds window for the given sport(s)."""
    f_in = io.StringIO(candidates_csv_text)
    reader = csv.DictReader(f_in)
    fieldnames = reader.fieldnames or []
    rows = list(reader)

    kept = [r for r in rows if any(_is_master_card_row(r, sport) for sport in sports)]

    f_out = io.StringIO()
    writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(kept)
    return f_out.getvalue()


def csv_has_data_rows(csv_text: str) -> bool:
    """Return true only when CSV text includes at least one data row."""
    if not csv_text.strip():
        return False
    return next(csv.DictReader(io.StringIO(csv_text)), None) is not None


def filter_bankroll_spreads(csv_text: str, *, keep_spreads: bool) -> str:
    """Split mixed bankroll CSV text without changing the source artifact."""
    if not csv_text.strip():
        return ""
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames or []
    if not fieldnames:
        return ""
    rows = []
    for row in reader:
        is_spread = str(row.get("proposition") or "").strip().upper() == "SPREAD"
        if is_spread == keep_spreads:
            rows.append(row)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def build_fallback_spreads(csv_text: str) -> str:
    """Build identity-complete spread CSV from the legacy mixed bankroll artifact."""
    spreads = filter_bankroll_spreads(csv_text, keep_spreads=True)
    if not spreads.strip():
        return ""
    reader = csv.DictReader(io.StringIO(spreads))
    fieldnames = reader.fieldnames or []
    if not fieldnames:
        return ""
    repo_root = Path(__file__).resolve().parents[4]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from outlier_scrapers.alt_spreads import build_alt_spread_rows

    output_fields = [name for name in fieldnames if name not in {"signed_line", "selection"}]
    output_fields.extend(("signed_line", "selection"))
    rows = build_alt_spread_rows(list(reader))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=output_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def load_prompt_template(filename: str) -> str:
    """Read a prompt template from prompts directory, falling back to A.md if missing."""
    repo_root = Path(__file__).resolve().parents[4]
    candidate_paths = [
        repo_root / "prompts" / filename,
        Path(r"C:\Users\dasil\Dev\GitHub\outlier\prompts") / filename,
    ]
    for p in candidate_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    # Safety fallback to Master Cards prompt (A.md)
    for fallback_name in ["A.md"]:
        for p in [
            repo_root / "prompts" / fallback_name,
            Path(r"C:\Users\dasil\Dev\GitHub\outlier\prompts") / fallback_name,
        ]:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
    raise FileNotFoundError(f"prompt template not found: {filename} (A.md fallback missing)")


def safe_write_text(filepath: Path, content: str, retries: int = 10, delay: float = 1.0) -> None:
    import time

    for attempt in range(retries):
        try:
            if filepath.exists():
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return
        except (PermissionError, OSError):
            if attempt == retries - 1:
                print(f"Warning: could not write to {filepath} due to cloud lock.")
                return
            time.sleep(delay)


def generate_for_dir(
    out_dir: Path,
    date_str: str,
    briefing: str,
    candidates: str,
    totals_data: tuple[str, str, str],
    alt_props_data: tuple[str, str],
    bankroll_data: tuple[str, str],
    no_clean: bool,
    spreads_data: tuple[str | None, str | None] | None = None,
    bankroll_parlays_data: tuple[str, str] | None = None,
    ultimate_alt_data: tuple[str, str] | None = None,
    include_sequential_prompts: bool = False,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Archive old files and clean archive folder (only keep 1 day old data)
    archive_old_packs(out_dir, date_str)

    prompts_dir = out_dir / "prompts"

    if not no_clean:
        clean_stray_files(out_dir)
        if prompts_dir.exists():
            safe_rmtree(prompts_dir)

    desk1_dir = prompts_dir / "Desk1_Automated"
    desk2_dir = prompts_dir / "Desk2_Manual"
    desk1_dir.mkdir(parents=True, exist_ok=True)
    if include_sequential_prompts:
        desk2_dir.mkdir(parents=True, exist_ok=True)
    else:
        # A regular run must not leave an older opt-in sequential bundle behind,
        # including when --no-clean preserves the other prompt outputs.
        safe_rmtree(desk2_dir)

    # Master Prompts for Data Types (Cards, Totals, Alt Total, Alt Player Prop,
    # Alt Spread). The dedicated Alt Spread lane intentionally uses prefix 5
    # so existing prompt filenames and consumers are not renumbered.
    # HitRate prompts are intentionally not generated — dropped pending a redesign.
    cards_template = load_prompt_template("A.md")
    cards_2unit = filter_min_unit_candidates(candidates, min_units=2.0)

    # Master Card is split per league (MLB, WNBA) plus a combined variant, each
    # restricted to its own market whitelist and the -250..+150 odds window.
    master_card_variants = [
        ("MLB", ("MLB",)),
        ("WNBA", ("WNBA",)),
        ("Both", ("MLB", "WNBA")),
    ]
    for label, sports in master_card_variants:
        cards_filtered = filter_master_card_candidates(cards_2unit, sports)
        if not csv_has_data_rows(cards_filtered):
            continue
        full_cards_prompt = (
            f"{cards_template}\n\n### Pack Data\n{briefing}\n\n"
            f"### 2+ Unit Candidates Data\n```csv\n{cards_filtered}\n```\n"
        )
        safe_write_text(
            desk1_dir / f"1_Master_Cards_{label}_pack_{date_str}.txt", full_cards_prompt
        )

    totals_template = load_prompt_template("Totals_Analysis.md")
    game_totals, team_totals, _alt_team_totals = totals_data
    full_totals_prompt = (
        f"{totals_template}\n\n### Pack Data\n{briefing}\n\n"
        f"### Game Totals Data\n```csv\n{game_totals}\n```\n\n"
        f"### Team Totals Data\n```csv\n{team_totals}\n```\n"
    )

    safe_write_text(desk1_dir / f"2_Master_Totals_pack_{date_str}.txt", full_totals_prompt)

    # When the unified shadow artifact exists, emit one Ultimate Alt prompt and
    # suppress the three legacy decision prompts. Their CSVs remain available
    # in the pack for compatibility and audit.
    if ultimate_alt_data is not None:
        ultimate_rows, ultimate_parlays = ultimate_alt_data
        if csv_has_data_rows(ultimate_rows):
            ultimate_template = load_prompt_template("Ultimate_Alt_Analysis.md")
            full_ultimate_prompt = (
                f"{ultimate_template}\n\n### Pack Data\n{briefing}\n\n"
                f"### Ultimate Alt Shadow Table\n```csv\n{ultimate_rows}\n```\n\n"
                f"### Ultimate Alt Shadow Parlays\n```csv\n{ultimate_parlays}\n```\n"
            )
            safe_write_text(
                desk1_dir / f"3_Master_Ultimate_Alt_Shadow_pack_{date_str}.txt",
                full_ultimate_prompt,
            )
    else:
        # Legacy prompt lanes remain available for packs created before the
        # unified artifact was introduced.
        _write_legacy_alt_prompts(
            desk1_dir,
            date_str,
            briefing,
            alt_props_data,
            bankroll_data,
            spreads_data,
            bankroll_parlays_data=bankroll_parlays_data,
        )
    desk2_count = 0
    if include_sequential_prompts:
        desk2_count = _write_desk2_prompts(prompts_dir, date_str, briefing, candidates)

    master_count = len(list(desk1_dir.glob("*_Master_*_pack_*.txt")))
    print(
        f"Successfully generated {master_count} Master Prompts and {desk2_count} "
        f"opt-in Desk2 prompt files in {prompts_dir}"
    )


def _write_legacy_alt_prompts(
    desk1_dir: Path,
    date_str: str,
    briefing: str,
    alt_props_data: tuple[str, str],
    bankroll_data: tuple[str, str],
    spreads_data: tuple[str | None, str | None] | None,
    bankroll_parlays_data: tuple[str, str] | None = None,
) -> None:
    # "Alt Total" and "Alt Player Prop" are both bankroll-style plays (low
    # variance, high probability) — the market type differs (game/team total
    # vs. player prop), not the underlying strategy. Both templates already
    # describe themselves as bankroll parlays; only the market-type label
    # differs in the output filename.
    bankroll_template = load_prompt_template("Alt_Bankroll_Props_Analysis.md")
    alt_props_template = load_prompt_template("Alt_Player_Props_Analysis.md")
    alt_player_props, alt_player_props_parlays = alt_props_data
    full_alt_props_prompt = (
        f"{alt_props_template}\n\n### Pack Data\n{briefing}\n\n"
        f"### Alternate Player Props Data\n```csv\n{alt_player_props}\n```\n\n"
        f"### Alternate Player Props Parlays Data\n```csv\n{alt_player_props_parlays}\n```\n"
    )
    mlb_bankroll_all, wnba_bankroll_all = bankroll_data
    mlb_bankroll = filter_bankroll_spreads(mlb_bankroll_all, keep_spreads=False)
    wnba_bankroll = filter_bankroll_spreads(wnba_bankroll_all, keep_spreads=False)
    mlb_parlays, _wnba_parlays = bankroll_parlays_data or ("", "")
    if csv_has_data_rows(mlb_bankroll):
        full_mlb_bankroll = (
            f"{bankroll_template}\n\n### Pack Data\n{briefing}\n\n"
            f"### Bankroll Alt Props Data (MLB)\n```csv\n{mlb_bankroll}\n```\n"
        )
        if csv_has_data_rows(mlb_parlays):
            full_mlb_bankroll += (
                "\n### MLB Cross-Game Alt Over Totals Parlays\n"
                f"```csv\n{mlb_parlays}\n```\n"
            )
        safe_write_text(
            desk1_dir / f"3_Master_Alt_Total_MLB_pack_{date_str}.txt", full_mlb_bankroll
        )
    if csv_has_data_rows(wnba_bankroll):
        full_wnba_bankroll = f"{bankroll_template}\n\n### Pack Data\n{briefing}\n\n### Bankroll Alt Props Data (WNBA)\n```csv\n{wnba_bankroll}\n```\n"
        safe_write_text(
            desk1_dir / f"3_Master_Alt_Total_WNBA_pack_{date_str}.txt", full_wnba_bankroll
        )

    if csv_has_data_rows(alt_player_props):
        safe_write_text(
            desk1_dir / f"4_Master_Alt_Player_Prop_pack_{date_str}.txt",
            full_alt_props_prompt,
        )

    spread_template = load_prompt_template("Alt_Spreads_Analysis.md")
    provided_mlb, provided_wnba = spreads_data or (None, None)
    mlb_spreads = (
        provided_mlb if provided_mlb is not None else build_fallback_spreads(mlb_bankroll_all)
    )
    wnba_spreads = (
        provided_wnba if provided_wnba is not None else build_fallback_spreads(wnba_bankroll_all)
    )
    for league, spread_csv in (("MLB", mlb_spreads), ("WNBA", wnba_spreads)):
        if league == "MLB":
            continue
        if not csv_has_data_rows(spread_csv):
            continue
        full_spread_prompt = (
            f"{spread_template}\n\n### Pack Data\n{briefing}\n\n"
            f"### Alternate Spreads Data ({league})\n```csv\n{spread_csv}\n```\n"
        )
        safe_write_text(
            desk1_dir / f"5_Master_Alt_Spread_{league}_pack_{date_str}.txt",
            full_spread_prompt,
        )


def _write_desk2_prompts(
    prompts_dir: Path,
    date_str: str,
    briefing: str,
    candidates: str,
) -> int:
    desk2_dir = prompts_dir / "Desk2_Manual"
    desk2_order_map = {
        "Q_chatgpt": (1, "PhaseQ"),
        "R_claude": (2, "PhaseR"),
        "W_gemini": (3, "PhaseW"),
        "X_grok": (4, "PhaseX"),
        "S_claude": (5, "PhaseS"),
    }

    repo_root = Path(__file__).resolve().parents[4]
    src_desk2_dir = repo_root / "prompts" / "desk2"

    desk2_count = 0
    if src_desk2_dir.exists():
        for p in src_desk2_dir.glob("*.md"):
            with open(p, "r", encoding="utf-8") as pf:
                p_text = pf.read()
            full_prompt = f"{p_text}\n\n### Pack Data\n{briefing}\n\n### Candidates Data\n```csv\n{candidates}\n```\n"

            stem = p.stem
            if stem in desk2_order_map:
                order, phase_name = desk2_order_map[stem]
                filename = f"{order}_{phase_name}_{stem}_pack_{date_str}.txt"
            else:
                filename = f"99_{stem}_pack_{date_str}.txt"

            out_file = desk2_dir / filename
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(full_prompt)
            desk2_count += 1

    return desk2_count


def find_all_pack_dirs(pack_root: Path | None = None) -> list[Path]:
    """Return dated packs from the canonical checkout only."""
    canonical_root = pack_root or (Path(__file__).resolve().parents[4] / "packs")
    if not canonical_root.exists():
        return []
    return sorted(
        (
            path
            for path in canonical_root.iterdir()
            if path.is_dir() and path.name.replace("-", "").isdigit()
        ),
        key=lambda path: path.name,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate prompt files from Outlier packs")
    parser.add_argument(
        "--out-dir",
        action="append",
        default=None,
        help="Additional output directory for prompts (repeatable). Always includes both the OneDrive Desktop and Google Drive 'today' folders.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clean the output directory before generating",
    )
    parser.add_argument(
        "--include-sequential-prompts",
        action="store_true",
        help="Opt in to generating the ordered Q/R/W/X/S Desk2_Manual prompt bundle",
    )
    args = parser.parse_args()

    out_dirs = [Path(p) for p in dict.fromkeys(DEFAULT_OUT_DIRS + (args.out_dir or []))]
    subdirs = find_all_pack_dirs()
    if not subdirs:
        print("Error: No pack directories found in packs/.")
        return

    latest_pack = subdirs[-1]
    date_str = latest_pack.name

    print(f"Generating prompts for pack date: {date_str}")

    briefing_path = latest_pack / "briefing.md"
    candidates_path = latest_pack / "candidates.csv"

    if not briefing_path.exists() or not candidates_path.exists():
        print(f"Error: Required files (briefing.md, candidates.csv) not found in {latest_pack}")
        return

    with open(briefing_path, "r", encoding="utf-8") as f:
        briefing = f.read()

    with open(candidates_path, "r", encoding="utf-8") as f:
        candidates = f.read()
    candidates = filter_candidates_text_for_ai(candidates)

    briefing = briefing.replace("- Use this pack ONLY. Do not use memory or the web.\n", "")
    briefing = briefing.replace(
        "- If you need info not in the pack, list it under NEEDS — do not guess.\n", ""
    )

    # Read totals data if available
    gt_path = latest_pack / "game_totals.csv"
    tt_path = latest_pack / "team_totals.csv"
    att_path = latest_pack / "alt_team_totals.csv"

    game_totals = gt_path.read_text(encoding="utf-8") if gt_path.exists() else ""
    team_totals = tt_path.read_text(encoding="utf-8") if tt_path.exists() else ""
    alt_team_totals = att_path.read_text(encoding="utf-8") if att_path.exists() else ""
    totals_data = (game_totals, team_totals, alt_team_totals)

    # Read alt player props data if available
    app_path = latest_pack / "alt_player_props.csv"
    app_parlays_path = latest_pack / "alt_player_props_parlays.csv"
    alt_player_props = app_path.read_text(encoding="utf-8") if app_path.exists() else ""
    alt_player_props_parlays = (
        app_parlays_path.read_text(encoding="utf-8") if app_parlays_path.exists() else ""
    )
    alt_props_data = (alt_player_props, alt_player_props_parlays)

    # Read alt bankroll props
    mlb_bp_path = latest_pack / "mlb_alt_bankroll_props.csv"
    wnba_bp_path = latest_pack / "wnba_alt_bankroll_props.csv"
    mlb_bankroll = mlb_bp_path.read_text(encoding="utf-8") if mlb_bp_path.exists() else ""
    wnba_bankroll = wnba_bp_path.read_text(encoding="utf-8") if wnba_bp_path.exists() else ""
    bankroll_data = (mlb_bankroll, wnba_bankroll)
    mlb_bp_parlays_path = latest_pack / "mlb_alt_bankroll_parlays.csv"
    mlb_bankroll_parlays = (
        mlb_bp_parlays_path.read_text(encoding="utf-8") if mlb_bp_parlays_path.exists() else ""
    )
    bankroll_parlays_data = (mlb_bankroll_parlays, "")

    # Read dedicated alternate spreads. The mixed bankroll CSVs remain intact
    # for compatibility; these files provide the explicit spread-only product.
    mlb_spreads_path = latest_pack / "mlb_alt_spreads.csv"
    wnba_spreads_path = latest_pack / "wnba_alt_spreads.csv"
    mlb_spreads = (
        mlb_spreads_path.read_text(encoding="utf-8") if mlb_spreads_path.exists() else None
    )
    wnba_spreads = (
        wnba_spreads_path.read_text(encoding="utf-8") if wnba_spreads_path.exists() else None
    )
    spreads_data = (mlb_spreads, wnba_spreads)

    ultimate_alt_path = latest_pack / "ultimate_alt.csv"
    ultimate_alt_parlays_path = latest_pack / "ultimate_alt_parlays.csv"
    ultimate_alt_data = None
    if ultimate_alt_path.exists():
        ultimate_alt_data = (
            ultimate_alt_path.read_text(encoding="utf-8"),
            ultimate_alt_parlays_path.read_text(encoding="utf-8")
            if ultimate_alt_parlays_path.exists()
            else "",
        )

    for out_dir in out_dirs:
        generate_for_dir(
            out_dir,
            date_str,
            briefing,
            candidates,
            totals_data,
            alt_props_data,
            bankroll_data,
            args.no_clean,
            spreads_data=spreads_data,
            bankroll_parlays_data=bankroll_parlays_data,
            ultimate_alt_data=ultimate_alt_data,
            include_sequential_prompts=args.include_sequential_prompts,
        )


if __name__ == "__main__":
    main()
