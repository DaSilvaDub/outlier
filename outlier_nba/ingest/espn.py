"""Historical NBA results from ESPN's public JSON (plan Phase 2 step 8, decision D4).

Only ``site.api.espn.com`` is used: ``cdn.nba.com`` answers 403 and ``stats.nba.com``
is off limits (docs/nba/data-sources.md). Fetching is deliberately polite -- a handful
of requests per burst, a pause between bursts, the repo's own User-Agent, a hard stop
on 403/429 -- and every response is cached so a rerun only asks for what is missing.

Pipeline: ``fetch_scoreboards`` / ``fetch_summaries`` fill the cache (network),
``build_store`` turns the cache into a SQLite store (pure), ``install_store`` copies it
into place and re-counts it from a fresh process (the repo sits behind a cloud-sync
filter that has truncated SQLite writes before).
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sqlite3
import multiprocessing
import time
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import structlog

from outlier_nba.identity import resolve_team

logger = structlog.get_logger("outlier_nba.espn")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
# Same honest User-Agent as outlier_scrapers.results. A longer one carrying a contact
# address was refused (403) by the CDN in front of ESPN on 2026-10-21.
USER_AGENT = "outlier-results-collector/1.0"

# ESPN season.type: 2 regular season, 3 postseason, 5 play-in. Preseason (1) and
# all-star/other types are skipped and counted.
KEPT_SEASON_TYPES = frozenset({2, 3, 5})
CUP_PATTERN = re.compile(r"emirates nba cup|in-season tournament|nba cup", re.I)

# Games per regular season, for the gap report (2019-20 and 2020-21 were shortened).
EXPECTED_REGULAR_GAMES = {
    "2016-17": 1230,
    "2017-18": 1230,
    "2018-19": 1230,
    "2019-20": 1059,
    "2020-21": 1080,
}
DEFAULT_EXPECTED_REGULAR = 1230


class EspnError(RuntimeError):
    pass


class NotFoundError(EspnError):
    """404: ESPN has no such resource (a box score that was never published)."""


class BlockedError(EspnError):
    """403/429: stop the whole run rather than retry into a block."""


def season_label(espn_year: int) -> str:
    """ESPN labels a season by its end year (2017 -> ``2016-17``)."""
    return f"{espn_year - 1}-{str(espn_year)[-2:]}"


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(os.environ.get("OUTLIER_NBA_CACHE", Path(base) / "outlier_nba")) / "espn_cache"


# --------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class GameRow:
    game_id: str
    season: str
    season_type: int
    start_utc: datetime
    home: str
    away: str
    home_pts: int
    away_pts: int
    periods: int
    ot_periods: int
    neutral: bool
    attendance: int | None
    note: str
    home_fga: int | None = None
    home_fta: int | None = None
    home_reb: int | None = None
    home_oreb: int | None = None
    home_tov: int | None = None
    away_fga: int | None = None
    away_fta: int | None = None
    away_reb: int | None = None
    away_oreb: int | None = None
    away_tov: int | None = None

    @property
    def is_cup(self) -> bool:
        return bool(CUP_PATTERN.search(self.note))


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _stats(competitor: Mapping[str, Any]) -> dict[str, int | None]:
    by_name = {s.get("name"): s.get("displayValue") for s in competitor.get("statistics") or []}
    return {
        "fga": _int(by_name.get("fieldGoalsAttempted")),
        "fta": _int(by_name.get("freeThrowsAttempted")),
        "reb": _int(by_name.get("rebounds")),
    }


def _gap(kind: str, key: str, detail: str = "") -> dict[str, str]:
    return {"kind": kind, "key": key, "detail": detail}


def _parse_event(event: Mapping[str, Any]) -> tuple[GameRow | None, dict[str, str] | None]:
    game_id = str(event.get("id") or "")
    season = event.get("season") or {}
    season_type = _int(season.get("type"))
    if season_type not in KEPT_SEASON_TYPES:
        return None, _gap("SKIPPED_SEASON_TYPE", game_id, f"type={season_type}")
    comp = (event.get("competitions") or [{}])[0]
    status = ((comp.get("status") or event.get("status") or {}).get("type") or {}).get("name")
    if status != "STATUS_FINAL":
        return None, _gap("NOT_FINAL", game_id, str(status))
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if set(sides) != {"home", "away"}:
        return None, _gap("BAD_COMPETITORS", game_id)
    home = resolve_team((sides["home"].get("team") or {}).get("abbreviation"))
    away = resolve_team((sides["away"].get("team") or {}).get("abbreviation"))
    if home is None or away is None:
        names = [(sides[s].get("team") or {}).get("abbreviation") for s in ("home", "away")]
        return None, _gap("UNRESOLVED_TEAM", game_id, str(names))
    hp, ap = _int(sides["home"].get("score")), _int(sides["away"].get("score"))
    year = _int(season.get("year"))
    start = event.get("date") or comp.get("date")
    if hp is None or ap is None or year is None or not start:
        return None, _gap("INCOMPLETE_EVENT", game_id)
    lines = sides["home"].get("linescores") or []
    periods = len(lines) if lines else (_int((comp.get("status") or {}).get("period")) or 4)
    notes = " | ".join(str(n.get("headline") or "") for n in comp.get("notes") or [])
    hs, as_ = _stats(sides["home"]), _stats(sides["away"])
    row = GameRow(
        game_id=game_id,
        season=season_label(year),
        season_type=int(season_type),
        start_utc=datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(UTC),
        home=home,
        away=away,
        home_pts=hp,
        away_pts=ap,
        periods=max(periods, 4),
        ot_periods=max(periods - 4, 0),
        neutral=bool(comp.get("neutralSite")),
        attendance=_int(comp.get("attendance")),
        note=notes,
        home_fga=hs["fga"],
        home_fta=hs["fta"],
        home_reb=hs["reb"],
        away_fga=as_["fga"],
        away_fta=as_["fta"],
        away_reb=as_["reb"],
    )
    return row, None


def parse_scoreboard(payload: Mapping[str, Any]) -> tuple[list[GameRow], list[dict[str, str]]]:
    games: list[GameRow] = []
    gaps: list[dict[str, str]] = []
    for event in payload.get("events") or []:
        row, gap = _parse_event(event)
        if row is not None:
            games.append(row)
        if gap is not None:
            gaps.append(gap)
    return games, gaps


def _split_made_att(value: Any) -> int | None:
    parts = str(value or "").split("-")
    return _int(parts[1]) if len(parts) == 2 else None


def parse_summary_box(payload: Mapping[str, Any]) -> dict[str, dict[str, int | None]]:
    """``{"home"|"away": {fga, fta, oreb, dreb, tov}}`` from a summary's team box score."""
    out: dict[str, dict[str, int | None]] = {}
    for team in (payload.get("boxscore") or {}).get("teams") or []:
        side = team.get("homeAway")
        if side not in ("home", "away"):
            continue
        by_name = {s.get("name"): s.get("displayValue") for s in team.get("statistics") or []}
        tov = by_name.get("totalTurnovers", by_name.get("turnovers"))
        out[side] = {
            "fga": _split_made_att(by_name.get("fieldGoalsMade-fieldGoalsAttempted")),
            "fta": _split_made_att(by_name.get("freeThrowsMade-freeThrowsAttempted")),
            "oreb": _int(by_name.get("offensiveRebounds")),
            "dreb": _int(by_name.get("defensiveRebounds")),
            "tov": _int(tov),
        }
    return out


