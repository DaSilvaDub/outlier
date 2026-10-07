"""nflverse (GitHub releases CSV) box-score provider for shadow settle.

Downloads (or reads cached) ``stats_player_week_{season}.csv`` + ``games.csv``
from nflverse (player stats from the nflverse-data releases, the schedule from
the nfldata repo) and maps rows into the simplified ``NflBoxScoreEvent`` schema used by ``outlier_nfl.settle``.

ESPN live summary often 403s from sandboxed egress; this path is the reliable
offline-downloadable adapter boundary.

Env / deps:
- No Python package required (stdlib csv + urllib).
- Optional cache dir: ``OUTLIER_NFLVERSE_CACHE`` (default ``~/.cache/outlier_nflverse``).
- Cache freshness: ``OUTLIER_NFLVERSE_MAX_AGE_HOURS`` (default
  ``DEFAULT_MAX_AGE_HOURS``). A cached file is reused only while its sidecar
  ``<file>.meta.json`` says it was fetched and validated within that window;
  ``refresh=True`` forces a re-download. Files without a sidecar (pre-F15
  caches) are treated as unvalidated and refreshed.

Cache integrity (F15):
- Downloads are retried a bounded number of times on transient errors
  (URL errors, timeouts, HTTP 429/5xx), never on other 4xx.
- A download shorter than its ``Content-Length`` or a truncated gzip is rejected.
- The decoded CSV must carry the required columns and at least one data row,
  and must not have fewer rows than the currently cached valid copy.
- New files are written to a temp path and swapped in with ``os.replace``;
  a failed or invalid refresh never replaces a valid cached copy. If a
  refresh fails and a previously validated copy exists, that copy is used and
  a warning is logged (pass ``require_fresh=True`` to raise instead). A
  pre-F15 file with no sidecar is used as that fallback only if it passes
  schema validation, also with a warning.

Failure modes:
- HTTP / DNS failure with no valid cached copy → ``BoxScoreError`` with URL.
- Missing season/week in CSV → empty event list (caller decides).
- Schedule row missing scores → event skipped.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import os
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from outlier_nfl.boxscore import BoxScoreError, NflBoxScoreEvent, _number, _token
from outlier_nfl.tape_nflverse import SCHEDULES_URL
from outlier_nfl.utils import nfl_season_for_date

NFLVERSE_STATS_WEEK_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv.gz"
)
NFLVERSE_GAMES_URL = SCHEDULES_URL
USER_AGENT = "outlier-nfl-shadow-settle-nflverse/0.1"


def default_cache_dir() -> Path:
    override = os.environ.get("OUTLIER_NFLVERSE_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home().resolve() / ".cache" / "outlier_nflverse"


logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_HOURS = 6.0
DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})

WEEK_STATS_REQUIRED_COLUMNS: tuple[str, ...] = ("game_id",)
WEEK_STATS_NAME_COLUMNS: tuple[str, ...] = ("player_display_name", "player_name")
GAMES_REQUIRED_COLUMNS: tuple[str, ...] = (
    "game_id",
    "season",
    "week",
    "gameday",
    "away_team",
    "home_team",
    "away_score",
    "home_score",
)

# Indirections so tests can stub the network and clock.
_urlopen: Callable[..., Any] = urlopen
_sleep: Callable[[float], None] = time.sleep


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CacheMeta:
    url: str
    fetched_at: str
    sha256: str
    size: int
    rows: int

    def fetched_at_dt(self) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(self.fetched_at)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def read_cache_meta(path: Path) -> CacheMeta | None:
    """Return the validation sidecar for ``path``; ``None`` if missing or unreadable."""
    meta_file = _meta_path(path)
    try:
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
        return CacheMeta(
            url=str(raw["url"]),
            fetched_at=str(raw["fetched_at"]),
            sha256=str(raw["sha256"]),
            size=int(raw["size"]),
            rows=int(raw["rows"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_meta(path: Path, meta: CacheMeta) -> None:
    payload = {
        "url": meta.url,
        "fetched_at": meta.fetched_at,
        "sha256": meta.sha256,
        "size": meta.size,
        "rows": meta.rows,
    }
    _atomic_write_bytes(_meta_path(path), (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def max_age_from_env() -> timedelta:
    raw = os.environ.get("OUTLIER_NFLVERSE_MAX_AGE_HOURS")
    hours = DEFAULT_MAX_AGE_HOURS
    if raw not in (None, ""):
        try:
            hours = float(str(raw))
        except ValueError as exc:
            raise BoxScoreError(f"Invalid OUTLIER_NFLVERSE_MAX_AGE_HOURS: {raw!r}") from exc
    if hours < 0:
        raise BoxScoreError(f"OUTLIER_NFLVERSE_MAX_AGE_HOURS must be >= 0: {raw!r}")
    return timedelta(hours=hours)


def _is_fresh(path: Path, *, max_age: timedelta, now: datetime) -> bool:
    meta = read_cache_meta(path)
    if meta is None or not path.exists():
        return False
    fetched = meta.fetched_at_dt()
    if fetched is None:
        return False
    if path.stat().st_size != meta.size:
        return False
    return now - fetched <= max_age


def _fetch_bytes(
    url: str,
    *,
    timeout: int = 60,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> bytes:
    if not url.startswith("https://"):
        raise BoxScoreError(f"Refusing non-https URL: {url}")
    attempts = max(1, int(retries))
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        request = Request(url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})  # noqa: S310  # nosec B310 - https only
        try:
            with _urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310 - https only
                data = response.read()
                expected = response.headers.get("Content-Length") if response.headers else None
            if expected not in (None, ""):
                try:
                    expected_len = int(str(expected))
                except ValueError:
                    expected_len = None
                if expected_len is not None and len(data) != expected_len:
                    raise BoxScoreError(
                        f"nflverse download truncated ({len(data)} of {expected_len} bytes): {url}"
                    )
            return data
        except HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRYABLE_HTTP or attempt == attempts:
                raise BoxScoreError(f"nflverse download failed ({exc.code}): {url}") from exc
        except BoxScoreError as exc:
            last_exc = exc
            if attempt == attempts:
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt == attempts:
                raise BoxScoreError(f"nflverse download failed: {url}: {exc}") from exc
        _sleep(backoff * attempt)
    raise BoxScoreError(f"nflverse download failed: {url}: {last_exc}")  # pragma: no cover


def _validate_csv_bytes(
    data: bytes,
    *,
    label: str,
    required: Sequence[str],
    any_of: Sequence[str] = (),
) -> int:
    """Return the data-row count; raise ``BoxScoreError`` on schema/empty failure."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BoxScoreError(f"{label}: not valid UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = set(reader.fieldnames or ())
    missing = [col for col in required if col not in header]
    if missing:
        raise BoxScoreError(f"{label}: missing required columns {missing}")
    if any_of and not any(col in header for col in any_of):
        raise BoxScoreError(f"{label}: needs one of columns {list(any_of)}")
    rows = 0
    for row in reader:
        # Extra fields land under the ``None`` key; short rows get ``None`` values.
        if None in row or any(value is None for value in row.values()):
            raise BoxScoreError(f"{label}: malformed row {rows + 2} (truncated file?)")
        rows += 1
    if rows == 0:
        raise BoxScoreError(f"{label}: no data rows")
    return rows


