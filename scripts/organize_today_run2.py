"""Organize the latest Outlier pack into Desktop / Google Drive today folders.

Hardening (2026-07-27):
- Replace (not merge) ``*_latest`` export dirs so stale dossiers / mlb_only /
  reports cannot survive an empty re-run.
- Always write sport-split CSVs (header-only when no rows).
- Perfect-hit export applies slate + HR-Under policy filters at generation time.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Allow ``python scripts/organize_today_run2.py`` imports of sibling modules.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from filter_perfect_hit_props import (  # noqa: E402
    FilterOptions,
    allowlist_from_candidates_csv,
    allowlist_from_dossiers_dir,
    filter_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK_SEARCH = [
    REPO_ROOT / "packs",
    Path(r"C:\Users\dasil\OneDrive\Documents\outlier\packs"),
]
DEFAULT_DATA_SEARCH = [
    REPO_ROOT / "data",
    Path(r"C:\Users\dasil\OneDrive\Documents\outlier\data"),
]
DEFAULT_OUT_DIRS = [
    Path(r"C:\Users\dasil\OneDrive\Desktop\today"),
    Path(r"G:\My Drive\today"),
]

HIT_FIELDNAMES = ["player", "market_label", "side", "line", "team", "matchup"]


def safe_copy(src: Path, dst: Path, retries: int = 5, delay: float = 0.5) -> None:
    """Copy file with retries to handle transient cloud sync locks ([WinError 32])."""
    dst.parent.mkdir(parents=True, exist_ok=True)
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


def safe_rmtree(path: Path, retries: int = 5, delay: float = 0.5) -> None:
    """Remove a directory tree with retries for cloud-sync locks."""
    if not path.exists():
        return
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                # Last resort on Windows / OneDrive: shell remove
                subprocess.run(
                    [
                        "powershell",
                        "-Command",
                        f'Remove-Item -LiteralPath "{path}" -Recurse -Force '
                        f"-ErrorAction SilentlyContinue",
                    ],
                    check=False,
                )


def replace_dir(path: Path) -> Path:
    """Delete path if present, recreate empty directory, return path."""
    safe_rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


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
                    safe_rmtree(item)
            except OSError:
                pass


def _row_key(row: dict) -> tuple:
    return (
        row.get("player") or "",
        row.get("market_label") or "",
        row.get("side") or "",
        str(row.get("line") or ""),
        row.get("team") or "",
        row.get("matchup") or "",
    )


def build_slate_allowlist(
    candidates_csv: Path | None,
    dossiers_dir: Path | None,
) -> frozenset[str] | None:
    """Union of matchups from candidates + dossiers. None if both empty (no slate filter)."""
    allow: set[str] = set()
    allow |= allowlist_from_candidates_csv(candidates_csv)
    allow |= allowlist_from_dossiers_dir(dossiers_dir)
    return frozenset(allow) if allow else None


def parse_hit_rates(
    hit_100_props: dict[str, list[dict]],
    data_dirs: list[Path] | None = None,
    filter_opts: FilterOptions | None = None,
) -> dict[str, dict]:
    """Populate perfect-hit buckets from cards JSON with policy filters.

    First matching data_dir that yields rows wins per league.
    Returns a small stats dict for logging (kept/rejected per league).
    """
    from collections import Counter as _Counter

    data_dirs = data_dirs or DEFAULT_DATA_SEARCH
    opts = filter_opts or FilterOptions()
    leagues = ["MLB", "WNBA"]
    stats: dict[str, dict] = {
        league: {
            "raw_l5_l10_l20": 0,
            "kept_l5_l10_l20": 0,
            "reject_reasons": _Counter(),
        }
        for league in leagues
    }

    seen_full: dict[str, set] = {lg: set() for lg in leagues}

    for league in leagues:
        for data_dir in data_dirs:
            if not data_dir.exists():
                continue
            cards_file = data_dir / league / "cards" / f"{league.lower()}_cards_latest.json"
            if not cards_file.exists():
                continue
            try:
                with open(cards_file, "r", encoding="utf-8") as f:
                    cards_data = json.load(f)
            except Exception as e:
                print(f"Error parsing {cards_file}: {e}")
                continue

            raw_rows_full: list[dict] = []

            for board in ["board_a", "board_b"]:
                for card in cards_data.get(board) or []:
                    sides = card.get("sides") or {}
                    for _side_k, side_v in sides.items():
                        hit_rates = side_v.get("hit_rates") or {}
                        l5 = hit_rates.get("l5_pct")
                        l10 = hit_rates.get("l10_pct")
                        l20 = hit_rates.get("l20_pct")
                        row = {
                            "player": card.get("player") or "",
                            "market_label": card.get("market_label") or "",
                            "side": side_v.get("side") or "",
                            "line": side_v.get("line") or "",
                            "team": card.get("team") or "",
                            "matchup": card.get("matchup") or "",
                        }
                        if l5 == 100.0 and l10 == 100.0 and l20 == 100.0:
                            raw_rows_full.append(row)

            if not raw_rows_full:
                continue

            stats[league]["raw_l5_l10_l20"] = len(raw_rows_full)

            kept_full, _rej_full, reasons_full = filter_rows(raw_rows_full, opts)
            stats[league]["reject_reasons"].update(reasons_full)

            for row in kept_full:
                key = _row_key(row)
                if key not in seen_full[league]:
                    seen_full[league].add(key)
                    hit_100_props[league].append(row)

            stats[league]["kept_l5_l10_l20"] = len(hit_100_props[league])
            # First successful source wins per league.
            break

    return stats


def generate_specific_packs(candidates_path: Path, output_dir: Path) -> None:
    """Write mlb_only.csv / wnba_only.csv always (header-only when empty)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    header: list[str] | None = None
    rows: list[dict] = []

    if candidates_path.exists():
        with open(candidates_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = list(reader.fieldnames or [])
            rows = list(reader)

    if not header:
        # Minimal header if candidates missing entirely
        header = ["sport", "matchup", "selection", "board", "actionable"]

    mlb_rows = [r for r in rows if (r.get("sport") or "").upper() == "MLB"]
    wnba_rows = [r for r in rows if (r.get("sport") or "").upper() == "WNBA"]

    for name, subset in [("mlb_only.csv", mlb_rows), ("wnba_only.csv", wnba_rows)]:
        with open(output_dir / name, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(subset)


def write_hit_csv(path: Path, rows: list[dict]) -> None:
    """Always write a CSV (header-only when no rows) so stale files cannot remain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HIT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def find_all_pack_dirs(search_paths: list[Path] | None = None) -> list[Path]:
    search_paths = search_paths or DEFAULT_PACK_SEARCH
    packs_map: dict[str, Path] = {}
    for p in search_paths:
        if p.exists():
            for d in p.iterdir():
                if d.is_dir() and d.name.replace("-", "").isdigit():
                    packs_map[d.name] = d
    return sorted(packs_map.values(), key=lambda d: d.name)


def resolve_candidates_csv(pack_dir: Path) -> Path:
    candidates_csv = pack_dir / "data_analysis" / "candidates.csv"
    if not candidates_csv.exists():
        candidates_csv = pack_dir / "candidates.csv"
    return candidates_csv


def copy_pack_into(pipeline_data: Path, latest_pack: Path) -> None:
    """Full replace: wipe pipeline_data then copy pack contents."""
    replace_dir(pipeline_data)
    for item in latest_pack.iterdir():
        try:
            dest = pipeline_data / item.name
            if item.is_dir():
                shutil.copytree(str(item), str(dest), dirs_exist_ok=False)
            else:
                safe_copy(item, dest)
        except Exception as e:
            print(f"Failed to copy {item.name}: {e}")


def copy_prompt_outputs(
    out_dir: Path,
    generic_prompts: Path,
    hitrate_prompts: Path,
    totals_prompts: Path,
    desk2_prompts: Path | None = None,
) -> None:
    prompts_root = out_dir / "prompts"

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
    if desk2_prompts is not None and desk2_src.exists():
        for item in desk2_src.glob("*.txt"):
            safe_copy(item, desk2_prompts / item.name)

    for item in out_dir.glob("*.txt"):
        if "Cards" in item.name:
            safe_copy(item, generic_prompts / item.name)
            try:
                item.unlink()
            except OSError:
                pass
        elif "HitRate" in item.name:
            safe_copy(item, hitrate_prompts / item.name)
            try:
                item.unlink()
            except OSError:
                pass
        elif "Totals" in item.name:
            safe_copy(item, totals_prompts / item.name)
            try:
                item.unlink()
            except OSError:
                pass
        elif item.name.startswith(
            ("claude", "grok", "copilot", "gemini", "chatgpt", "1_Master", "generic")
        ):
            safe_copy(item, generic_prompts / item.name)
            try:
                item.unlink()
            except OSError:
                pass
        elif (len(item.name) > 1 and item.name[0] in "QRWXS" and item.name[1] == "_") or re.match(
            r"^\d+_Phase[QRWXS]_", item.name
        ):
            if desk2_prompts is not None:
                safe_copy(item, desk2_prompts / item.name)
            try:
                item.unlink()
            except OSError:
                pass


def organize_today_additive(
    pack_search: list[Path] | None = None,
    data_dirs: list[Path] | None = None,
    out_dirs: list[Path] | None = None,
    run_generate_prompts: bool = True,
    include_sequential_prompts: bool = False,
) -> Path | None:
    """Organize latest pack into today folders. Returns latest_pack path or None."""
    subdirs = find_all_pack_dirs(pack_search)
    if not subdirs:
        print("Error: No pack directories found in packs/.")
        return None

    latest_pack = subdirs[-1]
    today_str = latest_pack.name
    suffix = "_latest"

    out_dirs = out_dirs or DEFAULT_OUT_DIRS
    data_dirs = data_dirs or DEFAULT_DATA_SEARCH

    current_date = date.fromisoformat(today_str)
    one_day_old = (current_date - timedelta(days=1)).isoformat()
    keep_dates = {today_str, one_day_old}

    candidates_csv = resolve_candidates_csv(latest_pack)
    dossiers_dir = latest_pack / "dossiers"
    allow = build_slate_allowlist(candidates_csv, dossiers_dir if dossiers_dir.is_dir() else None)
    filter_opts = FilterOptions(
        drop_hr_under=True,
        drop_milestone_under=True,
        require_team_in_matchup=True,
        allow_matchups=allow,
    )
    if allow is not None:
        print(f"Perfect-hit slate allowlist ({len(allow)}): {sorted(allow)}")
    else:
        print(
            "Perfect-hit slate allowlist: OFF "
            "(no matchups in candidates/dossiers — policy side filters still apply)"
        )

    hit_100_props: dict[str, list[dict]] = {"MLB": [], "WNBA": []}
    hit_stats = parse_hit_rates(
        hit_100_props,
        data_dirs=data_dirs,
        filter_opts=filter_opts,
    )
    for league, st in hit_stats.items():
        if st.get("raw_l5_l10_l20"):
            print(
                f"Perfect-hit {league}: L5+L10+L20 raw={st['raw_l5_l10_l20']} "
                f"kept={st['kept_l5_l10_l20']}; "
                f"rejects={dict(st.get('reject_reasons') or {})}"
            )

    if run_generate_prompts:
        gen_script = (
            REPO_ROOT
            / ".agents"
            / "skills"
            / "export-manual-outlier-packs"
            / "scripts"
            / "generate_prompts.py"
        )
        if not gen_script.exists():
            gen_script = Path(
                r"C:\Users\dasil\Dev\GitHub\outlier\.agents\skills"
                r"\export-manual-outlier-packs\scripts\generate_prompts.py"
            )
        if gen_script.exists():
            gen_command = ["python", str(gen_script), "--no-clean"]
            if include_sequential_prompts:
                gen_command.append("--include-sequential-prompts")
            subprocess.run(gen_command, check=True)
        else:
            print(f"Warning: generate_prompts.py not found at {gen_script}")

    for out_dir in out_dirs:
        out_dir.mkdir(parents=True, exist_ok=True)

        purge_old_archive_items(out_dir / "archive", keep_dates)

        # REPLACE (not merge) all _latest export buckets for this slate date
        generic_prompts = replace_dir(out_dir / f"generic_prompts_{today_str}{suffix}")
        hitrate_prompts = replace_dir(out_dir / f"hitrate_prompts_{today_str}{suffix}")
        totals_prompts = replace_dir(out_dir / f"totals_prompts_{today_str}{suffix}")
        desk2_prompts: Path | None = None
        dated_desk2 = out_dir / f"desk2_prompts_{today_str}{suffix}"
        if include_sequential_prompts:
            desk2_prompts = replace_dir(dated_desk2)
        else:
            # Sequential prompts are opt-in. Remove both dated and stable buckets
            # so a prior opt-in run cannot leak them into a regular export.
            safe_rmtree(dated_desk2)
            safe_rmtree(out_dir / "desk2_prompts")
        pipeline_data = out_dir / f"extracted_data_{today_str}{suffix}"
        extra_packs = replace_dir(out_dir / f"extra_packs_{today_str}{suffix}")
        hit_props_dir = replace_dir(out_dir / f"perfect_hit_props_{today_str}{suffix}")

        copy_prompt_outputs(
            out_dir, generic_prompts, hitrate_prompts, totals_prompts, desk2_prompts
        )

        if latest_pack.exists():
            copy_pack_into(pipeline_data, latest_pack)

            generate_specific_packs(candidates_csv, extra_packs)

            for t_csv in [
                "game_totals.csv",
                "team_totals.csv",
                "alt_team_totals.csv",
                "opportunities.csv",
            ]:
                src = latest_pack / t_csv
                if not src.exists():
                    src = latest_pack / "data_analysis" / t_csv
                if src.exists():
                    safe_copy(src, extra_packs / t_csv)
                    if t_csv in [
                        "game_totals.csv",
                        "team_totals.csv",
                        "alt_team_totals.csv",
                    ]:
                        safe_copy(src, totals_prompts / t_csv)

        # Always write perfect-hit CSVs (even empty) so old dirty files cannot linger
        for league in ("MLB", "WNBA"):
            write_hit_csv(
                hit_props_dir / f"{league}_100_hit_rate.csv",
                hit_100_props.get(league) or [],
            )

        # Mirror to clean un-suffixed directories so both date-tagged and standard names work
        mirror_folders = [
            f"generic_prompts_{today_str}{suffix}",
            f"hitrate_prompts_{today_str}{suffix}",
            f"totals_prompts_{today_str}{suffix}",
            f"extracted_data_{today_str}{suffix}",
            f"extra_packs_{today_str}{suffix}",
            f"perfect_hit_props_{today_str}{suffix}",
        ]
        if include_sequential_prompts:
            mirror_folders.append(f"desk2_prompts_{today_str}{suffix}")
        for src_folder_name in mirror_folders:
            std_name = src_folder_name.replace(f"_{today_str}{suffix}", "")
            src_dir = out_dir / src_folder_name
            std_dir = replace_dir(out_dir / std_name)
            if src_dir.exists():
                shutil.copytree(str(src_dir), str(std_dir), dirs_exist_ok=True)

        # Remove empty top-level directories to prevent folder confusion
        for sub in list(out_dir.glob("*")):
            if sub.is_dir() and not sub.name.startswith("."):
                has_files = any(p.is_file() for p in sub.rglob("*"))
                if not has_files:
                    try:
                        shutil.rmtree(sub)
                    except OSError:
                        pass

        print(f"Organized replace-export -> {out_dir} (pack={latest_pack.name})")

    return latest_pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-sequential-prompts",
        action="store_true",
        help="Opt in to generating and exporting the ordered Q/R/W/X/S prompt bundle",
    )
    args = parser.parse_args()
    organize_today_additive(include_sequential_prompts=args.include_sequential_prompts)


if __name__ == "__main__":
    main()