# ------------------------------------------------------------------ fetch + cache


class Throttle:
    """At most ``burst`` requests ``gap`` seconds apart, then ``pause`` seconds of quiet."""

    def __init__(
        self,
        burst: int = 5,
        gap: float = 0.3,
        pause: float = 2.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.burst, self.gap, self.pause, self._sleep = burst, gap, pause, sleep
        self._n = 0

    def wait(self) -> None:
        self._n += 1
        self._sleep(self.pause if self._n % self.burst == 0 else self.gap)


def http_fetch(url: str, *, timeout: int = 30, retries: int = 2) -> dict[str, Any]:
    if urlsplit(url).scheme != "https" or not url.startswith(ESPN_BASE + "/"):
        # only ever talk to the one ESPN endpoint family, over https
        raise EspnError(f"refusing to fetch outside {ESPN_BASE}: {url.split('?')[0]}")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})  # noqa: S310
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # nosec B310 - https ESPN only, checked above
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise EspnError(f"non-object response from {url.split('?')[0]}")
            return payload
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise BlockedError(f"HTTP {exc.code} from {url.split('?')[0]}") from exc
            if exc.code == 404:
                raise NotFoundError(f"HTTP 404 from {url.split('?')[0]}") from exc
            last = exc
        except (OSError, ValueError, EspnError, HTTPException) as exc:  # IncompleteRead etc.
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise EspnError(f"request failed for {url.split('?')[0]}: {last}")


