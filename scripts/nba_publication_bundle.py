"""Sanitize NBA companion cards and verify each published CSV against its status."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_nba_extra_pack import FIELDNAMES, publication_eligible


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(path: Path, slate_date: str, summary: Path | None = None) -> dict:
    status = json.loads(path.with_suffix(".status.json").read_text(encoding="utf-8"))
    _, rows = read_rows(path)
    if (
        status.get("slate_date") != slate_date
        or status.get("row_count") != len(rows)
        or status.get("actionable_rows") != len(rows)
        or status.get("csv_sha256") != digest(path)
        or status.get("status") not in {"ready", "awaiting_odds", "awaiting_availability"}
        or (rows and status.get("status") != "ready")
        or any(not publication_eligible(row, slate_date) for row in rows)
    ):
        raise ValueError(f"Unverified NBA publication: {path}")
    if summary:
        data = json.loads(summary.read_text(encoding="utf-8"))
        if (
            data.get("after_rows") != len(rows)
            or data.get("slate_date") != slate_date
            or data.get("csv_sha256") != status["csv_sha256"]
            or data.get("status") != status["status"]
            or data.get("actionable_rows") != len(rows)
        ):
            raise ValueError(f"NBA summary disagrees with CSV: {summary}")
    return status


def selection(row: dict[str, str]) -> tuple[str, ...]:
    # Exact frozen selection and price contract. A projection/grade cannot create a bet.
    return tuple(
        str(row.get(k, "")).strip().casefold()
        for k in (
            "slate_date",
            "game_time",
            "matchup",
            "player",
            "market",
            "line",
            "recommended_side",
            "recommended_odds",
        )
    )


def sanitize(source: Path | None, destination: Path, pack: Path, slate_date: str) -> int:
    verify(pack, slate_date)
    _, authorized = read_rows(pack)
    selections = {selection(row) for row in authorized}
    fields, rows = read_rows(source) if source and source.is_file() else (FIELDNAMES, [])
    kept = [
        row
        for row in rows
        if publication_eligible(row, slate_date) and selection(row) in selections
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    tmp.replace(destination)
    status = {
        "slate_date": slate_date,
        "row_count": len(kept),
        "actionable_rows": len(kept),
        "status": "ready" if kept else "awaiting_odds",
        "csv_sha256": digest(destination),
    }
    destination.with_suffix(".status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )
    return len(kept)


def copy_verified(
    source: Path, destination: Path, slate_date: str, summary: Path | None = None
) -> None:
    verify(source, slate_date, summary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    for src, dst in [
        (source, destination),
        (source.with_suffix(".status.json"), destination.with_suffix(".status.json")),
    ]:
        for attempt in range(5):
            try:
                tmp = dst.with_suffix(dst.suffix + ".tmp")
                shutil.copyfile(src, tmp)
                tmp.replace(dst)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    if summary:
        shutil.copyfile(summary, destination.parent / summary.name)
    verify(destination, slate_date, destination.parent / summary.name if summary else None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--edge-source", type=Path)
    parser.add_argument("--grade-source", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    verify(args.csv, args.date, args.summary)
    if args.out_dir:
        sanitize(args.edge_source, args.out_dir / "nba_prop_edges.csv", args.csv, args.date)
        sanitize(args.grade_source, args.out_dir / "nba_graded_plays.csv", args.csv, args.date)
    if args.destination:
        copy_verified(args.csv, args.destination, args.date, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
