"""Backfilling model-probability keys from run bundles: exact identity, dry run, temp copy only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import outlier_nba.cli as cli
from outlier_nba import team_run
from outlier_nba.run_writer import sha256_hex
from outlier_nba.track import backfill, model_ledger
from outlier_scrapers import paths

AS_OF = "2026-10-22T17:30:00+00:00"


def _bundle_row(**kw):
    base = {f: "" for f in team_run.ROW_FIELDS}
    base.update(
        event_id="e1", market_id="m1", outcome_id="o1", market="TOTAL", family="team.TOTAL",
        side="over", line="221.5", as_of=AS_OF, model_prob="0.6",
    )  # fmt: skip
    base.update(kw)
    return base


def _write_bundle(root: Path, run_id: str, rows, *, manifest: bool = True, tamper: bool = False):
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    data = team_run.rows_to_csv(rows).encode("utf-8")
    (run_dir / "team_shadow.csv").write_bytes(data + (b"#" if tamper else b""))
    if manifest:
        body = {
            "run_id": run_id,
            "artifacts": [
                {"name": "team_shadow.csv", "sha256": sha256_hex(data), "bytes": len(data)}
            ],
        }
        (run_dir / "manifest.json").write_text(json.dumps(body), encoding="utf-8")
    return run_dir


def _ledger(db: Path, rows) -> Path:
    """An old-style ledger: model rows with no keys."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            model_ledger.MODEL_TABLE_SQL.replace("    market_id TEXT,\n", "").replace(
                "    side TEXT,\n", ""
            )
        )
        conn.executemany(
            "INSERT INTO nba_model_probs (event_id, outcome_id, run_id, as_of, family, market, "
            "line, model_prob, model_weight) VALUES (?,?,?,?,?,?,?,?,0.0)",
            rows,
        )
    conn = sqlite3.connect(db)
    conn.close()
    return db


def _row(run="r1", event="e1", outcome="o1", as_of=AS_OF, family="team.TOTAL", line="221.5"):
    return (event, outcome, run, as_of, family, "TOTAL", line, 0.6)


def _has_keys(db: Path) -> bool:
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nba_model_probs)")}
    return "market_id" in cols


def _keys(db: Path):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT event_id, outcome_id, run_id, market_id, side FROM nba_model_probs "
            "ORDER BY event_id, outcome_id, run_id"
        ).fetchall()


@pytest.fixture
def work(tmp_path: Path):
    return tmp_path


def test_dry_run_reports_matches_and_writes_nothing(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    before = db.read_bytes()
    res = backfill.backfill(db, [work / "b"])
    assert res.counts["matched"] == 1 and res.applied is False
    assert db.read_bytes() == before


def test_apply_writes_keys_to_the_copy_only(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    before, out = db.read_bytes(), work / "out" / "copy.sqlite3"
    res = backfill.backfill(db, [work / "b"], out=out, apply=True)
    assert res.applied and res.counts["matched"] == 1 and res.integrity == "ok"
    assert db.read_bytes() == before  # the original is never modified
    assert _keys(out) == [("e1", "o1", "r1", "m1", "over")]


def test_unmatched_rows_stay_unkeyed_and_are_counted(work) -> None:
    db = _ledger(
        work / "l.sqlite3",
        [
            _row(),
            _row(outcome="o2", as_of="2026-10-22T17:31:00+00:00"),  # different snapshot time
            _row(outcome="o3", line="222.5"),  # different line
            _row(outcome="o4", family="team.SPREAD"),  # different family
            _row(outcome="o5"),  # not in the bundle
        ],
    )
    rows = [
        _bundle_row(),
        _bundle_row(outcome_id="o2", market_id="m2"),
        _bundle_row(outcome_id="o3", market_id="m3"),
        _bundle_row(outcome_id="o4", market_id="m4"),
    ]
    _write_bundle(work / "b", "r1", rows)
    out = work / "copy.sqlite3"
    res = backfill.backfill(db, [work / "b"], out=out, apply=True)
    assert res.counts["matched"] == 1 and res.counts["unmatched"] == 4
    keyed = {(r[1], r[3]) for r in _keys(out)}
    assert keyed == {("o1", "m1"), ("o2", None), ("o3", None), ("o4", None), ("o5", None)}


def test_an_ambiguous_bundle_identity_is_never_guessed(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row(market_id="m1"), _bundle_row(market_id="OTHER")])
    out = work / "copy.sqlite3"
    res = backfill.backfill(db, [work / "b"], out=out, apply=True)
    assert res.counts["ambiguous"] == 1 and res.counts["matched"] == 0
    assert _keys(out)[0][3] is None


def test_identical_duplicate_bundle_rows_are_not_ambiguous(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row(), _bundle_row()])
    res = backfill.backfill(db, [work / "b"], out=work / "c.sqlite3", apply=True)
    assert res.counts["matched"] == 1 and res.counts["ambiguous"] == 0


