"""F15: nflverse box-score cache must refresh, validate and never regress."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError

import pytest

from outlier_nfl import boxscore_nflverse as nv
from outlier_nfl.boxscore import BoxScoreError

GAMES_HEADER = "game_id,season,week,gameday,away_team,home_team,away_score,home_score\n"
STATS_HEADER = "game_id,player_display_name,passing_yards,rushing_yards\n"


def _games(*rows: str) -> bytes:
    return (GAMES_HEADER + "".join(r + "\n" for r in rows)).encode()


def _stats(*rows: str) -> bytes:
    return (STATS_HEADER + "".join(r + "\n" for r in rows)).encode()


WEEK1_GAME = "2026_01_CLE_TB,2026,1,2026-09-13,CLE,TB,23,19"
WEEK2_GAME = "2026_02_KC_BUF,2026,2,2026-09-20,KC,BUF,27,24"
WEEK1_STAT = "2026_01_CLE_TB,Deshaun Watson,250,20"
WEEK1_STAT_CORRECTED = "2026_01_CLE_TB,Deshaun Watson,262,20"
WEEK2_STAT = "2026_02_KC_BUF,Patrick Mahomes,301,12"


class _Resp:
    def __init__(self, body: bytes, content_length: int | None = None) -> None:
        self._body = body
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeNet:
    """Serve queued responses per URL; an Exception entry is raised instead."""

    def __init__(self) -> None:
        self.queue: dict[str, list[Any]] = {}
        self.calls: list[str] = []

    def serve(self, url: str, *items: Any) -> None:
        self.queue.setdefault(url, []).extend(items)

    def __call__(self, request: Any, timeout: int = 60) -> _Resp:
        url = request.full_url
        self.calls.append(url)
        if not self.queue.get(url):
            raise URLError(f"no fake response queued for {url}")
        item = self.queue[url].pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, _Resp):
            return item
        return _Resp(item)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


STATS_URL = nv.NFLVERSE_STATS_WEEK_URL.format(season=2026)
GAMES_URL = nv.NFLVERSE_GAMES_URL


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(nv, "_urlopen", fake)
    monkeypatch.setattr(nv, "_sleep", lambda _s: None)
    monkeypatch.delenv("OUTLIER_NFLVERSE_MAX_AGE_HOURS", raising=False)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(nv, "_utcnow", c)
    return c


def _load_week(tmp_path: Path, **kw: Any) -> list[Any]:
    return nv.load_nflverse_events(season=2026, cache_dir=tmp_path, **kw)


def test_early_season_cache_picks_up_later_week_after_ttl(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)))
    assert [e.provider_event_id for e in _load_week(tmp_path)] == ["2026_01_CLE_TB"]

    clock.now += timedelta(days=7)
    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT, WEEK2_STAT)))
    events = nv.load_nflverse_events(season=2026, event_date=date(2026, 9, 20), cache_dir=tmp_path)
    assert [e.provider_event_id for e in events] == ["2026_02_KC_BUF"]
    assert events[0].players["PATRICKMAHOMES"]["PASSING:YDS"] == 301.0


def test_fresh_cache_is_reused_without_network(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)))
    _load_week(tmp_path)
    calls = len(net.calls)
    clock.now += timedelta(hours=1)
    _load_week(tmp_path)
    assert len(net.calls) == calls


def test_stat_correction_arrives_on_explicit_refresh(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME), _games(WEEK1_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)), gzip.compress(_stats(WEEK1_STAT_CORRECTED)))
    assert _load_week(tmp_path)[0].players["DESHAUNWATSON"]["PASSING:YDS"] == 250.0
    events = _load_week(tmp_path, refresh=True)
    assert events[0].players["DESHAUNWATSON"]["PASSING:YDS"] == 262.0


def test_truncated_gzip_never_replaces_valid_cache(tmp_path, net, clock):
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)))
    nv.ensure_week_stats_csv(2026, cache_dir=tmp_path)
    good = (tmp_path / "stats_player_week_2026.csv").read_bytes()
    full = gzip.compress(_stats(WEEK1_STAT, WEEK2_STAT))
    net.serve(STATS_URL, full[: len(full) // 2])
    path = nv.ensure_week_stats_csv(2026, cache_dir=tmp_path, refresh=True)
    assert path.read_bytes() == good


def test_truncated_gzip_with_no_cache_raises(tmp_path, net, clock):
    full = gzip.compress(_stats(WEEK1_STAT))
    net.serve(STATS_URL, full[:-8])
    with pytest.raises(BoxScoreError, match="gzip"):
        nv.ensure_week_stats_csv(2026, cache_dir=tmp_path)
    assert not (tmp_path / "stats_player_week_2026.csv").exists()


def test_short_body_vs_content_length_is_rejected(tmp_path, net, clock):
    body = _games(WEEK1_GAME, WEEK2_GAME)
    net.serve(GAMES_URL, *[_Resp(body[:-20], content_length=len(body))] * nv.DEFAULT_RETRIES)
    with pytest.raises(BoxScoreError, match="truncated"):
        nv.ensure_games_csv(cache_dir=tmp_path)
    assert not (tmp_path / "games.csv").exists()


def test_truncated_plain_csv_row_is_rejected(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME, "2026_02_KC_BUF,2026,2"))
    with pytest.raises(BoxScoreError, match="malformed row"):
        nv.ensure_games_csv(cache_dir=tmp_path)


def test_refresh_with_fewer_rows_keeps_cached_copy(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME), _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path)
    path = nv.ensure_games_csv(cache_dir=tmp_path, refresh=True)
    assert path.read_bytes() == _games(WEEK1_GAME, WEEK2_GAME)
    with pytest.raises(BoxScoreError, match="fewer than cached"):
        net.serve(GAMES_URL, _games(WEEK1_GAME))
        nv.ensure_games_csv(cache_dir=tmp_path, refresh=True, require_fresh=True)


def test_schema_change_is_rejected(tmp_path, net, clock):
    net.serve(GAMES_URL, b"game_id,season\n2026_01_CLE_TB,2026\n")
    with pytest.raises(BoxScoreError, match="missing required columns"):
        nv.ensure_games_csv(cache_dir=tmp_path)


def test_refresh_failure_falls_back_to_validated_copy(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path)
    clock.now += timedelta(days=2)
    net.serve(GAMES_URL, *[HTTPError(GAMES_URL, 503, "busy", {}, None)] * nv.DEFAULT_RETRIES)  # type: ignore[arg-type]
    path = nv.ensure_games_csv(cache_dir=tmp_path)
    assert path.read_bytes() == _games(WEEK1_GAME)
    net.serve(GAMES_URL, *[HTTPError(GAMES_URL, 503, "busy", {}, None)] * nv.DEFAULT_RETRIES)  # type: ignore[arg-type]
    with pytest.raises(BoxScoreError, match="503"):
        nv.ensure_games_csv(cache_dir=tmp_path, require_fresh=True)


def test_tampered_cache_is_not_a_fallback(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    path = nv.ensure_games_csv(cache_dir=tmp_path)
    path.write_bytes(_games(WEEK1_GAME, WEEK2_GAME))
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    with pytest.raises(BoxScoreError):
        nv.ensure_games_csv(cache_dir=tmp_path)


def test_retry_is_bounded_and_skips_non_retryable(tmp_path, net, clock):
    net.serve(GAMES_URL, HTTPError(GAMES_URL, 503, "busy", {}, None), _games(WEEK1_GAME))  # type: ignore[arg-type]
    nv.ensure_games_csv(cache_dir=tmp_path)
    assert net.calls.count(GAMES_URL) == 2

    net.calls.clear()
    net.serve(STATS_URL, HTTPError(STATS_URL, 404, "gone", {}, None), gzip.compress(_stats(WEEK1_STAT)))  # type: ignore[arg-type]
    with pytest.raises(BoxScoreError, match="404"):
        nv.ensure_week_stats_csv(2026, cache_dir=tmp_path)
    assert net.calls == [STATS_URL]

    net.calls.clear()
    net.queue.clear()
    net.serve(STATS_URL, *[URLError("down")] * (nv.DEFAULT_RETRIES + 2))
    with pytest.raises(BoxScoreError):
        nv.ensure_week_stats_csv(2026, cache_dir=tmp_path, refresh=True)
    assert len(net.calls) == nv.DEFAULT_RETRIES


def test_repeat_refresh_is_idempotent(tmp_path, net, clock):
    net.serve(GAMES_URL, _games(WEEK1_GAME), _games(WEEK1_GAME))
    path = nv.ensure_games_csv(cache_dir=tmp_path)
    first_meta = nv.read_cache_meta(path)
    clock.now += timedelta(hours=1)
    nv.ensure_games_csv(cache_dir=tmp_path, refresh=True)
    second_meta = nv.read_cache_meta(path)
    assert first_meta is not None and second_meta is not None
    assert second_meta.sha256 == first_meta.sha256
    assert second_meta.rows == first_meta.rows == 1
    assert second_meta.fetched_at > first_meta.fetched_at
    assert path.read_bytes() == _games(WEEK1_GAME)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["games.csv", "games.csv.meta.json"]


def test_legacy_cache_without_sidecar_is_refreshed(tmp_path, net, clock):
    legacy = tmp_path / "games.csv"
    legacy.write_bytes(_games(WEEK1_GAME))
    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME))
    assert nv.ensure_games_csv(cache_dir=tmp_path).read_bytes() == _games(WEEK1_GAME, WEEK2_GAME)
    meta = json.loads((tmp_path / "games.csv.meta.json").read_text())
    assert meta["rows"] == 2


def test_legacy_cache_used_offline_only_if_schema_valid(tmp_path, net, clock):
    legacy = tmp_path / "games.csv"
    legacy.write_bytes(_games(WEEK1_GAME))
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    assert nv.ensure_games_csv(cache_dir=tmp_path) == legacy

    legacy.write_bytes(b"game_id\n")
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    with pytest.raises(BoxScoreError):
        nv.ensure_games_csv(cache_dir=tmp_path)


def test_max_age_env(monkeypatch):
    monkeypatch.setenv("OUTLIER_NFLVERSE_MAX_AGE_HOURS", "0.5")
    assert nv.max_age_from_env() == timedelta(minutes=30)
    monkeypatch.setenv("OUTLIER_NFLVERSE_MAX_AGE_HOURS", "soon")
    with pytest.raises(BoxScoreError):
        nv.max_age_from_env()


def test_shrink_override_accepts_upstream_row_removal(tmp_path, net, clock, monkeypatch):
    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME), _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path)
    nv.drain_cache_events()
    path = nv.ensure_games_csv(cache_dir=tmp_path, refresh=True, allow_shrink=True)
    assert path.read_bytes() == _games(WEEK1_GAME)
    [event] = nv.drain_cache_events()
    assert event["status"] == "shrunk" and "fewer than previous 2" in event["reason"]

    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME), _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path, refresh=True)
    monkeypatch.setenv("OUTLIER_NFLVERSE_ALLOW_SHRINK", "1")
    nv.ensure_games_csv(cache_dir=tmp_path, refresh=True)
    assert path.read_bytes() == _games(WEEK1_GAME)


def test_row_count_guard_is_per_season_file(tmp_path, net, clock):
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT, WEEK2_STAT)))
    nv.ensure_week_stats_csv(2026, cache_dir=tmp_path)
    url_2027 = nv.NFLVERSE_STATS_WEEK_URL.format(season=2027)
    net.serve(url_2027, gzip.compress(_stats("2027_01_CLE_TB,Deshaun Watson,100,0")))
    path = nv.ensure_week_stats_csv(2027, cache_dir=tmp_path)
    meta = nv.read_cache_meta(path)
    assert path.name == "stats_player_week_2027.csv" and meta is not None and meta.rows == 1


def test_settle_cli_surfaces_cache_fallback(tmp_path, net, clock, capsys):
    from outlier_nfl import settle

    preds = Path(__file__).parent / "fixtures" / "nfl" / "settle" / "predictions_tier1.json"
    cache = tmp_path / "cache"
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)))
    nv.ensure_games_csv(cache_dir=cache)
    nv.ensure_week_stats_csv(2026, cache_dir=cache)
    nv.drain_cache_events()
    net.queue.clear()
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    out_json = tmp_path / "settle.json"
    rc = settle.main(
        [
            "--predictions", str(preds),
            "--provider", "nflverse",
            "--season", "2026",
            "--week", "1",
            "--nflverse-cache", str(cache),
            "--nflverse-refresh",
            "--out-json", str(out_json),
        ]
    )
    assert rc == 0
    assert "WARNING: nflverse `games.csv` refresh failed" in capsys.readouterr().out
    statuses = {e["file"]: e["status"] for e in json.loads(out_json.read_text())["nflverse_cache"]}
    assert statuses["games.csv"] == "fallback"



class _CrashAfterFirstWrite:
    """Let one cache write land, then crash, whichever file the code writes first."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.writes = 0

    def __call__(self, path: Path, data: bytes) -> None:
        self.writes += 1
        if self.writes > 1:
            raise KeyboardInterrupt("simulated crash between cache writes")
        self.real(path, data)