def _refresh_csv(
    *,
    url: str,
    dest: Path,
    label: str,
    required: Sequence[str],
    any_of: Sequence[str] = (),
    gzipped: bool = False,
    refresh: bool = False,
    require_fresh: bool = False,
    max_age: timedelta | None = None,
) -> Path:
    """Return a validated cached CSV at ``dest``, re-downloading when stale."""
    now = _utcnow()
    age = max_age if max_age is not None else max_age_from_env()
    if not refresh and _is_fresh(dest, max_age=age, now=now):
        return dest

    previous = read_cache_meta(dest) if dest.exists() else None
    try:
        payload = _fetch_bytes(url)
        if gzipped:
            try:
                data = gzip.decompress(payload)
            except (OSError, EOFError, zlib.error) as exc:
                raise BoxScoreError(f"{label}: corrupt or truncated gzip from {url}") from exc
        else:
            data = payload
        rows = _validate_csv_bytes(data, label=label, required=required, any_of=any_of)
        if previous is not None and rows < previous.rows:
            raise BoxScoreError(
                f"{label}: refreshed file has {rows} rows, fewer than cached {previous.rows}; "
                "keeping cached copy"
            )
    except BoxScoreError as exc:
        fallback = _usable_existing(dest, label=label, required=required, any_of=any_of)
        if fallback is None or require_fresh:
            raise
        logger.warning("%s refresh failed (%s); using cached copy %s", label, exc, dest)
        return fallback

    digest = hashlib.sha256(data).hexdigest()
    if previous is not None and previous.sha256 == digest and dest.exists():
        _write_meta(dest, CacheMeta(url, now.isoformat(), digest, len(data), rows))
        return dest
    _atomic_write_bytes(dest, data)
    _write_meta(dest, CacheMeta(url, now.isoformat(), digest, len(data), rows))
    return dest