def test_a_bundle_row_without_a_market_id_cannot_key_anything(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row(market_id="")])
    res = backfill.backfill(db, [work / "b"], out=work / "c.sqlite3", apply=True)
    assert res.counts["unmatched"] == 1 and res.counts["matched"] == 0


def test_a_tampered_or_unsealed_bundle_is_rejected(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row(run="r1"), _row(run="r2")])
    _write_bundle(work / "b", "r1", [_bundle_row()], tamper=True)
    _write_bundle(work / "b", "r2", [_bundle_row()], manifest=False)
    res = backfill.backfill(db, [work / "b"], out=work / "c.sqlite3", apply=True)
    assert res.bundles_rejected == 2 and res.counts["matched"] == 0
    assert res.counts["no_bundle"] == 2


def test_a_row_whose_run_has_no_bundle_is_counted(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row(run="gone")])
    (work / "b").mkdir()
    res = backfill.backfill(db, [work / "b"])
    assert res.counts["no_bundle"] == 1


def test_an_existing_key_is_never_overwritten(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    with sqlite3.connect(db) as conn:
        model_ledger.ensure_model_schema(conn)
        conn.execute("UPDATE nba_model_probs SET market_id='mine', side='under'")
    _write_bundle(work / "b", "r1", [_bundle_row()])
    out = work / "c.sqlite3"
    res = backfill.backfill(db, [work / "b"], out=out, apply=True)
    assert res.counts["already_keyed"] == 1 and res.counts["matched"] == 0
    assert _keys(out) == [("e1", "o1", "r1", "mine", "under")]


@pytest.mark.parametrize("which", ["same", "in_repo", "exists", "default"])
def test_unsafe_output_paths_are_refused(work, which) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    out = {
        "same": db,
        "in_repo": paths.PROJECT_ROOT / "tmp_backfill_copy.sqlite3",
        "exists": work / "present.sqlite3",
        "default": paths.FEEDBACK_DB_PATH,
    }[which]
    if which == "exists":
        out.write_bytes(b"x")
    with pytest.raises(backfill.UnsafeOutputError):
        backfill.backfill(db, [work / "b"], out=out, apply=True)
    assert not (paths.PROJECT_ROOT / "tmp_backfill_copy.sqlite3").exists()


def test_apply_without_an_output_path_is_refused(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    with pytest.raises(backfill.UnsafeOutputError):
        backfill.backfill(db, [work / "b"], apply=True)


def test_rows_with_other_old_columns_still_migrate_in_the_copy(work) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    out = work / "c.sqlite3"
    backfill.backfill(db, [work / "b"], out=out, apply=True)
    with sqlite3.connect(out) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nba_model_probs)")}
    assert {"market_id", "side"} <= cols


def test_cli_defaults_to_a_dry_run(work, capsys) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    args = ["backfill-model-probs", "--db", str(db), "--bundles", str(work / "b")]
    assert cli.main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["counts"]["matched"] == 1
    assert not _has_keys(db)  # the original keeps its old schema


def test_cli_apply_needs_out_and_then_writes_the_copy(work, capsys) -> None:
    db = _ledger(work / "l.sqlite3", [_row()])
    _write_bundle(work / "b", "r1", [_bundle_row()])
    base = ["backfill-model-probs", "--db", str(db), "--bundles", str(work / "b"), "--apply"]
    assert cli.main(base) == 2
    assert "--out" in capsys.readouterr().out
    out = work / "copy.sqlite3"
    assert cli.main([*base, "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert _keys(out)[0][3] == "m1" and not _has_keys(db)


def test_cli_missing_ledger_fails_loudly(work, capsys) -> None:
    assert cli.main(["backfill-model-probs", "--db", str(work / "nope.sqlite3")]) == 2
    assert "not found" in capsys.readouterr().out