def _crash_mid_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, net: FakeNet, body: bytes) -> None:
    real = nv._atomic_write_bytes
    monkeypatch.setattr(nv, "_atomic_write_bytes", _CrashAfterFirstWrite(real))
    net.serve(GAMES_URL, body)
    with pytest.raises(KeyboardInterrupt):
        nv.ensure_games_csv(cache_dir=tmp_path, refresh=True)
    monkeypatch.setattr(nv, "_atomic_write_bytes", real)


def test_crash_between_csv_and_sidecar_writes_still_falls_back_offline(tmp_path, net, clock, monkeypatch):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path)
    nv.drain_cache_events()
    _crash_mid_refresh(tmp_path, monkeypatch, net, _games(WEEK1_GAME, WEEK2_GAME))

    net.calls.clear()
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    path = nv.ensure_games_csv(cache_dir=tmp_path)
    assert net.calls, "a copy the sidecar does not describe must not count as fresh"
    assert path.read_bytes() == _games(WEEK1_GAME)
    assert [e["status"] for e in nv.drain_cache_events()] == ["fallback"]


def test_crash_between_writes_heals_on_next_online_refresh(tmp_path, net, clock, monkeypatch):
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    nv.ensure_games_csv(cache_dir=tmp_path)
    _crash_mid_refresh(tmp_path, monkeypatch, net, _games(WEEK1_GAME, WEEK2_GAME))

    net.serve(GAMES_URL, _games(WEEK1_GAME, WEEK2_GAME))
    path = nv.ensure_games_csv(cache_dir=tmp_path)
    meta = nv.read_cache_meta(path)
    assert path.read_bytes() == _games(WEEK1_GAME, WEEK2_GAME)
    assert meta is not None and meta.rows == 2
    assert meta.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_crash_replacing_legacy_cache_keeps_it_as_fallback(tmp_path, net, clock, monkeypatch):
    (tmp_path / "games.csv").write_bytes(_games(WEEK1_GAME))
    _crash_mid_refresh(tmp_path, monkeypatch, net, _games(WEEK1_GAME, WEEK2_GAME))
    net.serve(GAMES_URL, *[URLError("down")] * nv.DEFAULT_RETRIES)
    assert nv.ensure_games_csv(cache_dir=tmp_path).read_bytes() == _games(WEEK1_GAME)


