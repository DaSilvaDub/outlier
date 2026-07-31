import argparse
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


import csv
import io

def filter_3unit_candidates(candidates_csv_text: str) -> str:
    """Filter candidate rows to 3-unit recommended candidates (recommended_units_pre_news >= 3.0 or Board A fallback)."""
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
        if val >= 3.0:
            kept.append(r)

    if not kept:
        kept = [r for r in rows if r.get("board") == "A"]

    f_out = io.StringIO()
    writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(kept)
    return f_out.getvalue()


def get_hitrate_data_buckets(allow_matchups: frozenset[str] | None = None) -> dict[str, str]:
    """Extract and format 3 specialized hit rate prop datasets across leagues."""
    repo_root = Path(__file__).resolve().parents[4]
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import json
        from filter_perfect_hit_props import FilterOptions, filter_rows

        data_dirs = [repo_root / "data", Path(r"C:\Users\dasil\OneDrive\Documents\outlier\data")]
        b1_all3: dict[str, list[dict]] = {"MLB": [], "WNBA": []}
        b2_l10_l5: dict[str, list[dict]] = {"MLB": [], "WNBA": []}
        b3_l5_thresh: dict[str, list[dict]] = {"MLB": [], "WNBA": []}

        for league in ["MLB", "WNBA"]:
            for d in data_dirs:
                if not d.exists():
                    continue
                cards_file = d / league / "cards" / f"{league.lower()}_cards_latest.json"
                if not cards_file.exists():
                    continue
                try:
                    with open(cards_file, "r", encoding="utf-8") as cf:
                        cards_data = json.load(cf)
                except Exception:
                    continue

                r1, r2, r3 = [], [], []
                for board in ["board_a", "board_b"]:
                    for card in cards_data.get(board) or []:
                        sides = card.get("sides") or {}
                        for _sk, sv in sides.items():
                            hr = sv.get("hit_rates") or {}
                            l5 = hr.get("l5_pct", 0.0) or 0.0
                            l10 = hr.get("l10_pct", 0.0) or 0.0
                            l20 = hr.get("l20_pct", 0.0) or 0.0
                            row = {
                                "player": card.get("player") or "",
                                "market_label": card.get("market_label") or "",
                                "side": sv.get("side") or "",
                                "line": sv.get("line") or "",
                                "team": card.get("team") or "",
                                "matchup": card.get("matchup") or "",
                            }
                            if l5 == 100.0 and l10 == 100.0 and l20 == 100.0:
                                r1.append(row)
                            if l5 == 100.0 and l10 == 100.0:
                                r2.append(row)
                            if l5 == 100.0 and l10 >= 90.0 and l20 >= 70.0:
                                r3.append(row)

                opts = FilterOptions(allow_matchups=allow_matchups)
                k1, _, _ = filter_rows(r1, opts)
                k2, _, _ = filter_rows(r2, opts)
                k3, _, _ = filter_rows(r3, opts)
                b1_all3[league] = k1
                b2_l10_l5[league] = k2
                b3_l5_thresh[league] = k3
                break

        def _format_bucket(b_dict: dict[str, list[dict]], title_suffix: str) -> str:
            output = []
            headers = ["player", "market_label", "side", "line", "team", "matchup"]
            for lg in ["MLB", "WNBA"]:
                output.append(f"#### {lg} {title_suffix}")
                f_out = io.StringIO()
                w = csv.DictWriter(f_out, fieldnames=headers, extrasaction="ignore")
                w.writeheader()
                w.writerows(b_dict.get(lg) or [])
                output.append("```csv\n" + f_out.getvalue() + "```\n")
            return "\n".join(output)

        return {
            "all3": _format_bucket(b1_all3, "100% Hit Rate Props (All 3: Last 5, Last 10, Last 20)"),
            "l10_l5": _format_bucket(b2_l10_l5, "100% Hit Rate Props (Last 10 & Last 5)"),
            "l5_thresh": _format_bucket(b3_l5_thresh, "100% Hit Rate Props (Last 5 = 100%, Last 10 >= 90%, Last 20 >= 70%)"),
        }
    except Exception as exc:
        err_msg = f"Error extracting hit rate props data: {exc}"
        return {"all3": err_msg, "l10_l5": err_msg, "l5_thresh": err_msg}


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
        for p in [repo_root / "prompts" / fallback_name, Path(r"C:\Users\dasil\Dev\GitHub\outlier\prompts") / fallback_name]:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
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
    hitrate_buckets: dict[str, str],
    no_clean: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    current_date = date.fromisoformat(date_str)
    keep_dates = {date_str, (current_date - timedelta(days=1)).isoformat()}

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
    desk2_dir.mkdir(parents=True, exist_ok=True)

    # Master Prompts for Data Types (Cards, HitRate, Totals)
    cards_template = load_prompt_template("A.md")
    cards_3unit = filter_3unit_candidates(candidates)
    full_cards_prompt = f"{cards_template}\n\n### Pack Data\n{briefing}\n\n### 3-Unit Candidates Data\n```csv\n{cards_3unit}\n```\n"

    hitrate_template = load_prompt_template("HitRate_Props_Analysis.md")
    full_hitrate_all3 = f"{hitrate_template}\n\n### Pack Data\n{briefing}\n\n### Hit Rate Props Data\n{hitrate_buckets.get('all3', '')}\n"
    full_hitrate_l10_l5 = f"{hitrate_template}\n\n### Pack Data\n{briefing}\n\n### Hit Rate Props Data\n{hitrate_buckets.get('l10_l5', '')}\n"
    full_hitrate_l5_thresh = f"{hitrate_template}\n\n### Pack Data\n{briefing}\n\n### Hit Rate Props Data\n{hitrate_buckets.get('l5_thresh', '')}\n"

    totals_template = load_prompt_template("Totals_Analysis.md")
    game_totals, team_totals, _alt_team_totals = totals_data
    full_totals_prompt = (
        f"{totals_template}\n\n### Pack Data\n{briefing}\n\n"
        f"### Game Totals Data\n```csv\n{game_totals}\n```\n\n"
        f"### Team Totals Data\n```csv\n{team_totals}\n```\n"
    )

    safe_write_text(desk1_dir / f"1_Master_Cards_pack_{date_str}.txt", full_cards_prompt)
    safe_write_text(desk1_dir / f"2a_Master_HitRate_100_All3_pack_{date_str}.txt", full_hitrate_all3)
    safe_write_text(desk1_dir / f"2b_Master_HitRate_100_L10_L5_pack_{date_str}.txt", full_hitrate_l10_l5)
    safe_write_text(desk1_dir / f"2c_Master_HitRate_100_L5_Min90L10_Min70L20_pack_{date_str}.txt", full_hitrate_l5_thresh)
    safe_write_text(desk1_dir / f"3_Master_Totals_pack_{date_str}.txt", full_totals_prompt)

    # Desk 2 - Manual Sequence (Phase-specific prompts)
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

    print(
        f"Successfully generated 3 Master Prompts (Cards, HitRate, Totals) and {desk2_count} Desk2 prompt files in {out_dir}/prompts (archived anything older than {min(keep_dates)})"
    )


