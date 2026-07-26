import json
import csv
import shutil
import os
import re
import time
import subprocess
from datetime import date, timedelta
from pathlib import Path


def safe_copy(src: Path, dst: Path, retries: int = 5, delay: float = 0.5) -> None:
    """Copy file with retries to handle transient cloud sync locks ([WinError 32])."""
    for attempt in range(retries):
        try:
            shutil.copy2(str(src), str(dst))
            return
        except OSError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                try:
                    shutil.copyfile(str(src), str(dst))
                except OSError:
                    pass


def purge_old_archive_items(archive_dir: Path, keep_dates: set[str]) -> None:
    """Purge any files/directories in archive that are older than 1 day (not in keep_dates)."""
    if not archive_dir.exists():
        return

    pat = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")
    for item in list(archive_dir.iterdir()):
        if item.name.startswith("."):
            continue
        match = pat.search(item.name)
        should_del = True
        if match:
            item_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            if item_date in keep_dates:
                should_del = False

        if should_del:
            try:
                if item.is_file():
                    try:
                        os.chmod(item, 0o777)
                    except OSError:
                        pass
                    item.unlink()
                elif item.is_dir():
                    subprocess.run(
                        ["powershell", "-Command", f'Remove-Item -LiteralPath "{item}" -Recurse -Force -ErrorAction SilentlyContinue'],
                        check=False,
                    )
            except OSError:
                pass


def parse_hit_rates(hit_100_props, hit_100_l5_l10_props):
    data_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\data")
    leagues = ["MLB", "WNBA"]
    
    for league in leagues:
        cards_file = data_dir / league / "cards" / f"{league.lower()}_cards_latest.json"
        if not cards_file.exists():
            continue
        try:
            with open(cards_file, 'r', encoding='utf-8') as f:
                cards_data = json.load(f)
            
            for board in ['board_a', 'board_b']:
                if board in cards_data:
                    for card in cards_data[board]:
                        sides = card.get('sides', {})
                        for side_k, side_v in sides.items():
                            hit_rates = side_v.get('hit_rates', {})
                            l5 = hit_rates.get('l5_pct')
                            l10 = hit_rates.get('l10_pct')
                            l20 = hit_rates.get('l20_pct')
                            
                            row = {
                                "player": card.get("player") or "",
                                "market_label": card.get("market_label") or "",
                                "side": side_v.get("side") or "",
                                "line": side_v.get("line") or "",
                                "team": card.get("team") or "",
                                "matchup": card.get("matchup") or ""
                            }

                            if l5 == 100.0 and l10 == 100.0:
                                hit_100_l5_l10_props[league].append(row)
                                if l20 == 100.0:
                                    hit_100_props[league].append(row)
        except Exception as e:
            print(f"Error parsing {cards_file}: {e}")