def _seed_cache(cache: Path, net: FakeNet) -> None:
    net.serve(GAMES_URL, _games(WEEK1_GAME))
    net.serve(STATS_URL, gzip.compress(_stats(WEEK1_STAT)))
    nv.ensure_games_csv(cache_dir=cache)
    nv.ensure_week_stats_csv(2026, cache_dir=cache)
    nv.drain_cache_events()
    net.calls.clear()
    net.queue.clear()


def test_pinned_cache_never_touches_network_even_when_stale(tmp_path, net, clock):
    _seed_cache(tmp_path, net)
    clock.now += timedelta(days=30)
    events = _load_week(tmp_path, pinned=True)
    assert net.calls == []
    assert events[0].players["DESHAUNWATSON"]["PASSING:YDS"] == 250.0
    drained = nv.drain_cache_events()
    assert {e["status"] for e in drained} == {"pinned"}
    games = next(e for e in drained if e["file"] == "games.csv")
    assert games["sha256"] == hashlib.sha256(_games(WEEK1_GAME)).hexdigest()


def test_pinned_cache_refuses_file_without_sidecar(tmp_path, net, clock):
    (tmp_path / "games.csv").write_bytes(_games(WEEK1_GAME))
    with pytest.raises(BoxScoreError, match="no fetch metadata"):
        nv.ensure_games_csv(cache_dir=tmp_path, pinned=True)
    assert net.calls == []