def find_all_pack_dirs() -> list[Path]:
    search_paths = [
        Path(r"C:\Users\dasil\Dev\GitHub\outlier\packs"),
        Path(r"C:\Users\dasil\OneDrive\Documents\outlier\packs"),
    ]
    packs_map: dict[str, Path] = {}
    for p in search_paths:
        if p.exists():
            for d in p.iterdir():
                if d.is_dir() and d.name.replace("-", "").isdigit():
                    packs_map[d.name] = d
    return sorted(packs_map.values(), key=lambda d: d.name)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description="Generate prompt files from Outlier packs")
    parser.add_argument(
        "--out-dir",
        action="append",
        default=None,
        help="Additional output directory for prompts (repeatable). Always includes both the OneDrive Desktop and Google Drive 'today' folders.",
    )
    parser.add_argument("--no-clean", action="store_true", help="Do not clean the output directory before generating")
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
    briefing = briefing.replace("- If you need info not in the pack, list it under NEEDS — do not guess.\n", "")

    # Read totals data if available
    gt_path = latest_pack / "game_totals.csv"
    tt_path = latest_pack / "team_totals.csv"
    att_path = latest_pack / "alt_team_totals.csv"

    game_totals = gt_path.read_text(encoding="utf-8") if gt_path.exists() else ""
    team_totals = tt_path.read_text(encoding="utf-8") if tt_path.exists() else ""
    alt_team_totals = att_path.read_text(encoding="utf-8") if att_path.exists() else ""
    totals_data = (game_totals, team_totals, alt_team_totals)

    # Build slate allowlist from candidates + dossiers to enforce today's slate games only
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    dossiers_dir = latest_pack / "dossiers"
    from filter_perfect_hit_props import allowlist_from_candidates_csv, allowlist_from_dossiers_dir
    allow_set = set()
    allow_set |= allowlist_from_candidates_csv(candidates_path)
    allow_set |= allowlist_from_dossiers_dir(dossiers_dir if dossiers_dir.is_dir() else None)
    allow_matchups = frozenset(allow_set) if allow_set else None

    # Extract HitRate buckets data (filtered to today's active slate matchups)
    hitrate_buckets = get_hitrate_data_buckets(allow_matchups=allow_matchups)

    for out_dir in out_dirs:
        generate_for_dir(out_dir, date_str, briefing, candidates, totals_data, hitrate_buckets, args.no_clean)




if __name__ == "__main__":
    main()