class EspnCache:
    """Gzip JSON cache: ``scoreboard/YYYYMMDD.json.gz`` and trimmed ``summary/<id>.json.gz``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, kind: str, key: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z_-]+", key):
            raise ValueError(f"unsafe cache key {key!r}")
        return self.root / kind / f"{key}.json.gz"

    def _get(self, kind: str, key: str) -> dict[str, Any] | None:
        path = self._path(kind, key)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, EOFError, ValueError, zlib.error):
            return None  # a truncated cache file is a miss, not an error
        return data if isinstance(data, dict) else None

    def _put(self, kind: str, key: str, payload: Mapping[str, Any]) -> None:
        path = self._path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)

    def get_scoreboard(self, day: str) -> dict[str, Any] | None:
        return self._get("scoreboard", day)

    def put_scoreboard(self, day: str, payload: Mapping[str, Any]) -> None:
        self._put("scoreboard", day, payload)

    def get_summary(self, game_id: str) -> dict[str, Any] | None:
        return self._get("summary", game_id)

    def put_summary(self, game_id: str, payload: Mapping[str, Any]) -> None:
        """Keep only the team box score: the full payload carries ~500 plays per game."""
        keep = {
            "boxscore": {"teams": (payload.get("boxscore") or {}).get("teams") or []},
            "header": {"id": (payload.get("header") or {}).get("id")},
        }
        self._put("summary", game_id, keep)

    def scoreboard_days(self) -> list[str]:
        folder = self.root / "scoreboard"
        return sorted(p.name.removesuffix(".json.gz") for p in folder.glob("*.json.gz"))

    def summary_ids(self) -> set[str]:
        folder = self.root / "summary"
        return {p.name.removesuffix(".json.gz") for p in folder.glob("*.json.gz")}


Fetcher = Callable[[str], dict[str, Any]]


def fetch_scoreboards(
    days: Iterable[str],
    cache: EspnCache,
    fetch: Fetcher,
    throttle: Throttle,
    *,
    refetch: bool = False,
) -> int:
    """Fetch every uncached ``YYYYMMDD`` (all of them with ``refetch``); returns requests made."""
    made = 0
    for day in days:
        if not refetch and cache.get_scoreboard(day) is not None:
            continue
        cache.put_scoreboard(day, fetch(f"{ESPN_BASE}/scoreboard?dates={day}"))
        made += 1
        throttle.wait()
    return made


def fetch_summaries(
    game_ids: Iterable[str], cache: EspnCache, fetch: Fetcher, throttle: Throttle
) -> int:
    made = 0
    for game_id in game_ids:
        if cache.get_summary(game_id) is not None:
            continue
        try:
            payload = fetch(f"{ESPN_BASE}/summary?event={game_id}")
        except NotFoundError:
            payload = {}  # cached as "no box score" so the gap is reported, not retried forever
        cache.put_summary(game_id, payload)
        made += 1
        throttle.wait()
    return made


def season_dates(season: str) -> list[str]:
    """Every calendar day that could hold a game (empty days are cached and cheap)."""
    start_year = int(season[:4])
    first, last = date(start_year, 10, 1), date(start_year + 1, 7, 15)
    if season == "2019-20":
        last = date(2020, 10, 15)  # the bubble finals ran into October
    if season == "2020-21":
        first = date(2020, 12, 1)
    days = (last - first).days
    return [(first + timedelta(days=i)).strftime("%Y%m%d") for i in range(days + 1)]


# ------------------------------------------------------------------------ store

SCHEMA = """
CREATE TABLE games (
    game_id TEXT PRIMARY KEY, season TEXT NOT NULL, season_type INTEGER NOT NULL,
    start_utc TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL,
    home_pts INTEGER NOT NULL, away_pts INTEGER NOT NULL,
    periods INTEGER NOT NULL, ot_periods INTEGER NOT NULL, neutral INTEGER NOT NULL,
    attendance INTEGER, note TEXT NOT NULL,
    home_fga INTEGER, home_fta INTEGER, home_reb INTEGER, home_oreb INTEGER, home_tov INTEGER,
    away_fga INTEGER, away_fta INTEGER, away_reb INTEGER, away_oreb INTEGER, away_tov INTEGER
);
CREATE INDEX games_start ON games (start_utc);
CREATE TABLE gaps (kind TEXT NOT NULL, key TEXT NOT NULL, detail TEXT NOT NULL);
"""
_GAME_COLS = [
    "game_id", "season", "season_type", "start_utc", "home", "away", "home_pts", "away_pts",
    "periods", "ot_periods", "neutral", "attendance", "note",
    "home_fga", "home_fta", "home_reb", "home_oreb", "home_tov",
    "away_fga", "away_fta", "away_reb", "away_oreb", "away_tov",
]  # fmt: skip


INSERT_GAME = (
    "INSERT INTO games (game_id, season, season_type, start_utc, home, away, home_pts, away_pts, "
    "periods, ot_periods, neutral, attendance, note, home_fga, home_fta, home_reb, home_oreb, "
    "home_tov, away_fga, away_fta, away_reb, away_oreb, away_tov) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
if INSERT_GAME.count("?") != len(_GAME_COLS):  # a schema edit must fail at import, not at insert
    raise RuntimeError("INSERT_GAME placeholders do not match _GAME_COLS")


def _merge_box(row: GameRow, box: Mapping[str, Mapping[str, int | None]]) -> GameRow:
    values: dict[str, Any] = {}
    for side in ("home", "away"):
        b = box.get(side) or {}
        values[f"{side}_oreb"] = b.get("oreb")
        values[f"{side}_tov"] = b.get("tov")
        for stat in ("fga", "fta"):  # the scoreboard value wins; the box fills a hole
            current = getattr(row, f"{side}_{stat}")
            values[f"{side}_{stat}"] = current if current is not None else b.get(stat)
    return GameRow(**{**row.__dict__, **values})


def build_store(cache: EspnCache, db_path: Path) -> dict[str, int]:
    """Rebuild ``db_path`` from the cache. Pure and deterministic: same cache, same store."""
    games: dict[str, GameRow] = {}
    gaps: list[dict[str, str]] = []
    for day in cache.scoreboard_days():
        payload = cache.get_scoreboard(day) or {}
        day_games, day_gaps = parse_scoreboard(payload)
        if not (payload.get("events") or []):
            gaps.append(_gap("EMPTY_DATE", day))
        gaps.extend(day_gaps)
        for g in day_games:
            games.setdefault(g.game_id, g)
    with_box = 0
    for game_id, row in list(games.items()):
        summary = cache.get_summary(game_id)
        box = parse_summary_box(summary) if summary else {}
        if box.get("home", {}).get("tov") is None or box.get("away", {}).get("tov") is None:
            gaps.append(_gap("MISSING_BOX", game_id))
            continue
        games[game_id] = _merge_box(row, box)
        with_box += 1
    ordered = sorted(games.values(), key=lambda g: (g.start_utc, g.game_id))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(INSERT_GAME, [_row_values(g) for g in ordered])
        conn.executemany(
            "INSERT INTO gaps VALUES (?, ?, ?)", [(g["kind"], g["key"], g["detail"]) for g in gaps]
        )
        conn.commit()
    finally:
        conn.close()
    return {"games": len(ordered), "with_box": with_box, "gaps": len(gaps)}


def _row_values(g: GameRow) -> tuple[Any, ...]:
    out = []
    for col in _GAME_COLS:
        v = getattr(g, col)
        out.append(
            v.isoformat() if isinstance(v, datetime) else int(v) if isinstance(v, bool) else v
        )
    return tuple(out)


def load_games(db_path: Path) -> list[GameRow]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM games ORDER BY start_utc, game_id").fetchall()
    finally:
        conn.close()
    games = []
    for r in rows:
        data = dict(r)
        data["start_utc"] = datetime.fromisoformat(data["start_utc"])
        data["neutral"] = bool(data["neutral"])
        games.append(GameRow(**data))
    return games


def store_counts(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        with_box = conn.execute("SELECT COUNT(*) FROM games WHERE home_tov IS NOT NULL").fetchone()[
            0
        ]
        gaps = conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
    finally:
        conn.close()
    return {"integrity": ok, "games": games, "with_box": with_box, "gaps": gaps}


def _count_in_fresh_interpreter(path_str: str) -> dict[str, Any]:
    """Worker run in a spawned interpreter: opens the file read-only and counts it."""
    return store_counts(Path(path_str))


def verify_store_fresh_process(path: Path) -> dict[str, Any]:
    """Count the store from a brand-new interpreter (no shared handles or page cache).

    A spawned worker (not a shell-out) so there is no command line to build or escape.
    """
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
        return pool.submit(_count_in_fresh_interpreter, path.as_posix()).result(timeout=300)


class StoreRegressionError(RuntimeError):
    """The new store has less history than the one it would replace."""


def season_counts(db_path: Path) -> dict[tuple[str, int], tuple[int, int]]:
    """``{(season, season_type): (games, games_with_box)}``."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT season, season_type, COUNT(*), SUM(home_tov IS NOT NULL) "
            "FROM games GROUP BY season, season_type"
        ).fetchall()
    finally:
        conn.close()
    return {(s, t): (n, b or 0) for s, t, n, b in rows}


