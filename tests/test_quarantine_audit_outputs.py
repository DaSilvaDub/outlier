"""Quarantine tests operate exclusively on temporary publication trees."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "quarantine", Path(__file__).resolve().parents[1] / "scripts/quarantine_audit_outputs.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def seed(tmp_path):
    root = tmp_path / "today"
    source = root / "Fresh_Data/NBA/03_Early_Pipeline/props.csv"
    source.parent.mkdir(parents=True)
    source.write_text("unsafe betting output")
    return root, source


def test_inventory_read_only_then_move_hash_verified(tmp_path):
    root, source = seed(tmp_path)
    payload = q.inventory([(root, "today")])
    assert source.exists()
    q.apply(payload, tmp_path / "manifest.json")
    row = payload["files"][0]
    assert row["status"] == "quarantined"
    assert not source.exists()
    assert q.sha256(Path(row["destination"])) == row["sha256"]
    q.apply(payload, tmp_path / "manifest.json")
    assert row["status"] == "quarantined"
    after = q.inventory([(root, "today")])
    assert not any(item["status"] == "planned" for item in after["files"])
    assert sum(item["status"] == "retained_publication_hold" for item in after["files"]) == 3


def test_database_and_historical_archive_untouched(tmp_path):
    root, _ = seed(tmp_path)
    database = root / "Fresh_Data/NBA/ledger.sqlite3"
    database.write_bytes(b"research")
    historical = root / "Older_Data/old.csv"
    historical.parent.mkdir()
    historical.write_text("old")
    payload = q.inventory([(root, "today")])
    q.apply(payload, tmp_path / "manifest.json")
    assert database.read_bytes() == b"research"
    assert historical.read_text() == "old"


def test_modified_source_blocks_without_move(tmp_path):
    root, source = seed(tmp_path)
    payload = q.inventory([(root, "today")])
    source.write_text("changed")
    q.apply(payload, tmp_path / "manifest.json")
    assert source.exists()
    assert payload["status"] == "PARTIAL"


def test_destination_escape_rejected(tmp_path):
    root, source = seed(tmp_path)
    payload = q.inventory([(root, "today")])
    payload["files"][0]["destination"] = str(tmp_path / "outside.csv")
    q.apply(payload, tmp_path / "manifest.json")
    assert source.exists()
    assert payload["files"][0]["status"] == "quarantine_error"


def test_duplicate_alias_and_missing_root_recorded(tmp_path):
    root, _ = seed(tmp_path)
    payload = q.inventory([(root, "today"), (root, "today"), (tmp_path / "missing", "today")])
    assert len(payload["files"]) == 1
    assert [row["status"] for row in payload["roots"]] == ["present", "duplicate_alias", "missing"]


def test_other_sports_and_unrelated_root_files_preserved(tmp_path):
    root, _ = seed(tmp_path)
    packs = root / "extra_packs"
    packs.mkdir()
    for name in ("mlb_only.csv", "wnba_only.csv", "nfl_only.csv"):
        (packs / name).write_text(name)
    for name in ("user_notes.md", "Bankroll_Master_Ledger.md", "candidates.csv"):
        (root / name).write_text(name)
    payload = q.inventory([(root, "today")])
    q.apply(payload, tmp_path / "manifest.json")
    assert not (packs / "nfl_only.csv").exists()
    for path in (packs / "mlb_only.csv", packs / "wnba_only.csv", root / "user_notes.md",
                 root / "Bankroll_Master_Ledger.md", root / "candidates.csv"):
        assert path.exists()
