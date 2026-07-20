import json
import csv
import shutil
import os
import subprocess
from pathlib import Path

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

def organize_today_additive():
    today_str = "2026-07-20"
    suffix = "_latest"
    packs_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\packs")
    latest_pack = packs_dir / today_str
    
    out_dirs = [
        Path(r"C:\Users\dasil\OneDrive\Desktop\today"),
        Path(r"G:\My Drive\today")
    ]
    
    # 1. Gather 100% hit rate props
    hit_100_props = { "MLB": [], "WNBA": [] }
    hit_100_l5_l10_props = { "MLB": [], "WNBA": [] }
    parse_hit_rates(hit_100_props, hit_100_l5_l10_props)
    
    # Run the generate_prompts script first so it creates the prompt files in today folders
    gen_script = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\.agents\skills\export-manual-outlier-packs\scripts\generate_prompts.py")
    subprocess.run(["python", str(gen_script), "--no-clean"], check=True)
    
    for out_dir in out_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # WE DO NOT ARCHIVE EXISTING FILES. They stay exactly where they are.
                    
        # Organize new folders for this run (using _latest suffix)
        generic_prompts = out_dir / f"generic_prompts_{today_str}{suffix}"
        desk2_prompts = out_dir / f"desk2_prompts_{today_str}{suffix}"
        pipeline_data = out_dir / f"extracted_data_{today_str}{suffix}"
        extra_packs = out_dir / f"extra_packs_{today_str}{suffix}"
        hit_props_dir = out_dir / f"perfect_hit_props_{today_str}{suffix}"
        hit_l5_l10_props_dir = out_dir / f"perfect_hit_l10_l5_props_{today_str}{suffix}"
        
        for d in [generic_prompts, desk2_prompts, pipeline_data, extra_packs, hit_props_dir, hit_l5_l10_props_dir]:
            d.mkdir(exist_ok=True)
            
        # Move prompt files to proper folders
        for item in out_dir.glob("*.txt"):
            if item.name.startswith(("claude", "grok", "copilot", "gemini", "chatgpt")):
                shutil.move(str(item), str(generic_prompts / item.name))
            elif item.name[0].isupper() and item.name[1] == '_':
                shutil.move(str(item), str(desk2_prompts / item.name))
                    
        # Copy pipeline data
        if latest_pack.exists():
            for item in latest_pack.iterdir():
                try:
                    if item.is_dir():
                        shutil.copytree(str(item), str(pipeline_data / item.name), dirs_exist_ok=True)
                    else:
                        shutil.copy2(str(item), str(pipeline_data / item.name))
                except Exception as e:
                    print(f"Failed to copy {item.name}: {e}")
            
            # Generate extra packs from the candidate data
            candidates_csv = latest_pack / "data_analysis" / "candidates.csv"
            if not candidates_csv.exists():
                candidates_csv = latest_pack / "candidates.csv"
            
            generate_specific_packs(candidates_csv, extra_packs)
            
            # Also copy game_totals.csv and team_totals.csv if they exist
            for t_csv in ["game_totals.csv", "team_totals.csv", "alt_team_totals.csv", "opportunities.csv"]:
                src = latest_pack / t_csv
                if not src.exists():
                    src = latest_pack / "data_analysis" / t_csv
                    
                if src.exists():
                    shutil.copy2(str(src), str(extra_packs / t_csv))
        
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
