"""CLI wiring for ``slate``: feeds + store in, report out. Offline; Outlier is never touched."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import outlier_nba.cli as cli
from outlier_nba import data_dirs, team_run
from outlier_nba.ingest import espn, espn_slate
from outlier_nba.ratings.model import TeamParams
from outlier_scrapers import paths
from tests.nba_synth import synth_history

FIX = Path(__file__).parent / "fixtures" / "nba"
NOW = datetime(2026, 11, 25, 23, 0, tzinfo=UTC)  # an hour before the 19:30 ET fixture tip


def _fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network, no Outlier, a synthetic store, a fast model and a fixed clock."""
    monkeypatch.setattr(
        espn_slate,
        "fetch_feeds",
        lambda day, fetch=None: (
            _fixture("espn_slate_scoreboard.json"),
            _fixture("espn_slate_injuries.json"),
        ),
    )
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    monkeypatch.setattr(
        team_run, "load_params", lambda: (TeamParams(lam=20.0, tau=1e6, carry=0.5), "t")
    )
    monkeypatch.setattr(team_run, "load_feed_receipt", lambda cache_root=None: None)
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW)

    def forbidden(*a, **k):
        raise AssertionError("slate must not touch the Outlier API")

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", forbidden)
    monkeypatch.delenv(data_dirs.DATA_DIR_ENV, raising=False)


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "slate", "--date", "2026-10-21", "--no-refresh", "--draws", "2000",
        "--out-dir", str(tmp_path / "out"), "--store", str(tmp_path / "s.sqlite3"),
        "--cache-dir", str(tmp_path / "c"), *extra,
    ]  # fmt: skip


def test_slate_writes_the_report_json_and_raw_feeds(tmp_path, capsys) -> None:
    assert cli.main(_args(tmp_path)) == 0
    out = tmp_path / "out"
    names = {p.name for p in out.iterdir()}
    assert names == {
        "NBA_Master_Report_2026-10-21_1800ET.md", "nba_master_2026-10-21_1800ET.json",
        "espn_scoreboard_2026-10-21_1800ET.json", "espn_injuries_2026-10-21_1800ET.json",
    }  # fmt: skip
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["games"] == 2 and summary["candidates"] == 6  # only CLE@NYK carries lines
    assert summary["report"].endswith("NBA_Master_Report_2026-10-21_1800ET.md")
    data = json.loads((out / "nba_master_2026-10-21_1800ET.json").read_text(encoding="utf-8"))
    assert data["n_draws"] == 2000 and data["day"] == "2026-10-21"
    text = (out / "NBA_Master_Report_2026-10-21_1800ET.md").read_text(encoding="utf-8")
    assert "## I. Final NBA Slate Verdict" in text and "CLE@NYK" in text


def test_the_in_progress_game_is_projected_but_never_a_candidate(tmp_path) -> None:
    cli.main(_args(tmp_path))
    data = json.loads(
        (tmp_path / "out" / "nba_master_2026-10-21_1800ET.json").read_text(encoding="utf-8")
    )
    assert {g["away"] + "@" + g["home"] for g in data["board"]} == {"CLE@NYK", "MIA@PHI"}
    assert {c["game"] for c in data["candidates"]} == {"CLE@NYK"}


def test_a_blocked_espn_feed_exits_four_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    def blocked(day, fetch=None):
        raise espn.BlockedError("HTTP 429")

    monkeypatch.setattr(espn_slate, "fetch_feeds", blocked)
    assert cli.main(_args(tmp_path)) == 4
    assert "blocked" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_an_unusable_team_state_exits_three(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(team_run, "load_store_games", lambda store: [])
    assert cli.main(_args(tmp_path)) == 3
    assert "team_state_unavailable" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_a_day_with_no_games_is_a_clean_empty_run(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(espn_slate, "fetch_feeds", lambda day, fetch=None: ({"events": []}, {}))
    assert cli.main(_args(tmp_path)) == 0
    assert "no_games" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_refresh_runs_by_default_and_not_with_no_refresh(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(espn, "refresh_recent", lambda cache, today: calls.append("refresh") or {})
    monkeypatch.setattr(espn, "build_store", lambda cache, db: calls.append("build") or {})
    monkeypatch.setattr(espn, "install_store", lambda src, dst: calls.append("install") or {})
    args = [a for a in _args(tmp_path) if a != "--no-refresh"]
    assert cli.main(args) == 0
    assert calls == ["refresh", "build", "install"]
    calls.clear()
    assert cli.main(_args(tmp_path)) == 0
    assert calls == []


def test_a_default_out_dir_inside_the_repo_is_refused(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(data_dirs.DATA_DIR_ENV, str(paths.PROJECT_ROOT / "data" / "x"))
    args = [a for a in _args(tmp_path) if a not in ("--out-dir", str(tmp_path / "out"))]
    assert cli.main(args) == 2
    assert "refused" in capsys.readouterr().out


def test_a_rerun_keeps_the_earlier_snapshot_instead_of_overwriting_it(
    tmp_path, monkeypatch
) -> None:
    cli.main(_args(tmp_path))
    monkeypatch.setattr(cli, "_utc_now", lambda: NOW.replace(hour=23, minute=30))  # 18:30 ET
    cli.main(_args(tmp_path))
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(names) == 8
    assert any("1800ET" in n for n in names) and any("1830ET" in n for n in names)


def test_an_empty_injury_feed_rejects_every_candidate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        espn_slate,
        "fetch_feeds",
        lambda day, fetch=None: (_fixture("espn_slate_scoreboard.json"), {"injuries": []}),
    )
    cli.main(_args(tmp_path))
    path = tmp_path / "out" / "nba_master_2026-10-21_1800ET.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["candidates"] and all(c["units"] == 0 for c in data["candidates"])
    assert all(any("injury feed" in f for f in c["fails"]) for c in data["candidates"])


def test_a_missing_store_names_the_flag_for_the_other_commands_store(
    tmp_path, monkeypatch, capsys
) -> None:
    def missing(store):
        raise team_run.TeamStateError(f"games store not found at {store}; run `backfill`")

    monkeypatch.setattr(team_run, "load_store_games", missing)
    assert cli.main(_args(tmp_path)) == 3
    out = capsys.readouterr().out
    assert "--store" in out and "backfill" in out and "ratings" in out
