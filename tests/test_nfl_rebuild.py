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
from outlier_nfl import rebuild as rb
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)


NETWORK_CALLS: list[Any] = []


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Real loaders (conftest's offline stubs undone) with every socket blocked and logged.

    Tests assert the rebuild makes no network call. The original live runs may try
    (and fail), just as a real live run without network would.
    """
    import socket

    from outlier_nfl import external, usage, weather

    NETWORK_CALLS.clear()

    def deny(_self: Any, address: Any, *_a: Any) -> None:
        NETWORK_CALLS.append(address)
        raise OSError("network disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", lambda address, *a, **k: deny(None, address))
    monkeypatch.setattr("outlier_nfl.pipeline.load_external_metrics",
                        external.load_external_metrics)
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", weather.load_slate_weather)
    monkeypatch.setattr("outlier_nfl.pipeline.load_usage", usage.load_usage)


# Real column layouts: nflverse games.csv (``boxscore_nflverse.GAMES_REQUIRED_COLUMNS``
# plus ``gametime``/``game_type``) and stats_player_week (``usage.py`` reads player_id,
# player_display_name, position, season, week, season_type, game_id, team, targets,
# target_share, carries, receiving_yards, rushing_yards).
GAMES_CSV = (
    "game_id,season,game_type,week,gameday,gametime,away_team,home_team,away_score,home_score\n"
    + "".join(f"2026_{w:02d}_BAL_KC,2026,REG,{w},2026-09-{13 + 7 * (w - 1) - 7:02d},13:00,"
              f"BAL,KC,20,24\n" for w in (1, 2, 3))
    + "2026_04_BAL_KC,2026,REG,4,2026-10-04,13:00,BAL,KC,,\n"
).encode()
STATS_CSV = (
    "player_id,player_display_name,position,season,week,season_type,game_id,team,targets,"
    "target_share,carries,receiving_yards,rushing_yards\n"
    + "".join(f"P1,Travis Kelce,TE,2026,{w},REG,2026_{w:02d}_BAL_KC,KC,8,0.25,0,70,0\n"
              for w in (1, 2, 3))
).encode()


def _archive_nflverse(tmp_path: Path, nfl: Path, fetched_at: str = "2026-10-04T12:00:00+00:00"):
    import hashlib

    from outlier_nfl.boxscore_nflverse import CacheMeta, _write_meta

    cache = tmp_path / "nflverse_cache"
    cache.mkdir(exist_ok=True)
    for name, data in (("stats_player_week_2026.csv", STATS_CSV), ("games.csv", GAMES_CSV)):
        (cache / name).write_bytes(data)
        _write_meta(cache / name, CacheMeta(url=f"https://x/{name}", fetched_at=fetched_at,
                                            sha256=hashlib.sha256(data).hexdigest(),
                                            size=len(data), rows=data.count(b"\n") - 1))
    assert not ar.record_nflverse(nfl, cache, [2026]).conflicts


def _tape(nfl: Path) -> Path:
    tape = nfl / "tape" / "prior_week.json"
    if not tape.exists():  # a test may plant its own tape first
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    return tape


def live_original(tmp_path: Path, client: Any = None, clock: str = PREGAME,
                  window: str | None = None) -> tuple[Path, str]:
    nfl = tmp_path / "NFL"
    _tape(nfl)
    now = parse_utc(clock)
    s = NflPipeline(client=client or snap.FrozenOutlierClient(FIXTURES_DIR), data_dir=tmp_path,
                    clock=lambda: now).run(date=SLATE, as_of_utc=clock, window=window,
                                           reports_dir=tmp_path / "reports")
    return nfl, s["run_id"]


def _originals(nfl: Path) -> dict[str, bytes]:
    return {p.relative_to(nfl).as_posix(): p.read_bytes() for p in sorted(nfl.rglob("*"))
            if p.is_file() and "versions" not in p.relative_to(nfl).parts}


@pytest.fixture
def archived(tmp_path):
    nfl, run_id = live_original(tmp_path)
    assert not ar.record_pointed_runs(nfl).conflicts
    _archive_nflverse(tmp_path, nfl)
    NETWORK_CALLS.clear()  # the original live run may try; the rebuild must not
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


# --- pinned nflverse, offline usage/weather ------------------------------------------

def test_nflverse_is_pinned_from_the_archive_with_original_names(archived):
    nfl, _ = archived
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    staged = nfl / "versions" / "r1" / "nflverse"
    assert sorted(p.name for p in staged.iterdir()) == [
        "games.csv", "games.csv.meta.json", "stats_player_week_2026.csv",
        "stats_player_week_2026.csv.meta.json"]
    entries = {e.path: e for e in ar.read_manifest(ar.manifest_path(nfl))[0]}
    for f in m["nflverse_files"]:
        e = entries[f["archive_path"]]
        assert (f["sha256"], f["fetched_at"]) == (e.sha256, e.fetched_at)
        assert ar.sha256_file(staged / f["name"]) == e.sha256
    assert m["evidence_sources"]["nflverse"] == "pinned"
    assert NETWORK_CALLS == []


def test_damaged_archive_copy_is_refused_before_use(archived):
    nfl, _ = archived
    [stats] = [e for e in ar.read_manifest(ar.manifest_path(nfl))[0]
               if e.path.endswith("stats_player_week_2026.csv")]
    (nfl / stats.path).write_bytes(STATS_CSV.replace(b"70", b"71"))
    with pytest.raises(rb.RebuildRefused):
        rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)


def test_newest_archived_nflverse_copy_is_used(archived, tmp_path):
    import hashlib

    from outlier_nfl.boxscore_nflverse import CacheMeta, _write_meta

    nfl, _ = archived
    newer = STATS_CSV + b"P2,Rashee Rice,WR,2026,3,REG,2026_03_BAL_KC,KC,6,0.2,0,50,0\n"
    cache = tmp_path / "nflverse_cache"
    (cache / "stats_player_week_2026.csv").write_bytes(newer)
    _write_meta(cache / "stats_player_week_2026.csv", CacheMeta(
        url="https://x/s", fetched_at="2026-10-05T12:00:00+00:00",
        sha256=hashlib.sha256(newer).hexdigest(), size=len(newer), rows=4))
    ar.record_nflverse(nfl, cache, [2026])
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    [f] = [f for f in m["nflverse_files"] if f["name"] == "stats_player_week_2026.csv"]
    assert f["sha256"] == hashlib.sha256(newer).hexdigest()


def _rebuilt_sources(nfl: Path, m: dict[str, Any]) -> dict[str, str]:
    run = json.loads((nfl / "versions" / m["rebuild_id"] / m["rebuilt_run_dir"] / "manifest.json")
                     .read_text("utf-8"))
    return {s["name"]: s["status"] for s in run["sources"]}


def test_usage_comes_only_from_the_pinned_weekly_file(archived):
    nfl, _ = archived
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    sources = _rebuilt_sources(nfl, m)
    assert sources["external:schedule"] == "AVAILABLE" and sources["usage"] == "AVAILABLE"
    assert all(v == "UNAVAILABLE" for k, v in sources.items()
               if k.startswith("external:") and k != "external:schedule")
    assert any("ffopportunity" in u for u in m["refused_sources"])
    assert NETWORK_CALLS == []


def test_without_archived_nflverse_usage_is_unavailable(tmp_path):
    nfl, _ = live_original(tmp_path)
    ar.record_pointed_runs(nfl)
    NETWORK_CALLS.clear()
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    sources = _rebuilt_sources(nfl, m)
    assert sources["external:schedule"] == "UNAVAILABLE"
    assert sources["usage"] == "UNAVAILABLE"
    assert m["evidence_sources"]["nflverse"] == "unavailable" and NETWORK_CALLS == []


def test_weather_is_never_fetched_only_archived(archived, monkeypatch):
    nfl, run_id = archived
    from outlier_nfl import weather

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("weather fetched during a rebuild")

    monkeypatch.setattr(weather, "_fetch_json", boom)
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", boom)
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    assert m["evidence_sources"]["weather"] == "archived" and NETWORK_CALLS == []
    out = nfl / "versions" / "r1" / m["rebuilt_run_dir"] / "nfl_weather.json"
    original = nfl / "runs" / run_id / "nfl_weather.json"
    assert (json.loads(out.read_text("utf-8"))["records"]
            == json.loads(original.read_text("utf-8"))["records"])


def test_missing_archived_weather_is_missing_and_labelled():
    labels = rb.context_labels({"nflverse": "pinned", "weather": "unavailable", "tape": "archived"})
    assert labels["evidence_changed:injury_weather"] == ["evidence_changed:weather"]
    assert labels["probability_changed"] == ["evidence_changed:nflverse"]
    inputs = rb.PinnedInputs({}, None)
    with pytest.raises(rb.UnavailableInRebuild):
        inputs.weather([])
    assert inputs.refused == ["weather"]


# --- archived tape role (#258) ----------------------------------------------------------

def _tape_source(nfl: Path, m: dict[str, Any]) -> dict[str, Any]:
    run = json.loads((nfl / "versions" / m["rebuild_id"] / m["rebuilt_run_dir"] / "manifest.json")
                     .read_text("utf-8"))
    return next(s for s in run["sources"] if s["name"] == "tape")


def test_archived_tape_is_used_and_matches_the_original(archived):
    nfl, run_id = archived
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    original = json.loads((nfl / "runs" / run_id / "manifest.json").read_text("utf-8"))
    orig_tape = next(s for s in original["sources"] if s["name"] == "tape")
    tape = _tape_source(nfl, m)
    assert m["evidence_sources"]["tape"] == "archived"
    assert (tape["status"], tape["sha256"]) == ("AVAILABLE", orig_tape["sha256"])
    comp = json.loads((nfl / "versions" / "r1" / "comparison.json").read_text("utf-8"))
    assert not any("evidence_changed:injury" in d["causes"] for d in comp["differences"])


def test_rebuild_without_archived_tape_labels_injury(tmp_path):
    # The original's tape lists Mahomes Out, so his picks' injury pillar CONTRADICTS.
    # The tape is then rewritten, so it can't be archived; the rebuild has no tape
    # and the pillar becomes MISSING. That is a real difference, labelled injury.
    tape_body = snap.frozen_tape(FIXTURES_DIR)
    tape_body["inactive"] = {"KC": ["Patrick Mahomes"]}
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    tape.parent.mkdir(parents=True)
    tape.write_text(json.dumps(tape_body), encoding="utf-8")
    nfl, _ = live_original(tmp_path)
    tape.write_text(tape.read_text("utf-8") + " ", encoding="utf-8")  # rewritten after the run
    result = ar.record_pointed_runs(nfl)
    assert result.notes and ar.TAPE_ROLE not in {e.role for e in result.recorded}
    _archive_nflverse(tmp_path, nfl)
    m = rb.rebuild_slate(nfl, SLATE, rebuild_id="r1", clock=lambda: NOW)
    assert m["evidence_sources"]["tape"] == "unavailable"
    assert _tape_source(nfl, m)["status"] != "AVAILABLE"
    comp = json.loads((nfl / "versions" / "r1" / "comparison.json").read_text("utf-8"))
    changed = [d for d in comp["differences"] if "evidence_changed:injury_weather" in d["causes"]]
    assert changed, comp["cause_counts"]  # a real difference: the original had the tape
    assert all("evidence_changed:injury" in d["causes"] for d in changed)


def test_two_windows_sharing_one_tape_both_rebuild_with_it(tmp_path):
    """The full-day run and the early-window run read the same shared prior_week.json.
    The archive stores one copy (keyed by sha256, so its entry names the first run
    recorded), and both slates' rebuilds must use it. The frozen client can't serve a
    complete late-window props feed (that run is PARTIAL), so the second window is
    the full day."""
    nfl, full = live_original(tmp_path, clock=PREGAME)
    _, early = live_original(tmp_path, window="early", clock="2026-10-04T15:05:00+00:00")
    assert not ar.record_pointed_runs(nfl).conflicts
    _archive_nflverse(tmp_path, nfl)
    tapes = [e for e in ar.read_manifest(ar.manifest_path(nfl))[0] if e.role == ar.TAPE_ROLE]
    assert len({e.path for e in tapes}) == 1
    verdicts = {v.slate: v for v in rb.admissibility(nfl)}
    assert set(verdicts) == {SLATE, f"{SLATE}_early"}
    for slate, run_id in ((SLATE, full), (f"{SLATE}_early", early)):
        v = verdicts[slate]
        assert v.admissible and v.run_id == run_id, v.reasons
        assert ar.TAPE_ROLE in {e.role for e in v.entries}, slate
        m = rb.rebuild_slate(nfl, slate, rebuild_id=f"r-{slate}", clock=lambda: NOW)
        assert m["evidence_sources"]["tape"] == "archived"
        assert _tape_source(nfl, m)["status"] == "AVAILABLE"
