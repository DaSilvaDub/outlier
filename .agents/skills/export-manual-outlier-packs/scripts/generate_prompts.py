import argparse
import shutil
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Generate prompt files from Outlier packs")
    parser.add_argument("--out-dir", default=r"C:\Users\dasil\OneDrive\Desktop\today", help="Output directory for prompts")
    parser.add_argument("--no-clean", action="store_true", help="Do not clean the output directory before generating")
    args = parser.parse_args()

    desktop = Path(args.out_dir)
    packs_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\packs")

    # Ensure target directory exists
    desktop.mkdir(parents=True, exist_ok=True)

    if not args.no_clean:
        # Cleanup target directory, keeping old pack files
        for f in desktop.glob("*"):
            if f.is_file() and not ("_pack_" in f.name and f.suffix == ".txt"):
                try:
                    f.unlink()
                except OSError:
                    pass
            elif f.is_dir():
                try:
                    shutil.rmtree(f)
                except OSError:
                    pass

    # Find the most recent date directory in packs/
    subdirs = [d for d in packs_dir.iterdir() if d.is_dir() and d.name.replace("-", "").isdigit()]
    if not subdirs:
        print("Error: No pack directories found in packs/.")
        return

    latest_pack = max(subdirs, key=lambda d: d.name)
    date_str = latest_pack.name

    print(f"Generating prompts for pack date: {date_str}")

    briefing_path = latest_pack / "briefing.md"
    candidates_path = latest_pack / "candidates.csv"

    if not briefing_path.exists() or not candidates_path.exists():
        print(f"Error: Required files (briefing.md, candidates.csv) not found in {latest_pack}")
        return

    # Read briefing and candidates
    with open(briefing_path, "r", encoding="utf-8") as f:
        briefing = f.read()

    with open(candidates_path, "r", encoding="utf-8") as f:
        candidates = f.read()

    # Modifications to briefing
    briefing = briefing.replace("- Use this pack ONLY. Do not use memory or the web.\n", "")
    briefing = briefing.replace("- If you need info not in the pack, list it under NEEDS — do not guess.\n", "")

    models = ["Claude", "Grok", "Copilot", "Gemini", "ChatGPT"]

    for model in models:
        prompt = f"""Hello {model}, please analyze the following betting data and generate a final betting report.
CRITICAL INSTRUCTIONS FOR YOU:
1. You MUST USE THE INTERNET / web search for any missing information you need (like injuries, news, or context) that you do not have to make your analysis. Do not just say you need it, go search for it.
2. I WANT NO HIGH VARIANCE PROPS in the final recommendations. Exclude any high variance props entirely.

{briefing}

### Candidates Data
```csv
{candidates}
```
"""
        
        file_path = desktop / f"{model.lower()}_pack_{date_str}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(prompt)

    print(f"Successfully generated {len(models)} prompt files in {desktop}")

if __name__ == "__main__":
    main()
