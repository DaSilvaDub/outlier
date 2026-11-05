"""Inventory, then quarantine explicitly scoped publication outputs; never delete them."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SPORTS = ("NBA", "NFL", "NCAA")
DATE = "2026-11-05"
REDIRECTION_TAGS = {0xA0000003, 0xA000000C}  # mount point / symbolic link
EXPORT_SUFFIXES = {".csv", ".md", ".txt", ".json", ".html", ".pdf"}
PACK_PATTERNS = ("nba_only*", "nfl_only*", "ncaafb_only*")
ALIAS_PATTERNS = ("nba*", "nfl*", "ncaa*", "ncaafb*", "top_5*", "top_10*",
                  "matchup_boost*", "*game_script*", "*alt_floors*",
                  "*-board-reasoning.txt", "*-master-v2-digest.md", "*-mispriced.txt",
                  "*-over-board*.txt", "*-slate.txt", "all_top_props.txt",
                  "game_totals_projection.txt", "sas_okc_*.txt")


def matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in patterns)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def protected(path: Path) -> bool:
    name = path.name.lower()
    return any(token in name for token in (".sqlite", ".db", "-wal", "-shm", "-journal"))


def contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"Path escapes explicit publication root: {path}")
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.exists():
            st = cursor.lstat()
            if cursor.is_symlink() or getattr(st, "st_reparse_tag", 0) in REDIRECTION_TAGS:
                raise ValueError(f"Refusing redirected reparse path: {cursor}")
    return resolved


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def inventory(roots: list[tuple[Path, str]]) -> dict:
    result = {"date": DATE, "status": "INVENTORY_ONLY", "created_utc": datetime.now(timezone.utc).isoformat(),
              "roots": [], "files": [], "issues": []}
    seen: set[str] = set()
    for raw, kind in roots:
        root = raw.resolve()
        key = str(root).casefold()
        if key in seen:
            result["roots"].append({"requested": str(raw), "resolved": str(root), "status": "duplicate_alias"})
            continue
        seen.add(key)
        if kind not in {"today", "betting_reports"}:
            raise ValueError(f"Unknown publication-root kind: {kind}")
        info = {"requested": str(raw), "resolved": str(root), "kind": kind,
                "status": "present" if root.is_dir() else "missing"}
        result["roots"].append(info)
        if not root.is_dir():
            continue
        print(f"Inventorying explicit publication root: {root}", file=sys.stderr, flush=True)
        quarantine = root / "quarantine" / DATE
        candidates: list[Path] = []

        def walk(directory: Path) -> None:
            try:
                contained(directory, root)
                for child in sorted(directory.iterdir()):
                    contained(child, root)
                    if child.is_dir():
                        walk(child)
                    elif child.is_file():
                        candidates.append(child)
            except (OSError, ValueError) as exc:
                result["issues"].append({"path": str(directory), "status": "inventory_error", "error": str(exc)})

        for sport in SPORTS:
            subtree = root / ("Fresh_Data" if kind == "today" else "") / sport
            if subtree.is_dir():
                walk(subtree)
            else:
                result["issues"].append({"path": str(subtree), "status": "missing_scope"})
        if kind == "today":
            # Only immediate export aliases, never arbitrary folders or historical archives.
            for child in sorted(root.iterdir()):
                if child.name.lower().startswith("extra_packs") and child.is_dir():
                    try:
                        contained(child, root)
                        for pack in sorted(child.iterdir()):
                            if pack.is_file() and matches(pack.name, PACK_PATTERNS):
                                candidates.append(pack)
                    except (OSError, ValueError) as exc:
                        result["issues"].append({"path": str(child), "status": "inventory_error", "error": str(exc)})
                elif (child.is_file() and child.suffix.lower() in EXPORT_SUFFIXES
                      and matches(child.name, ALIAS_PATTERNS)):
                    candidates.append(child)
        for path in candidates:
            row = {"source": str(path), "root": str(root),
                   "destination": str(quarantine / path.relative_to(root)), "status": "planned"}
            try:
                contained(path, root)
                if path.name == "QUARANTINE_HOLD.json":
                    row["status"] = "retained_publication_hold"
                elif protected(path):
                    row["status"] = "retained_research_database"
                else:
                    row.update(size=path.stat().st_size, sha256=sha256(path))
            except (OSError, ValueError) as exc:
                row.update(status="inventory_error", error=str(exc))
            result["files"].append(row)
            if len(result["files"]) % 100 == 0:
                print(f"Inventoried {len(result['files'])} files", file=sys.stderr, flush=True)
    return result


def apply(manifest: dict, receipt: Path) -> dict:
    if manifest.get("date") != DATE:
        raise ValueError("Unexpected quarantine date")
    allowed_roots = {str(Path(row["resolved"]).resolve()) for row in manifest["roots"]
                     if row["status"] == "present"}
    manifest["status"] = "APPLYING"
    save(receipt, manifest)
    for row in manifest["files"]:
        if row["status"] != "planned":
            continue
        source, root, dest = Path(row["source"]), Path(row["root"]), Path(row["destination"])
        try:
            if str(root) not in allowed_roots or protected(source) or source.name == "QUARANTINE_HOLD.json":
                raise ValueError("Source root not allowlisted or protected database")
            contained(source, root)
            expected = root / "quarantine" / DATE / source.relative_to(root)
            if dest != expected:
                raise ValueError("Unexpected quarantine destination")
            contained(dest, root)
            if source.exists():
                if sha256(source) != row["sha256"]:
                    raise ValueError("Source changed since inventory")
                if dest.exists():
                    raise ValueError("Destination already exists; preserving both files")
                dest.parent.mkdir(parents=True, exist_ok=True)
                contained(dest, root)
                if source.stat().st_dev != dest.parent.stat().st_dev:
                    raise ValueError("Cross-device quarantine is forbidden")
                os.rename(source, dest)
            elif not dest.exists():
                raise FileNotFoundError("Source and quarantine destination are missing")
            if sha256(dest) != row["sha256"]:
                raise ValueError("Quarantine hash verification failed")
            row["status"] = "quarantined"
        except (OSError, ValueError) as exc:
            row.update(status="quarantine_error", error=str(exc))
        save(receipt, manifest)
        if sum(item["status"] == "quarantined" for item in manifest["files"]) % 100 == 0:
            print(f"Quarantine progress: {row['status']}: {source}", file=sys.stderr, flush=True)
    manifest["holds"] = []
    for info in manifest["roots"]:
        if info["status"] != "present":
            continue
        root = Path(info["resolved"])
        for sport in SPORTS:
            marker = root / ("Fresh_Data" if info["kind"] == "today" else "") / sport / "QUARANTINE_HOLD.json"
            try:
                contained(marker, root)
                save(marker, {"status": "QUARANTINED / NO BET", "staking_allowed": False,
                              "date": DATE, "manifest": str(receipt),
                              "reason": "Audit remediation: published outputs withdrawn; databases retained for research only."})
                manifest["holds"].append({"path": str(marker), "status": "written"})
            except (OSError, ValueError) as exc:
                manifest["holds"].append({"path": str(marker), "status": "hold_error", "error": str(exc)})
    errors = [row for row in manifest["files"] if row["status"].endswith("error")]
    errors.extend(row for row in manifest["holds"] if row["status"] == "hold_error")
    manifest["status"] = "PARTIAL" if errors or any(x["status"] == "inventory_error" for x in manifest["issues"]) else "SCOPED_QUARANTINE_COMPLETE"
    save(receipt, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today-root", type=Path, action="append", default=[])
    parser.add_argument("--betting-reports-root", type=Path, action="append", default=[])
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        payload = json.loads(args.inventory.read_text(encoding="utf-8"))
        apply(payload, args.inventory)
    else:
        if args.inventory.exists():
            raise FileExistsError("Refusing to overwrite an existing quarantine inventory")
        payload = inventory([(p, "today") for p in args.today_root] +
                            [(p, "betting_reports") for p in args.betting_reports_root])
        save(args.inventory, payload)
    counts: dict[str, int] = {}
    for row in payload["files"]:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"status": payload["status"], "counts": counts,
                      "issues": len(payload["issues"]), "manifest": str(args.inventory)}))
    return int(payload["status"] == "PARTIAL")


if __name__ == "__main__":
    raise SystemExit(main())