def generate_specific_packs(candidates_path: Path, output_dir: Path):
    if not candidates_path.exists():
        return
    with open(candidates_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    mlb_rows = [r for r in rows if r.get('sport') == 'MLB']
    wnba_rows = [r for r in rows if r.get('sport') == 'WNBA']
    
    header = reader.fieldnames
    
    for name, subset in [("mlb_only.csv", mlb_rows), ("wnba_only.csv", wnba_rows)]:
        if subset:
            with open(output_dir / name, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()
                writer.writerows(subset)


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


def organize_today_additive():
    subdirs = find_all_pack_dirs()
    if not subdirs:
        print("Error: No pack directories found in packs/.")
        return

    latest_pack = subdirs[-1]
    today_str = latest_pack.name
    suffix = "_latest"

    out_dirs = [
        Path(r"C:\Users\dasil\OneDrive\Desktop\today"),
        Path(r"G:\My Drive\today")
    ]

    current_date = date.fromisoformat(today_str)
    one_day_old = (current_date - timedelta(days=1)).isoformat()
    keep_dates = {today_str, one_day_old}

    # 1. Gather 100% hit rate props
    hit_100_props = {"MLB": [], "WNBA": []}
    hit_100_l5_l10_props = {"MLB": [], "WNBA": []}
    parse_hit_rates(hit_100_props, hit_100_l5_l10_props)

    # Run the generate_prompts script first so it creates the prompt files in today folders
    repo_root = Path(__file__).resolve().parents[1]
    gen_script = repo_root / ".agents" / "skills" / "export-manual-outlier-packs" / "scripts" / "generate_prompts.py"
    if not gen_script.exists():
        gen_script = Path(r"C:\Users\dasil\Dev\GitHub\outlier\.agents\skills\export-manual-outlier-packs\scripts\generate_prompts.py")
    subprocess.run(["python", str(gen_script), "--no-clean"], check=True)

    for out_dir in out_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)

        # Purge archive folder so it ONLY keeps 1 day old data
        purge_old_archive_items(out_dir / "archive", keep_dates)

        # Organize new folders for this run (using _latest suffix)
        generic_prompts = out_dir / f"generic_prompts_{today_str}{suffix}"
        hitrate_prompts = out_dir / f"hitrate_prompts_{today_str}{suffix}"
        totals_prompts = out_dir / f"totals_prompts_{today_str}{suffix}"
        desk2_prompts = out_dir / f"desk2_prompts_{today_str}{suffix}"
        pipeline_data = out_dir / f"extracted_data_{today_str}{suffix}"
        extra_packs = out_dir / f"extra_packs_{today_str}{suffix}"
        hit_props_dir = out_dir / f"perfect_hit_props_{today_str}{suffix}"
        hit_l5_l10_props_dir = out_dir / f"perfect_hit_l10_l5_props_{today_str}{suffix}"

        for d in [generic_prompts, hitrate_prompts, totals_prompts, desk2_prompts, pipeline_data, extra_packs, hit_props_dir, hit_l5_l10_props_dir]:
            d.mkdir(exist_ok=True)

        # Copy prompt files from prompts/ subdirectories into organized output folders
        prompts_root = out_dir / "prompts"
        if prompts_root.exists():
            desk1_src = prompts_root / "Desk1_Automated"
            if desk1_src.exists():
                for item in desk1_src.glob("*.txt"):
                    if "Cards" in item.name:
                        safe_copy(item, generic_prompts / item.name)
                    elif "HitRate" in item.name:
                        safe_copy(item, hitrate_prompts / item.name)
                    elif "Totals" in item.name:
                        safe_copy(item, totals_prompts / item.name)
                    else:
                        safe_copy(item, generic_prompts / item.name)

            desk2_src = prompts_root / "Desk2_Manual"
            if desk2_src.exists():
                for item in desk2_src.glob("*.txt"):
                    safe_copy(item, desk2_prompts / item.name)

            # Also check root prompts folder for any loose .txt files
            for item in out_dir.glob("*.txt"):
                if "Cards" in item.name:
                    safe_copy(item, generic_prompts / item.name)
                    try: item.unlink()
                    except OSError: pass
                elif "HitRate" in item.name:
                    safe_copy(item, hitrate_prompts / item.name)
                    try: item.unlink()
                    except OSError: pass
                elif "Totals" in item.name:
                    safe_copy(item, totals_prompts / item.name)
                    try: item.unlink()
                    except OSError: pass
                elif item.name.startswith(("claude", "grok", "copilot", "gemini", "chatgpt", "1_Master", "generic")):
                    safe_copy(item, generic_prompts / item.name)
                    try: item.unlink()
                    except OSError: pass
                elif item.name[0].isupper() and item.name[1] == '_':
                    safe_copy(item, desk2_prompts / item.name)
                    try: item.unlink()
                    except OSError: pass

        # Copy pipeline data into extracted_data
        if latest_pack.exists():
            for item in latest_pack.iterdir():
                try:
                    if item.is_dir():
                        shutil.copytree(str(item), str(pipeline_data / item.name), dirs_exist_ok=True)
                    else:
                        safe_copy(item, pipeline_data / item.name)
                except Exception as e:
                    print(f"Failed to copy {item.name}: {e}")

            # Generate extra packs from the candidate data
            candidates_csv = latest_pack / "data_analysis" / "candidates.csv"
            if not candidates_csv.exists():
                candidates_csv = latest_pack / "candidates.csv"

            generate_specific_packs(candidates_csv, extra_packs)

            # Also copy game_totals.csv, team_totals.csv, alt_team_totals.csv, opportunities.csv to extra_packs & totals_prompts
            for t_csv in ["game_totals.csv", "team_totals.csv", "alt_team_totals.csv", "opportunities.csv"]:
                src = latest_pack / t_csv
                if not src.exists():
                    src = latest_pack / "data_analysis" / t_csv

                if src.exists():
                    safe_copy(src, extra_packs / t_csv)
                    if t_csv in ["game_totals.csv", "team_totals.csv", "alt_team_totals.csv"]:
                        safe_copy(src, totals_prompts / t_csv)

        # Write hit rate props (L5/L10/L20)
        for league, props in hit_100_props.items():
            if props:
                hit_file = hit_props_dir / f"{league}_100_hit_rate.csv"
                with open(hit_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=["player", "market_label", "side", "line", "team", "matchup"])
                    writer.writeheader()
                    writer.writerows(props)

        # Write hit rate props (L5/L10)
        for league, props in hit_100_l5_l10_props.items():
            if props:
                hit_file = hit_l5_l10_props_dir / f"{league}_100_hit_l10_l5.csv"
                with open(hit_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=["player", "market_label", "side", "line", "team", "matchup"])
                    writer.writeheader()
                    writer.writerows(props)

if __name__ == "__main__":
    organize_today_additive()