def _usable_existing(
    dest: Path, *, label: str, required: Sequence[str], any_of: Sequence[str]
) -> Path | None:
    """A cached copy is a fallback only if it still validates.

    With a sidecar, the bytes must match its hash. A pre-F15 file with no
    sidecar is accepted only if it passes schema validation now (logged).
    """
    if not dest.exists():
        return None
    meta = read_cache_meta(dest)
    try:
        data = dest.read_bytes()
    except OSError:
        return None
    if meta is not None and hashlib.sha256(data).hexdigest() != meta.sha256:
        return None
    try:
        _validate_csv_bytes(data, label=label, required=required, any_of=any_of)
    except BoxScoreError:
        return None
    if meta is None:
        logger.warning("%s: cached copy %s has no fetch metadata (pre-F15 cache)", label, dest)
    return dest


def ensure_week_stats_csv(
    season: int,
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
    require_fresh: bool = False,
    max_age: timedelta | None = None,
) -> Path:
    """Return path to a validated, decompressed week-stats CSV for a season."""
    cache = cache_dir or default_cache_dir()
    return _refresh_csv(
        url=NFLVERSE_STATS_WEEK_URL.format(season=season),
        dest=cache / f"stats_player_week_{season}.csv",
        label=f"nflverse stats_player_week_{season}",
        required=WEEK_STATS_REQUIRED_COLUMNS,
        any_of=WEEK_STATS_NAME_COLUMNS,
        gzipped=True,
        refresh=refresh,
        require_fresh=require_fresh,
        max_age=max_age,
    )


def ensure_games_csv(
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
    require_fresh: bool = False,
    max_age: timedelta | None = None,
) -> Path:
    """Return path to a validated nflverse ``games.csv`` schedule/results file."""
    cache = cache_dir or default_cache_dir()
    return _refresh_csv(
        url=NFLVERSE_GAMES_URL,
        dest=cache / "games.csv",
        label="nflverse games.csv",
        required=GAMES_REQUIRED_COLUMNS,
        refresh=refresh,
        require_fresh=require_fresh,
        max_age=max_age,
    )


