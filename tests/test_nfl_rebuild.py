"""#232 part a: rebuild from admissible archived originals (replay only, versioned).

Originals are real pipeline runs. A *live* original is produced the way the
stage-gate tests do it: the harness's live-shaped ``FrozenOutlierClient`` with a
pregame clock. It is archived with the 9c writer (``archive.record_pointed_runs``).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import archive as ar
from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl import rebuild as rb
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """External/weather/usage loaders would fetch current nflverse; keep tests hermetic."""
    def unavailable(*_a: Any, **_k: Any) -> Any:
        raise OSError("network disabled in tests")

    for name in ("load_external_metrics", "load_slate_weather", "load_usage"):
        monkeypatch.setattr(nfl_pipeline, name, unavailable)


def _tape(nfl: Path) -> Path:
    tape = nfl / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True, exist_ok=True)
    tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    return tape


def live_original(tmp_path: Path, client: Any = None, clock: str = PREGAME) -> tuple[Path, str]:
    nfl = tmp_path / "NFL"
    _tape(nfl)
    now = parse_utc(clock)
    s = NflPipeline(client=client or snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                    clock=lambda: now).run(date=SLATE, as_of_utc=clock,
                                           reports_dir=tmp_path / "reports")
    return nfl, s["run_id"]


def _originals(nfl: Path) -> dict[str, bytes]:
    return {p.relative_to(nfl).as_posix(): p.read_bytes() for p in sorted(nfl.rglob("*"))
            if p.is_file() and "versions" not in p.relative_to(nfl).parts}


@pytest.fixture
def archived(tmp_path):
    nfl, run_id = live_original(tmp_path)
    assert not ar.record_pointed_runs(nfl).conflicts
    return nfl, run_id


def test_archived_live_original_is_admissible(archived):
    nfl, run_id = archived
    [v] = rb.admissibility(nfl)
    assert (v.slate, v.admissible, v.run_id, v.reasons) == (SLATE, True, run_id, [])
    # The four required roles plus the tape the run admitted (archived since #258).
    assert {e.role for e in v.entries} == {*ar.REQUIRED_SLATE_ROLES, ar.TAPE_ROLE}


def test_unarchived_slate_is_not_admissible(tmp_path):
    nfl, _ = live_original(tmp_path)
    [v] = rb.admissibility(nfl)
    assert not v.admissible and any("missing_role" in r for r in v.reasons)
    with pytest.raises(rb.RebuildRefused, match="not admissible"):
        rb.rebuild_slate(nfl, SLATE, clock=lambda: NOW)
    assert not (nfl / "versions").exists()


def test_fixture_original_is_not_admissible(tmp_path):
    NflPipeline(data_dir=tmp_path).run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR,
                                       reports_dir=tmp_path / "reports")
    nfl = tmp_path / "NFL"
    ar.record_pointed_runs(nfl)
    [v] = rb.admissibility(nfl)
    assert not v.admissible and any("mode 'fixture'" in r for r in v.reasons)


def test_tampered_archive_input_is_not_admissible(archived):
    nfl, run_id = archived
    props = nfl / "runs" / run_id / "raw" / "player_props.json"
    props.write_bytes(props.read_bytes() + b" ")
    [v] = rb.admissibility(nfl)
    assert not v.admissible and any("size_mismatch" in r for r in v.reasons)


def test_rebuild_is_replay_versioned_and_touches_no_original(archived):
    nfl, run_id = archived
    before = _originals(nfl)
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW, code_version="abc")
    out = nfl / "versions" / "r1"
    # NOW is past kickoff, so RunContext calls it retrospective, with replay=True.
    assert (m["rebuilt_mode"], m["rebuilt_replay"], m["publication"]) == (
        "retrospective", True, "bundle_only")
    assert m["source_run_id"] == run_id and m["as_of_utc"] == PREGAME
    assert {i["role"] for i in m["inputs"]} == {*ar.REQUIRED_SLATE_ROLES, ar.TAPE_ROLE}
    assert all(len(i["sha256"]) == 64 for i in m["inputs"])
    assert json.loads((out / "rebuild_manifest.json").read_text("utf-8")) == m
    assert (out / m["rebuilt_run_dir"] / "nfl_best_bets.json").is_file()
    assert _originals(nfl) == before  # predictions, pointers, snapshots, archive untouched


def test_rebuild_id_is_write_once(archived):
    nfl, _ = archived
    rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    marker = (nfl / "versions" / "r1" / "rebuild_manifest.json").read_bytes()
    with pytest.raises(rb.RebuildRefused, match="never overwrites"):
        rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    assert (nfl / "versions" / "r1" / "rebuild_manifest.json").read_bytes() == marker
    with pytest.raises(rb.RebuildRefused, match="invalid rebuild id"):
        rb.rebuild_slate(nfl, SLATE, rebuild_id="../x", clock=lambda: NOW)


def test_rebuild_serves_exactly_the_archived_inputs(archived, monkeypatch):
    nfl, run_id = archived
    seen: dict[str, Any] = {}
    real = rb.ArchivedOutlierClient.__init__

    def spy(self, schedule, markets, props):
        seen.update(schedule=schedule, markets=markets, props=props)
        real(self, schedule, markets, props)

    monkeypatch.setattr(rb.ArchivedOutlierClient, "__init__", spy)
    rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    raw = nfl / "runs" / run_id / "raw"
    assert seen["props"] == json.loads((raw / "player_props.json").read_text("utf-8"))
    assert seen["markets"] == json.loads((raw / "event_markets.json").read_text("utf-8"))


def test_rebuild_writes_a_cause_labelled_comparison(archived):
    nfl, _ = archived
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    out = nfl / "versions" / "r1"
    comp = json.loads((out / "comparison.json").read_text("utf-8"))
    assert comp["edge_basis"].startswith("sportsbook prices only")
    assert comp["original_picks"] > 0 and comp["rebuilt_picks"] > 0
    assert all(d["causes"] for d in comp["differences"])
    assert m["comparison"]["differences"] == len(comp["differences"])
    assert "# NFL rebuild comparison: 2026-10-04 (r1)" in (out / "comparison.md").read_text("utf-8")


def test_edited_original_card_refuses_the_comparison(archived):
    nfl, run_id = archived
    card = nfl / "runs" / run_id / "nfl_best_bets.json"
    card.write_bytes(card.read_bytes() + b" ")
    with pytest.raises(rb.RebuildRefused, match="sealed in its run manifest"):
        rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)


def test_cli(archived, capsys):
    from scripts import nfl_rebuild

    nfl, _ = archived
    assert nfl_rebuild.main(["admissible", "--nfl-dir", str(nfl)]) == 0
    assert f"ADMISSIBLE {SLATE}" in capsys.readouterr().out
    assert nfl_rebuild.main(["rebuild", "--nfl-dir", str(nfl), "--slate", SLATE,
                             "--rebuild-id", "cli1"]) == 0
    assert "wrote" in capsys.readouterr().out
    assert nfl_rebuild.main(["rebuild", "--nfl-dir", str(nfl), "--slate", "2026-10-11"]) == 2
    assert nfl_rebuild.main(["admissible", "--nfl-dir", str(nfl), "--slate", "2026-10-11"]) == 1
