"""Run bundles and publication (F27 consistency, F11 dated publication)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl import run_writer
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_writer import RunWriter

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-09-13"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, **kw):
    return NflPipeline(data_dir=tmp_path).run(
        date=SLATE, offline_fixtures_dir=FIXTURES_DIR, reports_dir=tmp_path / "reports", **kw
    )


# ---------------------------------------------------------------------------
# F27: one staged bundle per run, pointers written last
# ---------------------------------------------------------------------------

def test_run_bundle_manifest_hashes_match_and_pointer_written_last(tmp_path, monkeypatch):
    order: list[str] = []
    real = run_writer._atomic_write_bytes

    def record(dest: Path, data: bytes) -> None:
        order.append(dest.name)
        real(dest, data)

    monkeypatch.setattr(run_writer, "_atomic_write_bytes", record)
    summary = _run(tmp_path)

    run_dir = Path(summary["run_dir"])
    assert run_dir.parent == tmp_path / "NFL" / "runs" and run_dir.name == summary["run_id"]
    manifest_bytes = (run_dir / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["run_id"] == summary["run_id"]
    assert manifest["context"]["slate_date"] == SLATE and manifest["context"]["mode"] == "fixture"
    names = {a["name"] for a in manifest["artifacts"]}
    assert {"summary.json", "nfl_best_bets.json", "nfl_matchup_scripts.json",
            "raw/schedule.json", "raw/player_props.json"} <= names
    for art in manifest["artifacts"]:
        assert _sha(run_dir / art["name"]) == art["sha256"], art["name"]
        for dest in art["published_to"]:
            assert _sha(Path(dest)) == art["sha256"], dest

    pointers = [n for n in order if n.startswith("nfl_run_pointer_")]
    assert pointers and order[-len(pointers):] == pointers  # nothing published after a pointer
    pointer = json.loads((tmp_path / "NFL" / "normalized" / f"nfl_run_pointer_{SLATE}.json")
                         .read_text())
    assert pointer["run_id"] == summary["run_id"]
    assert pointer["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert not list((tmp_path / "NFL" / "runs").glob(".staging-*"))


def test_failed_run_publishes_nothing(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("roster index exploded")

    monkeypatch.setattr(nfl_pipeline, "build_team_roster_index", boom)
    with pytest.raises(RuntimeError):
        _run(tmp_path)
    normalized = tmp_path / "NFL" / "normalized"
    assert not list(normalized.glob("*.json"))  # no half-written slate
    assert not [p for p in (tmp_path / "NFL" / "runs").iterdir() if not p.name.startswith(".")]


def test_snapshot_rows_carry_the_run_id(tmp_path):
    summary = _run(tmp_path)
    rows = [json.loads(line) for line in
            next((tmp_path / "NFL" / "snapshots").glob("*.jsonl")).read_text().splitlines()]
    assert rows and {r["run_id"] for r in rows} == {summary["run_id"]}


def test_run_writer_refuses_reuse(tmp_path):
    writer = RunWriter(tmp_path, "r1")
    writer.stage_json("a.json", {"x": 1}, publish=[tmp_path / "out" / "a.json"])
    writer.commit({}, pointers=[tmp_path / "out" / "ptr.json"])
    assert json.loads((tmp_path / "out" / "a.json").read_text()) == {"x": 1}
    with pytest.raises(RuntimeError):
        writer.commit({})
    with pytest.raises(FileExistsError):
        RunWriter(tmp_path, "r1")



def test_bundle_rename_retries_a_transient_windows_lock(tmp_path, monkeypatch):
    """The staging→runs/<id> folder rename survives a briefly locked file (Windows)."""
    import outlier_nfl.utils as nfl_utils

    monkeypatch.setattr(nfl_utils.time, "sleep", lambda _s: None)
    writer = RunWriter(tmp_path, "r-lock")
    writer.stage_json("a.json", {"x": 1}, publish=[tmp_path / "out" / "a.json"])
    real_replace = Path.replace
    calls: list[Path] = []

    def flaky_replace(self: Path, target):  # first folder rename hits a sharing violation
        if self == writer.stage_dir and not calls:
            calls.append(self)
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    writer.commit({}, pointers=[tmp_path / "out" / "ptr.json"])
    assert calls == [writer.stage_dir]
    assert (writer.run_dir / "manifest.json").is_file()
    assert not writer.stage_dir.exists()
    assert json.loads((tmp_path / "out" / "a.json").read_text()) == {"x": 1}

# ---------------------------------------------------------------------------
# F11: one suffix per run; window and after-kickoff runs never replace originals
# ---------------------------------------------------------------------------

def _published(tmp_path: Path) -> dict[str, str]:
    """sha256 of every published (non-bundle, non-snapshot) file under the data and reports dirs."""
    out: dict[str, str] = {}
    for root in (tmp_path / "NFL" / "normalized", tmp_path / "NFL" / "exports",
                 tmp_path / "reports"):
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    out[p.relative_to(tmp_path).as_posix()] = _sha(p)
    return out


def test_window_run_preserves_bare_date_bytes(tmp_path):
    _run(tmp_path, generate_game_script=True)
    before = _published(tmp_path)
    scripts = tmp_path / "NFL" / "normalized" / f"nfl_matchup_scripts_{SLATE}.json"
    assert json.loads(scripts.read_text())["count"] == 3
    summary = _run(tmp_path, window="1pm", generate_game_script=True)
    assert summary["publication"] == "published"
    after = _published(tmp_path)
    for name, digest in before.items():
        assert after[name] == digest, f"window run rewrote {name}"
    assert json.loads(scripts.read_text())["count"] == 3


def test_window_run_writes_only_suffixed_files(tmp_path):
    _run(tmp_path, generate_game_script=True)
    before = _published(tmp_path)
    _run(tmp_path, window="1pm", generate_game_script=True)
    written = {n for n, d in _published(tmp_path).items() if before.get(n) != d}
    assert written, "window run published nothing"
    assert all(f"{SLATE}_1pm" in n for n in written), sorted(written)
    assert f"NFL/normalized/nfl_run_pointer_{SLATE}_1pm.json" in written
    assert f"NFL/normalized/nfl_best_bets_{SLATE}_1pm.json" in written


def _week4(tmp_path: Path, as_of: str, *, clock: str | None = None, **kw):
    """Week-4 replay slate (KC@BAL 17:00Z) run at wall clock ``clock`` (default: as_of)."""
    from outlier_nfl.run_context import parse_utc
    from scripts import nfl_snapshot_diff as snap

    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock or as_of)
    pipeline = NflPipeline(client=snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                           clock=lambda: now)
    return pipeline.run(date="2026-10-04", as_of_utc=as_of, reports_dir=tmp_path / "reports",
                        **kw)


def test_after_kickoff_run_is_bundle_only(tmp_path):
    """KC@BAL kicks off 17:00Z; an 18:00Z full-slate rerun must not replace the pregame card."""
    pregame = _week4(tmp_path, "2026-10-04T16:00:00+00:00")
    assert pregame["publication"] == "published"
    before = _published(tmp_path)
    late = _week4(tmp_path, "2026-10-04T18:00:00+00:00")
    assert late["publication"] == "bundle_only" and late["run_mode"] == "retrospective"
    assert "first kickoff" in late["publication_reason"]
    assert _published(tmp_path) == before  # dated, latest, exports and reports untouched
    run_dir = Path(late["run_dir"])
    assert (run_dir / "nfl_best_bets.json").exists() and (run_dir / "summary.json").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["publication"] == "bundle_only"
    assert all(a["published_to"] == [] for a in manifest["artifacts"])
    pointer = json.loads((tmp_path / "NFL" / "normalized" / "nfl_run_pointer_2026-10-04.json")
                         .read_text())
    assert pointer["run_id"] == pregame["run_id"]


def _assert_bundle_only(tmp_path: Path, run: dict, before: dict[str, str], pregame_id: str):
    assert run["publication"] == "bundle_only"
    assert _published(tmp_path) == before  # pregame originals untouched
    manifest = json.loads((Path(run["run_dir"]) / "manifest.json").read_text())
    assert manifest["publication"] == "bundle_only"
    assert all(a["published_to"] == [] for a in manifest["artifacts"])
    pointer = json.loads((tmp_path / "NFL" / "normalized" / "nfl_run_pointer_2026-10-04.json")
                         .read_text())
    assert pointer["run_id"] == pregame_id
    return manifest


def test_rerun_after_kickoff_with_pregame_as_of_is_bundle_only(tmp_path):
    """Monday rerun of Sunday's slate with --as-of Sunday 12:00Z: it *runs* after the
    17:00Z kickoff, so it is retrospective whatever --as-of says, and its live-fetched
    odds/props postdate the claimed cutoff."""
    pregame = _week4(tmp_path, "2026-10-04T16:00:00+00:00")
    assert pregame["publication"] == "published" and pregame["run_mode"] == "live"
    before = _published(tmp_path)
    monday = _week4(tmp_path, "2026-10-04T12:00:00+00:00", clock="2026-10-05T14:00:00+00:00")
    assert monday["run_mode"] == "retrospective" and monday["replay"] is True
    assert "run started 2026-10-05T14:00:00+00:00" in monday["publication_reason"]
    assert "postdate as_of" in monday["publication_reason"]
    manifest = _assert_bundle_only(tmp_path, monday, before, pregame["run_id"])
    ctx = manifest["context"]
    assert ctx["as_of_utc"] == "2026-10-04T12:00:00+00:00"
    assert ctx["run_started_utc"] == "2026-10-05T14:00:00+00:00"
    assert ctx["mode"] == "retrospective" and ctx["replay"] is True
    assert ctx["live_inputs_postdate_as_of"] is True


def test_past_as_of_replay_before_kickoff_is_recorded_and_bundle_only(tmp_path):
    """Before kickoff, but --as-of hours behind the wall clock: a recorded replay."""
    pregame = _week4(tmp_path, "2026-10-04T15:00:00+00:00")
    before = _published(tmp_path)
    replay = _week4(tmp_path, "2026-10-04T12:00:00+00:00", clock="2026-10-04T16:30:00+00:00")
    assert replay["run_mode"] == "replay" and replay["replay"] is True
    assert replay["publication_reason"].startswith("replay: as_of 2026-10-04T12:00:00+00:00")
    manifest = _assert_bundle_only(tmp_path, replay, before, pregame["run_id"])
    assert manifest["context"]["run_started_utc"] == "2026-10-04T16:30:00+00:00"
    assert manifest["context"]["live_inputs_postdate_as_of"] is True


@pytest.mark.parametrize(
    ("as_of", "clock", "fixture", "mode", "replay"),
    [
        ("2026-10-04T16:00:00+00:00", "2026-10-04T16:10:00+00:00", False, "live", False),
        ("2026-10-04T16:00:00+00:00", "2026-10-04T16:30:00+00:00", False, "replay", True),
        ("2026-10-04T12:00:00+00:00", "2026-10-05T14:00:00+00:00", False, "retrospective", True),
        ("2026-10-04T18:00:00+00:00", "2026-10-04T18:00:00+00:00", False, "retrospective", False),
        ("2026-10-04T12:00:00+00:00", "2026-10-05T14:00:00+00:00", True, "fixture", False),
    ],
)
def test_run_mode_uses_wall_clock_and_as_of(as_of, clock, fixture, mode, replay):
    from outlier_nfl.run_context import make_run_context, parse_utc

    ctx = make_run_context(
        slate_date="2026-10-04", window=None, as_of_utc=parse_utc(as_of), fixture=fixture,
        slate_events=[{"scheduledTime": "2026-10-04T17:00:00Z"}],
        run_started_utc=parse_utc(clock),
    )
    assert (ctx.mode, ctx.replay) == (mode, replay)
    assert ctx.publishes is (mode in ("live", "fixture"))

def test_weekly_merges_bundle_only_card(tmp_path):
    from datetime import date

    from outlier_nfl.weekly import run_week
    from scripts import nfl_snapshot_diff as snap

    client = snap.FrozenOutlierClient(FIXTURES_DIR)
    pipeline = NflPipeline(client=client, data_dir=tmp_path)
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True, exist_ok=True)
    tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    # No as_of: the weekly runner runs "now", i.e. after these replayed kickoffs.
    out = run_week(pipeline, date(2026, 10, 4), events=client.fetch_schedule()["events"],
                   reports_dir=tmp_path / "reports", run_stamp="0900")
    assert out["dates"] == ["2026-10-04"]
    assert not (tmp_path / "NFL" / "normalized" / "nfl_best_bets_2026-10-04.json").exists()
    week_card = json.loads(next((tmp_path / "NFL" / "normalized")
                                .glob("nfl_best_bets_week_*.json")).read_text())
    assert week_card["counts"] == out["counts"] and sum(out["counts"].values()) > 0
