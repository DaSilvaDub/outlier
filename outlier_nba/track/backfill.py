"""Backfill ``market_id``/``side`` on old ``nba_model_probs`` rows from run bundles.

Model rows recorded before the key columns existed are ``key_unverifiable`` and excluded from
the gate evidence. The run bundle each row came from (``runs/<run_id>/team_shadow.csv``) holds
the keys, so they can be recovered, but only on proof:

* a bundle counts only if its sha256 matches its sealed ``manifest.json``;
* a ledger row is matched to bundle rows of the *same run* on event, outcome, snapshot time
  (``as_of``), family and line; the match must resolve to exactly one ``(market_id, side)``;
* zero matches stay unkeyed (``unmatched`` / ``no_bundle``), several different ones stay
  unkeyed (``ambiguous``): nothing is guessed, and an existing key is never overwritten.

The original ledger is only ever read. Work happens on a copy in local temp, and an applied
result is written solely to an explicit ``--out`` path that is not the source, not the shared
ledger and not inside the repository checkout (which sits behind a cloud-sync filter).
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from outlier_nba.run_context import try_parse_utc
from outlier_nba.run_writer import sha256_hex
from outlier_nba.track import model_ledger
from outlier_nba.track.evidence import _same_line
from outlier_scrapers import paths

logger = structlog.get_logger("outlier_nba.backfill")

BUNDLE_FILE = "team_shadow.csv"
COUNT_KEYS = ("matched", "ambiguous", "unmatched", "no_bundle", "already_keyed")


class UnsafeOutputError(ValueError):
    """The requested output path could modify or sit beside the original ledger unsafely."""


@dataclass
class BackfillResult:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNT_KEYS, 0))
    bundles_scanned: int = 0
    bundles_rejected: int = 0
    applied: bool = False
    out: str | None = None
    integrity: str | None = None


def _verified_rows(csv_path: Path) -> list[dict[str, str]] | None:
    """Rows of a bundle's ``team_shadow.csv``, or None if it is not sealed by its manifest."""
    manifest = csv_path.parent / "manifest.json"
    try:
        sealed = json.loads(manifest.read_text(encoding="utf-8"))
        data = csv_path.read_bytes()
    except (OSError, ValueError):
        return None
    if not isinstance(sealed, dict) or sealed.get("run_id", csv_path.parent.name) != (
        csv_path.parent.name
    ):
        return None
    digests = {
        a.get("name"): a.get("sha256") for a in sealed.get("artifacts", []) if isinstance(a, dict)
    }
    if digests.get(BUNDLE_FILE) != sha256_hex(data):
        return None
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def load_bundles(roots: Sequence[Path]) -> tuple[dict[str, list[dict[str, str]]], int, int]:
    """``({run_id: rows}, scanned, rejected)`` over every ``team_shadow.csv`` under the roots."""
    by_run: dict[str, list[dict[str, str]]] = defaultdict(list)
    scanned = rejected = 0
    for root in roots:
        for csv_path in sorted(Path(root).rglob(BUNDLE_FILE)):
            scanned += 1
            rows = _verified_rows(csv_path)
            if rows is None:
                rejected += 1
                logger.warning("backfill_bundle_rejected", path=str(csv_path))
                continue
            by_run[csv_path.parent.name].extend(rows)
    return by_run, scanned, rejected


def _candidates(row: sqlite3.Row, bundle_rows: Sequence[dict[str, str]]) -> set[tuple[str, str]]:
    """Distinct usable ``(market_id, side)`` from bundle rows with this row's identity."""
    at = try_parse_utc(row["as_of"])
    found: set[tuple[str, str]] = set()
    if at is None:
        return found
    for b in bundle_rows:
        if (
            b.get("event_id") == row["event_id"]
            and b.get("outcome_id") == row["outcome_id"]
            and b.get("family") == row["family"]
            and try_parse_utc(b.get("as_of")) == at
            and _same_line(row["line"], b.get("line"), moneyline=row["family"] == "team.ML")
            and b.get("market_id")
            and b.get("side")
        ):
            found.add((str(b["market_id"]), str(b["side"])))
    return found


def _check_output(source: Path, out: Path | None) -> Path:
    if out is None:
        raise UnsafeOutputError("applying requires an explicit --out path")
    target = Path(out).resolve()
    root = Path(paths.PROJECT_ROOT).resolve()
    if target == Path(source).resolve() or target == Path(paths.FEEDBACK_DB_PATH).resolve():
        raise UnsafeOutputError(f"refusing to write the original ledger: {target}")
    if target == root or root in target.parents:
        raise UnsafeOutputError(f"refusing to write inside the repository checkout: {target}")
    if target.exists():
        raise UnsafeOutputError(f"output already exists, refusing to overwrite: {target}")
    return target


def _copy_ledger(source: Path, dest: Path) -> None:
    shutil.copy2(source, dest)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{source}{suffix}")
        if side.exists():
            shutil.copy2(side, Path(f"{dest}{suffix}"))


def _plan(
    conn: sqlite3.Connection, bundles: dict[str, list[dict[str, str]]], result: BackfillResult
) -> list[tuple[str, str, str, str, str]]:
    conn.row_factory = sqlite3.Row
    updates: list[tuple[str, str, str, str, str]] = []
    rows = conn.execute(
        "SELECT event_id, outcome_id, run_id, as_of, family, line, market_id "
        "FROM nba_model_probs ORDER BY run_id, event_id, outcome_id"
    ).fetchall()
    for row in rows:
        if row["market_id"] not in (None, ""):
            result.counts["already_keyed"] += 1
            continue
        bundle_rows = bundles.get(row["run_id"])
        if not bundle_rows:
            result.counts["no_bundle"] += 1
            continue
        keys = _candidates(row, bundle_rows)
        if len(keys) == 1:
            market_id, side = next(iter(keys))
            updates.append((market_id, side, row["event_id"], row["outcome_id"], row["run_id"]))
            result.counts["matched"] += 1
        elif keys:
            result.counts["ambiguous"] += 1
        else:
            result.counts["unmatched"] += 1
    return updates


def backfill(
    db_path: Path,
    bundle_roots: Sequence[Path],
    *,
    out: Path | None = None,
    apply: bool = False,
) -> BackfillResult:
    """Plan (and with ``apply`` write to ``out``) the key backfill. Dry run by default."""
    target = _check_output(db_path, out) if apply else None
    bundles, scanned, rejected = load_bundles(bundle_roots)
    result = BackfillResult(bundles_scanned=scanned, bundles_rejected=rejected)
    with tempfile.TemporaryDirectory(prefix="nba_backfill_") as tmp:
        work = Path(tmp) / "ledger.sqlite3"
        _copy_ledger(Path(db_path), work)
        conn = sqlite3.connect(work)
        try:
            model_ledger.ensure_model_schema(conn)
            updates = _plan(conn, bundles, result)
            if apply and target is not None:
                conn.executemany(
                    "UPDATE nba_model_probs SET market_id = ?, side = ? WHERE event_id = ? "
                    "AND outcome_id = ? AND run_id = ? AND market_id IS NULL",
                    updates,
                )
                conn.commit()
                result.integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        if apply and target is not None:
            if result.integrity != "ok":
                raise RuntimeError(
                    f"integrity check failed on the working copy: {result.integrity}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work, target)
            result.applied = True
            result.out = str(target)
    return result


def describe(result: BackfillResult) -> dict[str, Any]:
    return {
        "applied": result.applied,
        "out": result.out,
        "integrity": result.integrity,
        "bundles_scanned": result.bundles_scanned,
        "bundles_rejected": result.bundles_rejected,
        "counts": result.counts,
    }
