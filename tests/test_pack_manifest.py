from __future__ import annotations

import json

from outlier_scrapers.pack_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_manifest_v2,
    sha256_file,
    write_manifest,
)


def test_manifest_v2_records_each_present_artifact(tmp_path) -> None:
    pack_dir = tmp_path / "packs" / "2026-08-30"
    pack_dir.mkdir(parents=True)
    (pack_dir / "candidates.csv").write_text("market_id\nm1\n", encoding="utf-8")
    (pack_dir / "briefing.md").write_text("SLATE", encoding="utf-8")
    verdicts = pack_dir / "verdicts"
    verdicts.mkdir()
    (verdicts / "desk_snapshot.json").write_text("{}", encoding="utf-8")

    payload = build_manifest_v2(
        pack_dir,
        run_id="2026-08-30-1",
        timestamp="2026-08-30T00:00:00+00:00",
        leagues=["MLB"],
        profile="local",
        overall="ok",
        pack_rows=1,
        extra={"blend_refit": {"status": "skipped"}},
    )
    paths = {item["path"]: item for item in payload["artifacts"]}
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert payload["overall"] == "ok"
    assert payload["blend_refit"] == {"status": "skipped"}
    assert paths["candidates.csv"]["producer"] == "pack"
    assert paths["candidates.csv"]["sha256"] == sha256_file(pack_dir / "candidates.csv")
    assert paths["candidates.csv"]["bytes"] > 0
    assert paths["verdicts/desk_snapshot.json"]["producer"] == "desk"
    assert "chatgpt_a.md" not in paths


def test_empty_pack_manifest_omits_leftover_desk_artifacts(tmp_path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "candidates.csv").write_text("market_id\n", encoding="utf-8")
    (pack_dir / "chatgpt_a.md").write_text("old A", encoding="utf-8")
    (pack_dir / "claude_e.md").write_text("old E", encoding="utf-8")

    payload = build_manifest_v2(
        pack_dir,
        run_id="empty",
        timestamp="2026-08-30T00:00:00+00:00",
        leagues=["MLB"],
        profile="local",
        overall="ok",
        pack_rows=0,
        include_desk=False,
    )
    paths = {item["path"] for item in payload["artifacts"]}
    assert "candidates.csv" in paths
    assert "chatgpt_a.md" not in paths
    assert "claude_e.md" not in paths


def test_write_manifest_is_atomic(tmp_path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    write_manifest(pack_dir, {"run_id": "x", "overall": "ok"})
    data = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "x"
    assert not (pack_dir / "manifest.json.tmp").exists()
