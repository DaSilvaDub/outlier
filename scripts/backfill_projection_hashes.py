"""Backfill projection_feature_hash/flags from pack candidates into feedback DB.

Safe additive update: only fills blank projection_* columns. Does not change
probs, units, or settlements. Intended for feature-branch measurement so
``so_eval`` can see gamelog digests on already-captured rows.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from outlier_scrapers.feedback import initialize_database


def _load_candidate_projection_fields(pack_dir: Path) -> list[dict[str, str]]:
    path = pack_dir / "candidates.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def backfill(db_path: Path, pack_dirs: list[Path]) -> dict[str, int]:
    initialize_database(db_path)
    updated = 0
    scanned = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for pack_dir in pack_dirs:
            for row in _load_candidate_projection_fields(pack_dir):
                scanned += 1
                digest = str(row.get("projection_feature_hash") or "").strip()
                flags = str(row.get("projection_quality_flags") or "").strip()
                if not digest and not flags:
                    continue
                event_id = str(row.get("event_id") or "").strip()
                market_id = str(row.get("market_id") or "").strip()
                outcome_id = str(row.get("outcome_id") or "").strip()
                selection = str(row.get("selection") or "").strip()
                if not (event_id and market_id and outcome_id):
                    continue
                cur = conn.execute(
                    """
                    UPDATE market_snapshots
                    SET
                      projection_feature_hash = CASE
                        WHEN COALESCE(projection_feature_hash, '') = '' THEN ?
                        ELSE projection_feature_hash
                      END,
                      projection_quality_flags = CASE
                        WHEN COALESCE(projection_quality_flags, '') = '' THEN ?
                        ELSE projection_quality_flags
                      END
                    WHERE event_id = ? AND market_id = ? AND outcome_id = ?
                      AND selection = ?
                      AND (
                        COALESCE(projection_feature_hash, '') = ''
                        OR COALESCE(projection_quality_flags, '') = ''
                      )
                    """,
                    (digest, flags, event_id, market_id, outcome_id, selection),
                )
                updated += cur.rowcount
        conn.commit()
    return {"scanned": scanned, "updated": updated, "packs": len(pack_dirs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(r"C:\Users\dasil\Dev\GitHub\outlier\calibration\feedback.sqlite3"),
    )
    parser.add_argument(
        "--packs-root",
        type=Path,
        default=Path(r"C:\Users\dasil\Dev\GitHub\outlier\packs"),
    )
    parser.add_argument("--dates", nargs="*", default=["2026-08-21", "2026-08-22"])
    args = parser.parse_args()
    pack_dirs = [args.packs_root / date for date in args.dates]
    stats = backfill(args.db, pack_dirs)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