def test_pinned_cache_refuses_hash_mismatch(tmp_path, net, clock):
    _seed_cache(tmp_path, net)
    (tmp_path / "games.csv").write_bytes(_games(WEEK1_GAME, WEEK2_GAME))
    with pytest.raises(BoxScoreError, match="does not match its sidecar"):
        nv.ensure_games_csv(cache_dir=tmp_path, pinned=True)
    assert net.calls == []


def test_pinned_cache_refuses_missing_file(tmp_path, net, clock):
    with pytest.raises(BoxScoreError, match="pinned cache file missing"):
        nv.ensure_week_stats_csv(2026, cache_dir=tmp_path, pinned=True)
    assert net.calls == []


def _settle_pinned(tmp_path: Path, cache: Path, out: Path, *extra: str) -> int:
    from outlier_nfl import settle

    preds = Path(__file__).parent / "fixtures" / "nfl" / "settle" / "predictions_tier1.json"
    return settle.main(
        [
            "--predictions", str(preds),
            "--provider", "nflverse",
            "--season", "2026",
            "--week", "1",
            *extra,
            "--out-json", str(out),
        ]
    )


def test_settle_cli_pinned_regrade_is_reproducible(tmp_path, net, clock):
    cache = tmp_path / "cache"
    _seed_cache(cache, net)
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    outs = []
    for i in range(2):
        clock.now += timedelta(days=10)
        out = tmp_path / f"settle{i}.json"
        assert _settle_pinned(tmp_path, cache, out, "--nflverse-cache", str(cache), "--nflverse-pinned") == 0
        payload = json.loads(out.read_text())
        payload.pop("generated_at", None)
        outs.append(payload)
    assert net.calls == []
    assert outs[0] == outs[1]
    assert {e["status"] for e in outs[0]["nflverse_cache"]} == {"pinned"}
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