def check_no_regression(new_db: Path, existing: Path) -> None:
    """Refuse a store that lost any season, game or box score the installed one has.

    A rebuild from an incomplete or cleared cache is readable and passes the integrity
    check, so coverage is compared explicitly. The installed store is left untouched.
    """
    if not existing.exists():
        return
    old, new = season_counts(existing), season_counts(new_db)
    lost = [
        f"{key}: games {old[key][0]}->{new.get(key, (0, 0))[0]}, "
        f"box {old[key][1]}->{new.get(key, (0, 0))[1]}"
        for key in sorted(old)
        if new.get(key, (0, 0))[0] < old[key][0] or new.get(key, (0, 0))[1] < old[key][1]
    ]
    if lost:
        raise StoreRegressionError(
            "refusing to replace the installed store with less history: " + "; ".join(lost[:6])
        )


def install_store(tmp_db: Path, dest: Path) -> dict[str, Any]:
    """Copy a locally built store into place, then re-count it; remove it on any mismatch.

    The installed store is only replaced by one that covers at least as much history.
    """
    check_no_regression(tmp_db, dest)
    expected = store_counts(tmp_db)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_suffix(dest.suffix + ".incoming")
    shutil.copyfile(tmp_db, staging)
    try:
        got = verify_store_fresh_process(staging)
        if got != expected or got["integrity"] != "ok":
            raise RuntimeError(f"count mismatch after copy: expected {expected}, got {got}")
        os.replace(staging, dest)
    finally:
        if staging.exists():
            staging.unlink()
    final = verify_store_fresh_process(dest)
    if final != expected:
        dest.unlink()
        raise RuntimeError(f"count mismatch after install: expected {expected}, got {final}")
    return final


