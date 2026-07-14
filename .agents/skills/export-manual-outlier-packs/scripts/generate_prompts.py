import argparse
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

ARCHIVE_DIRNAME = "archive"
PACK_DATE_RE = re.compile(r"_pack_(\d{4}-\d{2}-\d{2})")
DEFAULT_OUT_DIRS = [
    r"C:\Users\dasil\OneDrive\Desktop\today",
    r"G:\My Drive\today",
]


def clean_stray_files(out_dir: Path) -> None:
    """Remove non-pack files/dirs from out_dir, preserving the archive folder."""
    for f in out_dir.glob("*"):
        if f.name == ARCHIVE_DIRNAME:
            continue
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


def archive_old_packs(out_dir: Path, keep_dates: set[str]) -> None:
    """Move pack files older than keep_dates into out_dir/archive/."""
    archive_dir = out_dir / ARCHIVE_DIRNAME
    for f in out_dir.glob("*_pack_*.txt"):
        match = PACK_DATE_RE.search(f.name)
        if match and match.group(1) in keep_dates:
            continue
        archive_dir.mkdir(exist_ok=True)
        try:
            shutil.move(str(f), str(archive_dir / f.name))
        except OSError:
            pass


def generate_for_dir(out_dir: Path, date_str: str, briefing: str, candidates: str, no_clean: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    if not no_clean:
        clean_stray_files(out_dir)

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

        file_path = out_dir / f"{model.lower()}_pack_{date_str}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(prompt)

    desk2_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\prompts\desk2")
    desk2_count = 0
    if desk2_dir.exists():
        for p in desk2_dir.glob("*.md"):
            with open(p, "r", encoding="utf-8") as pf:
                p_text = pf.read()
            full_prompt = f"{p_text}\n\n### Pack Data\n{briefing}\n\n### Candidates Data\n```csv\n{candidates}\n```\n"
            out_file = out_dir / f"{p.stem}_pack_{date_str}.txt"
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(full_prompt)
            desk2_count += 1

    current_date = date.fromisoformat(date_str)
    keep_dates = {date_str, (current_date - timedelta(days=1)).isoformat()}
    archive_old_packs(out_dir, keep_dates)

    print(f"Successfully generated {len(models)} generic and {desk2_count} desk2 prompt files in {out_dir} (archived anything older than {min(keep_dates)})")


def main() -> None:
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
    packs_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\packs")

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

    with open(briefing_path, "r", encoding="utf-8") as f:
        briefing = f.read()

    with open(candidates_path, "r", encoding="utf-8") as f:
        candidates = f.read()

    briefing = briefing.replace("- Use this pack ONLY. Do not use memory or the web.\n", "")
    briefing = briefing.replace("- If you need info not in the pack, list it under NEEDS — do not guess.\n", "")

    for out_dir in out_dirs:
        generate_for_dir(out_dir, date_str, briefing, candidates, args.no_clean)


if __name__ == "__main__":
    main()