def _f(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return _number(row[key])
    return None


def _player_stats_from_week_row(row: Mapping[str, Any]) -> dict[str, float]:
    """Map one nflverse week-stats row to group-qualified simplified keys."""
    bucket: dict[str, float] = {}

    def put(key: str, value: float | None) -> None:
        if value is None:
            return
        bucket[key] = float(value)
        bucket[_token(key)] = float(value)

    put("PASSING:YDS", _f(row, "passing_yards"))
    put("PASSING:TD", _f(row, "passing_tds"))
    put("PASSING:CMP", _f(row, "completions"))
    put("PASSING:COMP", _f(row, "completions"))
    put("PASSING:ATT", _f(row, "attempts"))
    put("PASSING:INT", _f(row, "passing_interceptions"))

    put("RUSHING:YDS", _f(row, "rushing_yards"))
    put("RUSHING:CAR", _f(row, "carries"))
    put("RUSHING:ATT", _f(row, "carries"))
    put("RUSHING:TD", _f(row, "rushing_tds"))

    put("RECEIVING:YDS", _f(row, "receiving_yards"))
    put("RECEIVING:REC", _f(row, "receptions"))
    put("RECEIVING:TD", _f(row, "receiving_tds"))
    put("RECEIVING:TGT", _f(row, "targets"))

    put("DEFENSIVE:SACK", _f(row, "def_sacks"))
    put("DEFENSIVE:SK", _f(row, "def_sacks"))
    solo = _f(row, "def_tackles_solo")
    assist = _f(row, "def_tackle_assists")
    with_assist = _f(row, "def_tackles_with_assist")
    put("DEFENSIVE:SOLO", solo)
    put("DEFENSIVE:AST", assist)
    put("DEFENSIVE:ASSISTS", assist)
    if solo is not None or assist is not None:
        tot = (solo or 0.0) + (assist or 0.0)
        put("DEFENSIVE:TOT", tot)
        put("DEFENSIVE:TKL", tot)
        put("DEFENSIVE:TACKLESASSISTS", tot)
    elif with_assist is not None:
        put("DEFENSIVE:TOT", with_assist)
        put("DEFENSIVE:TKL", with_assist)
        put("DEFENSIVE:TACKLESASSISTS", with_assist)

    fg = _f(row, "fg_made")
    pat = _f(row, "pat_made")
    put("KICKING:FG", fg)
    put("KICKING:XP", pat)
    if fg is not None or pat is not None:
        put("KICKING:PTS", 3.0 * (fg or 0.0) + (pat or 0.0))

    put("RETURNING:TD", _f(row, "special_teams_tds"))
    return bucket


def _merge_player_buckets(
    left: dict[str, float], right: dict[str, float]
) -> dict[str, float]:
    out = dict(left)
    for key, value in right.items():
        # Sum numeric counting stats when a player appears twice (rare).
        if key in out and key.endswith(("YDS", "TD", "REC", "ATT", "CAR", "CMP", "COMP", "INT", "TOT", "TKL", "SACK", "SK", "AST", "ASSISTS", "SOLO", "FG", "XP", "PTS", "TGT", "TACKLESASSISTS")):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


def load_nflverse_events(
    *,
    season: int,
    week: int | None = None,
    event_date: date | None = None,
    cache_dir: Path | None = None,
    stats_csv: Path | str | None = None,
    games_csv: Path | str | None = None,
    refresh: bool = False,
) -> list[NflBoxScoreEvent]:
    """Build simplified box-score events for a season week and/or slate date."""
    cache = cache_dir or default_cache_dir()
    stats_path = (
        Path(stats_csv)
        if stats_csv
        else ensure_week_stats_csv(season, cache_dir=cache, refresh=refresh)
    )
    games_path = (
        Path(games_csv) if games_csv else ensure_games_csv(cache_dir=cache, refresh=refresh)
    )

    games_by_id: dict[str, dict[str, str]] = {}
    with games_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("season") or "") != str(season):
                continue
            if week is not None and str(row.get("week") or "") != str(week):
                continue
            if event_date is not None and str(row.get("gameday") or "")[:10] != event_date.isoformat():
                continue
            gid = str(row.get("game_id") or "").strip()
            if not gid:
                continue
            games_by_id[gid] = row

    if not games_by_id:
        return []

    players_by_game: dict[str, dict[str, dict[str, float]]] = {gid: {} for gid in games_by_id}
    with stats_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            gid = str(row.get("game_id") or "").strip()
            if gid not in players_by_game:
                continue
            name = str(row.get("player_display_name") or row.get("player_name") or "").strip()
            if not name:
                continue
            stats = _player_stats_from_week_row(row)
            if not stats:
                continue
            key = _token(name)
            bucket = players_by_game[gid]
            if key in bucket:
                bucket[key] = _merge_player_buckets(bucket[key], stats)
            else:
                bucket[key] = stats
            # Also keep a display-name key path via token only (settle resolves by token).

    events: list[NflBoxScoreEvent] = []
    for gid, grow in sorted(games_by_id.items()):
        away = str(grow.get("away_team") or "").strip()
        home = str(grow.get("home_team") or "").strip()
        away_score = _number(grow.get("away_score"))
        home_score = _number(grow.get("home_score"))
        gameday = str(grow.get("gameday") or "")[:10]
        if not away or not home or away_score is None or home_score is None or len(gameday) < 10:
            continue
        # Re-key players with original display names for resolve_player_stats token matching.
        # We stored token keys; rebuild with synthetic display from token is lossy — keep tokens.
        # settle.resolve_player_stats tokenizes the prediction name, so token keys are correct.
        # But parse_simplified expects display names then tokenizes — either works if keys are tokens.
        # Use a reverse map: store under a placeholder display equal to the token for stability.
        raw_players = players_by_game.get(gid) or {}
        # Prefer restoring readable names from week rows by re-scan is expensive; token keys OK.
        display_players: dict[str, dict[str, float]] = {}
        for token_key, stats in raw_players.items():
            display_players[token_key] = stats
        events.append(
            NflBoxScoreEvent(
                provider_event_id=gid,
                event_date=date.fromisoformat(gameday),
                away=away.upper(),
                home=home.upper(),
                away_score=away_score,
                home_score=home_score,
                players=display_players,
            )
        )
    return events