def coverage_report(db_path: Path) -> dict[str, Any]:
    """Per-season game counts against the expected regular-season total, plus named gaps."""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        seasons: dict[str, Any] = {}
        for season, stype, n, ot, neutral, box in conn.execute(
            "SELECT season, season_type, COUNT(*), SUM(ot_periods>0), SUM(neutral), "
            "SUM(home_tov IS NOT NULL) FROM games GROUP BY season, season_type"
        ):
            entry = seasons.setdefault(season, {})
            entry[f"type{stype}"] = {"games": n, "ot": ot, "neutral": neutral, "with_box": box}
        cups = dict(
            conn.execute(
                "SELECT season, COUNT(*) FROM games WHERE note LIKE '%Tournament Championship%' "
                "OR note LIKE '%Cup Championship%' GROUP BY season"
            ).fetchall()
        )
        for season, entry in seasons.items():
            want = EXPECTED_REGULAR_GAMES.get(season, DEFAULT_EXPECTED_REGULAR)
            # ESPN files the NBA Cup final under the regular season; it is not one of the 1230
            have = (entry.get("type2") or {}).get("games", 0) - cups.get(season, 0)
            entry["cup_championship_games"] = cups.get(season, 0)
            entry["regular_expected"] = want
            entry["regular_missing"] = want - have
        gap_kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM gaps GROUP BY kind").fetchall())
    finally:
        conn.close()
    return {"seasons": seasons, "gap_kinds": gap_kinds}


