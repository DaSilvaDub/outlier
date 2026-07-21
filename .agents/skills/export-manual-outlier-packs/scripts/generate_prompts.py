import argparse
import re
import shutil
import sys
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
            try:
                shutil.rmtree(f)
            except OSError:
                pass


def archive_old_packs(out_dir: Path, keep_dates: set[str]) -> None:
    """Move pack files older than keep_dates into out_dir/archive/."""
    archive_dir = out_dir / ARCHIVE_DIRNAME
    for f in out_dir.rglob("*_pack_*.txt"):
        if ARCHIVE_DIRNAME in f.parts:
            continue
        match = PACK_DATE_RE.search(f.name)
        if match and match.group(1) in keep_dates:
            continue
        archive_dir.mkdir(exist_ok=True)
        try:
            shutil.move(str(f), str(archive_dir / f.name))
        except OSError:
            pass


def get_desk1_prompt_template() -> str:
    """Read the unified Desk 1 prompt from the repo."""
    prompt_path = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\prompts\A.md")
    if prompt_path.exists():
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "Error: Could not find unified Desk 1 prompt (A.md)."


def generate_for_dir(out_dir: Path, date_str: str, briefing: str, candidates: str, no_clean: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    current_date = date.fromisoformat(date_str)
    keep_dates = {date_str, (current_date - timedelta(days=1)).isoformat()}
    
    # Archive old files before cleaning the prompts directory
    archive_old_packs(out_dir, keep_dates)

    prompts_dir = out_dir / "prompts"

    if not no_clean:
        clean_stray_files(out_dir)
        if prompts_dir.exists():
            shutil.rmtree(prompts_dir)

    desk1_dir = prompts_dir / "Desk1_Automated"
    desk2_dir = prompts_dir / "Desk2_Manual"
    desk1_dir.mkdir(parents=True, exist_ok=True)
    desk2_dir.mkdir(parents=True, exist_ok=True)

    # Desk 1 - Automated Models
    desk1_models = [
        (1, "Claude"),
        (2, "Grok"),
        (3, "Copilot"),
        (4, "Gemini"),
        (5, "ChatGPT")
    ]
    
    desk1_base_prompt = get_desk1_prompt_template()
    full_desk1_prompt = f"{desk1_base_prompt}\n\n### Pack Data\n{briefing}\n\n### Candidates Data\n```csv\n{candidates}\n```\n"

    for order, model in desk1_models:
        file_path = desk1_dir / f"{order}_{model}_pack_{date_str}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_desk1_prompt)

    # Desk 2 - Manual Sequence
    desk2_order_map = {
        "Q_chatgpt": (1, "PhaseQ"),
        "R_claude": (2, "PhaseR"),
        "W_gemini": (3, "PhaseW"),
        "X_grok": (4, "PhaseX"),
        "S_claude": (5, "PhaseS")
    }

    src_desk2_dir = Path(r"C:\Users\dasil\OneDrive\Documents\outlier\prompts\desk2")
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

    print(f"Successfully generated {len(desk1_models)} Desk1 and {desk2_count} Desk2 prompt files in {out_dir}/prompts (archived anything older than {min(keep_dates)})")


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
    candidates = filter_candidates_text_for_ai(candidates)

    briefing = briefing.replace("- Use this pack ONLY. Do not use memory or the web.\n", "")
    briefing = briefing.replace("- If you need info not in the pack, list it under NEEDS — do not guess.\n", "")

    for out_dir in out_dirs:
        generate_for_dir(out_dir, date_str, briefing, candidates, args.no_clean)


if __name__ == "__main__":
    main()