def events_to_simplified_payload(events: Sequence[NflBoxScoreEvent]) -> dict[str, Any]:
    """Serialize events back to the CI simplified box-score JSON schema."""
    out_events = []
    for event in events:
        # players are token-keyed; emit as-is (settle tokenizes lookup names).
        players = {name: dict(stats) for name, stats in event.players.items()}
        out_events.append(
            {
                "provider_event_id": event.provider_event_id,
                "event_date": event.event_date.isoformat(),
                "away": event.away,
                "home": event.home,
                "away_score": event.away_score,
                "home_score": event.home_score,
                "players": players,
            }
        )
    return {"provider": "nflverse", "events": out_events}


def fetch_nflverse_boxscores_for_date(
    event_date: date,
    *,
    season: int | None = None,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> list[NflBoxScoreEvent]:
    """Convenience: load all completed games on an Eastern slate date."""
    # A season is labelled by the calendar year of its September Week 1 and runs
    # through the Super Bowl in early February, so reading the year straight off
    # the date asks nflverse for an unplayed season on every January/February
    # playoff slate and comes back with no events at all.
    season_year = season or nfl_season_for_date(event_date)
    if season_year is None:
        raise BoxScoreError(f"Could not derive an NFL season from slate date: {event_date!r}")
    return load_nflverse_events(
        season=season_year,
        event_date=event_date,
        cache_dir=cache_dir,
        refresh=refresh,
    )


__all__ = [
    "DEFAULT_MAX_AGE_HOURS",
    "CacheMeta",
    "default_cache_dir",
    "ensure_games_csv",
    "ensure_week_stats_csv",
    "events_to_simplified_payload",
    "fetch_nflverse_boxscores_for_date",
    "load_nflverse_events",
    "max_age_from_env",
    "read_cache_meta",
]