def pending_summary_ids(cache: EspnCache, days: Iterable[str] | None = None) -> list[str]:
    """Final games (from cached scoreboards) whose box score is not cached yet."""
    have = cache.summary_ids()
    ids: list[str] = []
    for day in sorted(days) if days is not None else cache.scoreboard_days():
        day_games, _ = parse_scoreboard(cache.get_scoreboard(day) or {})
        ids.extend(g.game_id for g in day_games if g.game_id not in have)
    return ids


def seasons_between(first: str, last: str) -> list[str]:
    """``"2016-17", "2018-19"`` -> the three season labels, inclusive."""
    a, b = int(first[:4]), int(last[:4])
    if b < a:
        raise ValueError(f"season range runs backwards: {first}:{last}")
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(a, b + 1)]


def run_fetch(
    stage: str,
    seasons: Sequence[str],
    cache: EspnCache,
    *,
    fetch: Fetcher = http_fetch,
    throttle: Throttle | None = None,
) -> dict[str, int]:
    """One resumable network stage: ``scoreboard`` (all days) or ``summary`` (box scores)."""
    throttle = throttle or Throttle()
    if stage == "scoreboard":
        days = [d for s in seasons for d in season_dates(s)]
        todo = [d for d in days if cache.get_scoreboard(d) is None]
        logger.info("espn_scoreboard_todo", todo=len(todo), total=len(days))
        made = 0
        for i in range(0, len(todo), 50):  # chunked so progress is logged and a block loses little
            made += fetch_scoreboards(todo[i : i + 50], cache, fetch, throttle)
            logger.info("espn_scoreboard_progress", done=i + len(todo[i : i + 50]), of=len(todo))
        return {"requested": made, "todo": len(todo)}
    if stage == "summary":
        ids = pending_summary_ids(cache)
        logger.info("espn_summary_todo", todo=len(ids))
        made = 0
        for i in range(0, len(ids), 100):
            made += fetch_summaries(ids[i : i + 100], cache, fetch, throttle)
            logger.info("espn_summary_progress", done=i + len(ids[i : i + 100]), of=len(ids))
        return {"requested": made, "todo": len(ids)}
    raise ValueError(f"unknown stage {stage!r}")


def refresh_recent(
    cache: EspnCache,
    today: date,
    *,
    days_back: int = 4,
    fetch: Fetcher = http_fetch,
    throttle: Throttle | None = None,
) -> dict[str, int]:
    """Re-fetch the last few days (a cached "scheduled" game may since have finished)."""
    throttle = throttle or Throttle()
    days = [(today - timedelta(days=k)).strftime("%Y%m%d") for k in range(days_back, -1, -1)]
    boards = fetch_scoreboards(days, cache, fetch, throttle, refetch=True)
    boxes = fetch_summaries(pending_summary_ids(cache, days), cache, fetch, throttle)
    return {"scoreboards": boards, "summaries": boxes}