@pytest.mark.parametrize(
    "extra",
    [
        ("--nflverse-pinned",),
        ("--nflverse-cache", "CACHE", "--nflverse-pinned", "--nflverse-refresh"),
        ("--nflverse-cache", "CACHE", "--nflverse-pinned", "--nflverse-allow-shrink"),
    ],
)
def test_settle_cli_pinned_rejects_unpinnable_combinations(tmp_path, net, clock, extra):
    cache = tmp_path / "cache"
    _seed_cache(cache, net)
    args = tuple(str(cache) if a == "CACHE" else a for a in extra)
    with pytest.raises(SystemExit, match="nflverse-pinned"):
        _settle_pinned(tmp_path, cache, tmp_path / "out.json", *args)
    assert net.calls == []


@pytest.mark.parametrize("spelling", ["relative", "symlink", "env"])
def test_pinned_refuses_shared_default_cache_under_any_spelling(
    tmp_path, net, clock, monkeypatch, spelling
):
    home = tmp_path / "home"
    shared = home / ".cache" / "outlier_nflverse"
    shared.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OUTLIER_NFLVERSE_CACHE", raising=False)
    if spelling == "env":
        monkeypatch.setenv("OUTLIER_NFLVERSE_CACHE", str(shared))
    _seed_cache(shared, net)
    if spelling == "relative":
        monkeypatch.chdir(home)
        given = Path(".cache") / ".." / ".cache" / "outlier_nflverse"
    elif spelling == "symlink":
        given = tmp_path / "alias"
        given.symlink_to(shared, target_is_directory=True)
    else:
        given = shared
    with pytest.raises(BoxScoreError, match="shared default"):
        nv.ensure_games_csv(cache_dir=given, pinned=True)
    with pytest.raises(SystemExit, match="shared default"):
        _settle_pinned(tmp_path, given, tmp_path / "out.json", "--nflverse-cache", str(given),
                       "--nflverse-pinned")
    assert net.calls == []


def test_pinned_parses_the_bytes_it_verified(tmp_path, net, clock, monkeypatch):
    _seed_cache(tmp_path, net)
    games = tmp_path / "games.csv"
    real_read = Path.read_bytes
    reads: dict[str, int] = {}

    def swap_after_first_read(self: Path) -> bytes:
        data = real_read(self)
        if self == games:
            reads["n"] = reads.get("n", 0) + 1
            if reads["n"] == 1:
                games.write_bytes(_games(WEEK1_GAME, WEEK2_GAME))
        return data

    monkeypatch.setattr(Path, "read_bytes", swap_after_first_read)
    with pytest.raises(BoxScoreError, match="does not match its sidecar"):
        _load_week(tmp_path, pinned=True)
