"""CLI wiring for the Phase 2 steps (backfill build, ratings, run, backtest) and ESPN helpers."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError

import pytest

import outlier_nba.cli as cli
from outlier_nba import team_run
from outlier_nba.ingest import espn
from tests.nba_synth import synth_history

FIX = Path(__file__).parent / "fixtures" / "nba"


def _json(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _seed_cache(root: Path) -> espn.EspnCache:
    cache = espn.EspnCache(root)
    cache.put_scoreboard("20170101", _json("espn_scoreboard_20170101.json"))
    cache.put_summary("400899374", _json("espn_summary_400899374.json"))
    return cache


def _fast_params(monkeypatch) -> None:
    monkeypatch.setattr(team_run, "load_params", lambda: (team_run.TeamParams(lam=20.0), "t"))


def test_backfill_build_installs_and_reports_coverage(tmp_path, capsys) -> None:
    _seed_cache(tmp_path / "cache")
    store = tmp_path / "out" / "nba_games.sqlite3"
    code = cli.main(
        [
            "backfill", "--stage", "build", "--cache-dir", str(tmp_path / "cache"),
            "--store", str(store), "--seasons", "2016-17:2016-17",
        ]
    )  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and store.exists()
    assert out["games"] == 3 and out["with_box"] == 1 and out["integrity"] == "ok"
    assert out["seasons"]["2016-17"]["type2"]["games"] == 3


def test_backfill_network_stage_stops_cleanly_when_blocked(tmp_path, monkeypatch, capsys) -> None:
    def blocked(*a, **k):
        raise espn.BlockedError("HTTP 403")

    monkeypatch.setattr(espn, "run_fetch", blocked)
    code = cli.main(["backfill", "--stage", "scoreboard", "--cache-dir", str(tmp_path)])
    assert code == 4 and "blocked" in capsys.readouterr().out


def test_backfill_network_stage_prints_the_request_counts(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(espn, "run_fetch", lambda *a, **k: {"requested": 3, "todo": 3})
    assert cli.main(["backfill", "--stage", "summary", "--cache-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"requested": 3, "todo": 3}


def test_ratings_command_writes_table_and_state(tmp_path, monkeypatch, capsys) -> None:
    games = synth_history()
    monkeypatch.setattr(team_run, "load_store_games", lambda store: games)
    _fast_params(monkeypatch)
    code = cli.main(
        ["ratings", "--date", "2026-10-21", "--no-refresh", "--out-dir", str(tmp_path / "m")]
    )
    assert code == 0
    table = (tmp_path / "m" / "team_ratings_2026-10-21.csv").read_text(encoding="utf-8")
    assert len(table.strip().splitlines()) == 31  # header + 30 teams
    state = json.loads((tmp_path / "m" / "team_model_state_2026-10-21.json").read_text())
    assert state["season"] == "2026-27" and state["noise"]["sigma_e"] > 0
    assert json.loads(capsys.readouterr().out)["params_source"] == "t"


def test_ratings_command_exits_three_when_results_are_stale(tmp_path, monkeypatch, capsys) -> None:
    old = [g for g in synth_history() if g.season != "2026-27" or g.start_utc.day <= 9]
    monkeypatch.setattr(team_run, "load_store_games", lambda store: old)
    _fast_params(monkeypatch)
    args = ["ratings", "--date", "2026-10-21", "--no-refresh", "--out-dir", str(tmp_path)]
    assert cli.main(args) == 3
    assert "team_state_unavailable" in capsys.readouterr().out


def test_ratings_refresh_rebuilds_and_installs_the_store(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(espn, "refresh_recent", lambda cache, today: calls.append("refresh") or {})
    monkeypatch.setattr(espn, "build_store", lambda cache, db: calls.append("build") or {})
    monkeypatch.setattr(espn, "install_store", lambda src, dst: calls.append("install") or {})
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    _fast_params(monkeypatch)
    args = ["ratings", "--date", "2026-10-21", "--cache-dir", str(tmp_path / "c")]
    args += ["--store", str(tmp_path / "s.sqlite3"), "--out-dir", str(tmp_path / "o")]
    cli.main(args)
    assert calls == ["refresh", "build", "install"]


def test_ratings_refresh_survives_a_block(tmp_path, monkeypatch, capsys) -> None:
    def blocked(cache, today):
        raise espn.BlockedError("HTTP 429")

    monkeypatch.setattr(espn, "refresh_recent", blocked)
    monkeypatch.setattr(espn, "build_store", lambda cache, db: {})
    monkeypatch.setattr(espn, "install_store", lambda src, dst: {})
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    _fast_params(monkeypatch)
    args = ["ratings", "--date", "2026-10-21", "--cache-dir", str(tmp_path / "c")]
    args += ["--store", str(tmp_path / "s"), "--out-dir", str(tmp_path / "o")]
    assert cli.main(args) == 0
    assert "blocked" in capsys.readouterr().out


def test_ratings_refresh_keeps_the_old_store_when_the_rebuild_has_less_history(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(espn, "refresh_recent", lambda cache, today: {})
    monkeypatch.setattr(espn, "build_store", lambda cache, db: {})

    def refuse(src, dst):
        raise espn.StoreRegressionError("refusing to replace the installed store with less history")

    monkeypatch.setattr(espn, "install_store", refuse)
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    _fast_params(monkeypatch)
    args = ["ratings", "--date", "2026-10-21", "--cache-dir", str(tmp_path / "c")]
    args += ["--store", str(tmp_path / "s"), "--out-dir", str(tmp_path / "o")]
    assert cli.main(args) == 0  # still fits from the installed store
    assert "store_not_replaced" in capsys.readouterr().out


def test_backfill_build_exits_five_when_it_would_shrink_the_store(tmp_path, monkeypatch, capsys):
    _seed_cache(tmp_path / "cache")

    def refuse(src, dst):
        raise espn.StoreRegressionError("less history")

    monkeypatch.setattr(espn, "install_store", refuse)
    args = ["backfill", "--stage", "build", "--cache-dir", str(tmp_path / "cache")]
    args += ["--store", str(tmp_path / "s.sqlite3")]
    assert cli.main(args) == 5
    assert "store_not_replaced" in capsys.readouterr().out


def test_run_command_passes_arguments_and_returns_the_exit_code(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake(client, **kwargs):
        seen.update(kwargs)
        return cli.PipelineResult("r9", "OK", 0, True, None, [])

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(team_run, "run_team_shadow", fake)
    code = cli.main(["run", "--date", "2026-10-21", "--nba-dir", str(tmp_path), "--draws", "500"])
    assert code == 0 and seen["slate_date"] == "2026-10-21" and seen["n_draws"] == 500
    assert json.loads(capsys.readouterr().out)["run_id"] == "r9"


def test_run_command_without_auth_exits_one(monkeypatch, capsys) -> None:
    def boom():
        raise RuntimeError("no session")

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", boom)
    assert cli.main(["run"]) == 1
    assert "auth_required" in capsys.readouterr().out


def test_backtest_command_writes_report_and_optionally_the_tuned_config(
    tmp_path, monkeypatch
) -> None:
    from outlier_nba.track import backtest

    seen = {}

    def fake(games, **kw):
        seen.update(kw)
        return {"params": {"lam": 33.0}, "scored_seasons": ["2018-19"], "groups": {}}

    cfg = tmp_path / "cfg.json"
    monkeypatch.setattr(backtest, "run_backtest", fake)
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    monkeypatch.setattr(team_run, "MODEL_CONFIG_PATH", cfg)
    monkeypatch.setattr(espn, "coverage_report", lambda store: {"seasons": {}})
    out = tmp_path / "r.json"
    code = cli.main(["backtest", "--out", str(out), "--write-config", "--draws", "300"])
    assert code == 0 and json.loads(out.read_text())["coverage"] == {"seasons": {}}
    assert json.loads(cfg.read_text())["params"]["lam"] == 33.0
    assert seen["tune_season"] == "2017-18" and seen["n_draws"] == 300


def test_load_params_reads_the_tuned_file_or_flags_defaults(tmp_path) -> None:
    _params, source = team_run.load_params(tmp_path / "missing.json")
    assert source == "UNTUNED_DEFAULTS"
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"params": {"lam": 77.0, "bogus": 1}}), encoding="utf-8")
    params, source = team_run.load_params(path)
    assert params.lam == 77.0 and source.startswith("tuned:")


# ----------------------------------------------------------------------- espn helpers


def test_http_fetch_stops_on_403_and_429(monkeypatch) -> None:
    def deny(code):
        def opener(request, timeout=0):
            raise HTTPError(request.full_url, code, "no", {}, io.BytesIO(b""))

        return opener

    for code in (403, 429):
        monkeypatch.setattr(espn, "urlopen", deny(code))
        with pytest.raises(espn.BlockedError):
            espn.http_fetch(espn.ESPN_BASE + "/x?y=1")


def test_http_fetch_returns_json_and_sends_the_repo_user_agent(monkeypatch) -> None:
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout=0):
        seen["ua"] = request.get_header("User-agent")
        return Resp(b'{"events": []}')

    monkeypatch.setattr(espn, "urlopen", opener)
    assert espn.http_fetch(espn.ESPN_BASE + "/x") == {"events": []}
    assert seen["ua"] == espn.USER_AGENT == "outlier-results-collector/1.0"


def test_http_fetch_gives_up_after_retries(monkeypatch) -> None:
    monkeypatch.setattr(espn.time, "sleep", lambda s: None)

    def broken(request, timeout=0):
        raise OSError("reset")

    monkeypatch.setattr(espn, "urlopen", broken)
    with pytest.raises(espn.EspnError, match="request failed"):
        espn.http_fetch(espn.ESPN_BASE + "/x", retries=1)


def test_refresh_recent_refetches_days_and_only_fetches_missing_boxes(tmp_path) -> None:
    cache = _seed_cache(tmp_path)  # 20170101 cached, one of its three boxes cached
    urls: list[str] = []
    board = _json("espn_scoreboard_20170101.json")

    def fetch(url: str) -> dict:
        urls.append(url)
        if "summary" in url:
            return _json("espn_summary_400899374.json")
        return board if "dates=20170101" in url else {"events": []}

    th = espn.Throttle(burst=5, gap=0, pause=0, sleep=lambda s: None)
    out = espn.refresh_recent(cache, date(2017, 1, 2), days_back=1, fetch=fetch, throttle=th)
    receipt = cache.read_receipt()
    assert receipt["final_games_by_season"] == {"2016-17": 3}
    assert receipt["covered_days"][-1] == "20170102"
    assert out["scoreboards"] == 2  # 20170101 re-fetched although it was cached
    assert out["summaries"] == 2  # the two finals with no cached box
    assert sum("scoreboard" in u for u in urls) == 2


def test_run_fetch_handles_both_stages_and_rejects_unknown(tmp_path) -> None:
    cache = espn.EspnCache(tmp_path)
    th = espn.Throttle(burst=100, gap=0, pause=0, sleep=lambda s: None)

    def empty(url: str) -> dict:
        return {"events": []}

    out = espn.run_fetch("scoreboard", ["2016-17"], cache, fetch=empty, throttle=th)
    assert out["requested"] == out["todo"] == len(espn.season_dates("2016-17"))
    again = espn.run_fetch("scoreboard", ["2016-17"], cache, fetch=empty, throttle=th)
    assert again["todo"] == 0
    summary = espn.run_fetch("summary", ["2016-17"], cache, fetch=empty, throttle=th)
    assert summary["todo"] == 0
    with pytest.raises(ValueError):
        espn.run_fetch("bogus", ["2016-17"], cache)


def test_run_fetch_summary_stage_fetches_pending_boxes(tmp_path) -> None:
    cache = espn.EspnCache(tmp_path)
    cache.put_scoreboard("20170101", _json("espn_scoreboard_20170101.json"))
    th = espn.Throttle(burst=100, gap=0, pause=0, sleep=lambda s: None)
    out = espn.run_fetch(
        "summary", ["2016-17"], cache, fetch=lambda u: _json("espn_summary_400899374.json"),
        throttle=th,
    )  # fmt: skip
    assert out == {"requested": 3, "todo": 3}


def test_seasons_between_is_inclusive_and_rejects_reversed_ranges() -> None:
    assert espn.seasons_between("2016-17", "2018-19") == ["2016-17", "2017-18", "2018-19"]
    with pytest.raises(ValueError):
        espn.seasons_between("2018-19", "2016-17")


def test_corrupt_cache_file_is_a_miss_not_a_crash(tmp_path) -> None:
    cache = espn.EspnCache(tmp_path)
    cache.put_scoreboard("20170101", {"events": []})
    path = tmp_path / "scoreboard" / "20170101.json.gz"
    path.write_bytes(path.read_bytes()[:10])  # truncated, as a cloud-sync hiccup would leave it
    assert cache.get_scoreboard("20170101") is None
    with pytest.raises(ValueError):
        cache.get_scoreboard("../evil")


def test_coverage_report_does_not_count_the_cup_final_as_a_regular_season_game(tmp_path) -> None:
    cache = _seed_cache(tmp_path / "c")
    board = _json("espn_scoreboard_20170101.json")
    board["events"][0]["competitions"][0]["notes"] = [
        {"type": "event", "headline": "NBA In-Season Tournament Championship"}
    ]
    cache.put_scoreboard("20170101", board)
    db = tmp_path / "s.sqlite3"
    espn.build_store(cache, db)
    season = espn.coverage_report(db)["seasons"]["2016-17"]
    assert season["cup_championship_games"] == 1
    assert season["regular_missing"] == 1230 - 2


def test_coverage_report_flags_missing_regular_season_games(tmp_path) -> None:
    cache = _seed_cache(tmp_path / "c")
    db = tmp_path / "s.sqlite3"
    espn.build_store(cache, db)
    rep = espn.coverage_report(db)
    season = rep["seasons"]["2016-17"]
    assert season["regular_expected"] == 1230 and season["regular_missing"] == 1227
    assert rep["gap_kinds"]["MISSING_BOX"] == 2


def test_http_fetch_retries_a_truncated_body(monkeypatch) -> None:
    from http.client import IncompleteRead

    monkeypatch.setattr(espn.time, "sleep", lambda s: None)
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(request, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise IncompleteRead(b"partial")
        return Resp(b'{"ok": 1}')

    monkeypatch.setattr(espn, "urlopen", opener)
    assert espn.http_fetch(espn.ESPN_BASE + "/x") == {"ok": 1}
    assert len(calls) == 2


def test_missing_box_score_is_cached_as_a_gap_not_a_crash(tmp_path) -> None:
    cache = espn.EspnCache(tmp_path)

    def gone(url: str) -> dict:
        raise espn.NotFoundError("404")

    th = espn.Throttle(burst=5, gap=0, pause=0, sleep=lambda s: None)
    assert espn.fetch_summaries(["g1"], cache, gone, th) == 1
    assert cache.get_summary("g1") is not None
    assert espn.fetch_summaries(["g1"], cache, gone, th) == 0
    assert espn.parse_summary_box(cache.get_summary("g1")) == {}


def test_http_fetch_refuses_urls_outside_the_espn_endpoint() -> None:
    with pytest.raises(espn.EspnError, match="refusing"):
        espn.http_fetch("https://stats.nba.com/stats/x")
    with pytest.raises(espn.EspnError, match="refusing"):
        espn.http_fetch("file:///etc/passwd")


def _bt_args(tmp_path, *extra):
    return ["backtest", "--out", str(tmp_path / "r.json"), "--no-tune", *extra]


def test_sbro_backtest_fails_when_no_closing_line_game_matched(tmp_path, monkeypatch, capsys):
    from outlier_nba.ingest import sbro
    from outlier_nba.track import backtest

    called = []
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    monkeypatch.setattr(
        sbro, "load_market",
        lambda seasons, games: ({}, {"seasons": {s: {"cached": False} for s in seasons}}),
    )  # fmt: skip
    monkeypatch.setattr(backtest, "run_backtest", lambda *a, **k: called.append(1) or {})
    assert cli.main(_bt_args(tmp_path, "--sbro", "--allow-partial")) == 6
    assert "closing_lines_unavailable" in capsys.readouterr().out
    assert not called and not (tmp_path / "r.json").exists()  # no report claiming a comparison


def test_sbro_backtest_needs_enough_games_per_season_unless_partial_is_allowed(
    tmp_path, monkeypatch, capsys
):
    from outlier_nba.ingest import sbro
    from outlier_nba.track import backtest

    report = {"seasons": {"2018-19": {"cached": True, "matched": 30}}}
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    monkeypatch.setattr(sbro, "load_market", lambda seasons, games: ({"g": object()}, report))
    monkeypatch.setattr(espn, "coverage_report", lambda store: {})
    monkeypatch.setattr(
        backtest, "run_backtest", lambda *a, **k: {"params": {}, "scored_seasons": []}
    )
    base = _bt_args(tmp_path, "--sbro", "--seasons", "2018-19:2018-19")
    assert cli.main(base) == 6
    assert "only 30 matched games" in capsys.readouterr().out
    assert cli.main([*base, "--allow-partial"]) == 0
    saved = json.loads((tmp_path / "r.json").read_text())
    assert saved["sbro_join"]["coverage_problems"]  # the shortfall travels with the report
    assert (tmp_path / "sbro_manifest.json").exists()


def test_closing_csv_backtest_fails_on_an_empty_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(team_run, "load_store_games", lambda store: synth_history())
    empty = tmp_path / "c.csv"
    empty.write_text("game_id,season,date,home,away,ml_home,ml_away\n", encoding="utf-8")
    assert cli.main(_bt_args(tmp_path, "--closing-csv", str(empty))) == 6
    assert "no usable rows" in capsys.readouterr().out
