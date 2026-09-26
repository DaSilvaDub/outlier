"""nflverse (GitHub releases CSV) box-score provider for shadow settle.

Downloads (or reads cached) ``stats_player_week_{season}.csv`` + ``games.csv``
from the nflverse-data GitHub releases and maps rows into the simplified
``NflBoxScoreEvent`` schema used by ``outlier_nfl.settle``.

ESPN live summary often 403s from sandboxed egress; this path is the reliable
offline-downloadable adapter boundary.

Env / deps:
- No Python package required (stdlib csv + urllib).
- Optional cache dir: ``OUTLIER_NFLVERSE_CACHE`` (default ``~/.cache/outlier_nflverse``).
- Network only needed on first fetch; subsequent runs use cache.

Failure modes:
- HTTP / DNS failure → ``BoxScoreError`` with URL.
- Missing season/week in CSV → empty event list (caller decides).
- Schedule row missing scores → event skipped.
"""

from __future__ import annotations

import csv
import gzip
import os
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from outlier_nfl.boxscore import BoxScoreError, NflBoxScoreEvent, _number, _token

NFLVERSE_STATS_WEEK_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv.gz"
)
NFLVERSE_GAMES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
)
USER_AGENT = "outlier-nfl-shadow-settle-nflverse/0.1"


def default_cache_dir() -> Path:
    override = os.environ.get("OUTLIER_NFLVERSE_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home().resolve() / ".cache" / "outlier_nflverse"


def _download(url: str, dest: Path, *, timeout: int = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    request = Request(url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
    except HTTPError as exc:
        raise BoxScoreError(f"nflverse download failed ({exc.code}): {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise BoxScoreError(f"nflverse download failed: {url}: {exc}") from exc
    dest.write_bytes(data)
    return dest


def ensure_week_stats_csv(season: int, *, cache_dir: Path | None = None) -> Path:
    """Return path to decompressed week-stats CSV for a season (cached)."""
    cache = cache_dir or default_cache_dir()
    gz_path = cache / f"stats_player_week_{season}.csv.gz"
    csv_path = cache / f"stats_player_week_{season}.csv"
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return csv_path
    _download(NFLVERSE_STATS_WEEK_URL.format(season=season), gz_path)
    with gzip.open(gz_path, "rb") as src:
        csv_path.write_bytes(src.read())
    return csv_path


def ensure_games_csv(*, cache_dir: Path | None = None) -> Path:
    cache = cache_dir or default_cache_dir()
    path = cache / "games.csv"
    return _download(NFLVERSE_GAMES_URL, path)


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
) -> list[NflBoxScoreEvent]:
    """Build simplified box-score events for a season week and/or slate date."""
    cache = cache_dir or default_cache_dir()
    stats_path = Path(stats_csv) if stats_csv else ensure_week_stats_csv(season, cache_dir=cache)
    games_path = Path(games_csv) if games_csv else ensure_games_csv(cache_dir=cache)

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
) -> list[NflBoxScoreEvent]:
    """Convenience: load all completed games on an Eastern slate date."""
    season_year = season or event_date.year
    # NFL season year equals the calendar year of Week 1 (Sep); Sep/Oct/Nov/Dec use that year.
    return load_nflverse_events(
        season=season_year,
        event_date=event_date,
        cache_dir=cache_dir,
    )


__all__ = [
    "default_cache_dir",
    "ensure_games_csv",
    "ensure_week_stats_csv",
    "events_to_simplified_payload",
    "fetch_nflverse_boxscores_for_date",
    "load_nflverse_events",
]
