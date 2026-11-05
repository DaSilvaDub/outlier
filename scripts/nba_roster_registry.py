"""ESPN-backed NBA roster snapshots. No Gemini imports or implicit network reads."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outlier_nba.identity import name_key  # noqa: E402

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "resources/nba_roster_registry.json"
TEAMS = frozenset("ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS".split())
ALIASES = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
OUT = frozenset({"OUT", "INACTIVE", "SUSPENDED", "IR"})
STATUSES = {"active": "ACTIVE", "out": "OUT", "inactive": "INACTIVE", "suspended": "SUSPENDED", "ir": "IR", "injured reserve": "IR", "day to day": "QUESTIONABLE", "questionable": "QUESTIONABLE", "doubtful": "DOUBTFUL", "probable": "PROBABLE"}


class RegistryError(RuntimeError):
    """Missing, stale, unverified or inconsistent roster evidence."""


def utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RegistryError(f"Invalid source timestamp: {value}") from exc


def season_year(date: str) -> int:
    day = datetime.strptime(date, "%Y-%m-%d")
    return day.year + (day.month >= 9)


def current_date() -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def status(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    key = str(value or "").lower().replace("-", " ").strip()
    if key not in STATUSES:
        raise RegistryError(f"Unknown availability status: {value}")
    return STATUSES[key]


def athlete_id(athlete: dict) -> str:
    if athlete.get("id"):
        return str(athlete["id"])
    ids = {m.group(1) for link in athlete.get("links", [])
           if (m := re.search(r"espn\.com/nba/player/(?:[^/]+/)*_?/id/(\d+)", link.get("href", "")))}
    if len(ids) != 1:
        raise RegistryError(f"Missing/ambiguous ESPN player identity: {athlete.get('displayName')}")
    return ids.pop()


def build_registry(sources: list[tuple[dict, dict]], date: str, now: datetime,
                   max_age_hours: float = 6.0) -> dict:
    """Validate and deterministically join raw provider evidence by ESPN identity."""
    if max_age_hours <= 0 or max_age_hours > 6:
        raise RegistryError("Roster TTL must be greater than zero and at most six hours")
    slate_day = datetime.strptime(date, "%Y-%m-%d").date()
    if not 0 <= (slate_day - now.astimezone(ZoneInfo("America/New_York")).date()).days <= 1:
        raise RegistryError("Current roster observations support only today/next-day slates, not historical/far-future filtering")
    expected = season_year(date)
    payloads = {}
    for receipt, payload in sources:
        url = receipt["url"]
        if url in payloads:
            raise RegistryError("Duplicate source receipt")
        fetched = utc(receipt["fetched_utc"])
        if not -300 <= (now - fetched).total_seconds() <= max_age_hours * 3600:
            raise RegistryError("Stale/future roster fetch receipt")
        if url == BASE + "/teams?limit=40":
            season = payload["sports"][0]["leagues"][0]["season"]
        else:
            if payload.get("status") != "success":
                raise RegistryError(f"Provider did not report success: {url}")
            season = payload.get("season", {})
            stamp = utc(payload.get("timestamp"))
            if not -300 <= (now - stamp).total_seconds() <= max_age_hours * 3600:
                raise RegistryError(f"Stale/future provider payload: {url}")
        if season.get("year") != expected:
            raise RegistryError(f"Provider season mismatch: {url}: {season}")
        payloads[url] = payload
    league = payloads[BASE + "/teams?limit=40"]["sports"][0]["leagues"][0]
    teams, players, names = {}, {}, {}
    for item in league["teams"]:
        team = item["team"]
        code = ALIASES.get(team["abbreviation"], team["abbreviation"])
        tid = str(team["id"])
        if code not in TEAMS or code in teams:
            raise RegistryError("Duplicate/unknown NBA team")
        url = BASE + f"/teams/{tid}/roster"
        roster = payloads[url]
        if str(roster.get("team", {}).get("id")) != tid:
            raise RegistryError("Roster team mismatch")
        athletes = roster.get("athletes", [])
        if not 10 <= len(athletes) <= 30:
            raise RegistryError(f"Incomplete roster: {code}")
        team_rows = []
        for athlete in athletes:
            pid, name = athlete_id(athlete), athlete.get("displayName", "").strip()
            key = name_key(name)
            if not key or pid in players or key in names:
                raise RegistryError(f"Duplicate/ambiguous player identity: {name}")
            availability = status(athlete.get("status"))
            injuries = athlete.get("injuries", [])
            if not isinstance(injuries, list):
                raise RegistryError("Malformed roster injuries")
            injury_statuses = [status(x.get("status")) for x in injuries]
            if injury_statuses:
                availability = next((x for x in injury_statuses if x in OUT), injury_statuses[0])
            row = {"espn_id": pid, "name": name, "team": code, "status": availability,
                   "injuries": injuries}
            players[pid], names[key] = row, pid
            team_rows.append(row)
        teams[code] = {"espn_id": tid, "name": team["displayName"], "roster": team_rows}
    if set(teams) != TEAMS or len(payloads) != 32:
        raise RegistryError("Expected all 30 teams and exactly 32 source feeds")
    injury_feed = payloads[BASE + "/injuries"]
    if not isinstance(injury_feed.get("injuries"), list):
        raise RegistryError("Injury feed missing its complete list")
    unmatched, seen = [], set()
    for group in injury_feed["injuries"]:
        for injury in group["injuries"]:
            athlete = injury["athlete"]
            pid = athlete_id(athlete)
            availability = status(injury.get("status"))
            if pid in seen:
                raise RegistryError(f"Duplicate injury identity: {pid}")
            seen.add(pid)
            if pid not in players:
                unmatched.append(pid)  # Not in any current roster; cannot enter a matched pack.
                continue
            row = players[pid]
            if name_key(athlete.get("displayName")) != name_key(row["name"]) or str(group["id"]) != teams[row["team"]]["espn_id"]:
                raise RegistryError(f"Injury roster identity/team mismatch: {pid}")
            row["status"] = availability if row["status"] not in OUT else row["status"]
            row["injuries"] = [{"status": injury["status"], "reported_utc": injury.get("date")}]
    return {"schema_version": 1, "season_year": expected,
            "teams": dict(sorted(teams.items())), "unmatched_injury_ids": sorted(unmatched)}


def fetch(url: str) -> tuple[dict, bytes]:
    import requests
    response = requests.get(url, timeout=(10, 25), headers={"User-Agent": "OutlierRoster/1.0", "Cache-Control": "no-cache"})
    response.raise_for_status()
    raw = response.content
    if len(raw) > 10_000_000:
        raise RegistryError("Provider payload exceeds size limit")
    return {"url": url, "fetched_utc": datetime.now(timezone.utc).isoformat(), "sha256": digest(raw)}, raw


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh(path: Path, date: str, fetcher=fetch) -> dict:
    first = fetcher(BASE + "/teams?limit=40")
    league = json.loads(first[1])["sports"][0]["leagues"][0]
    urls = [BASE + "/injuries"] + [BASE + f"/teams/{x['team']['id']}/roster" for x in league["teams"]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = [first, *pool.map(fetcher, urls)]
    now = datetime.now(timezone.utc)
    registry = build_registry([(r, json.loads(raw)) for r, raw in fetched], date, now)
    # Publish only a complete snapshot. Failed fetch/validation cannot replace the old registry.
    bundle = path.parent / "nba_roster_snapshots" / (now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex)
    bundle.mkdir(parents=True)
    receipts = []
    for index, (receipt, raw) in enumerate(fetched):
        raw_path = bundle / f"{index:02d}.json"
        with raw_path.open("xb") as stream:
            stream.write(raw)
        receipts.append({**receipt, "raw_path": raw_path.relative_to(path.parent).as_posix()})
    registry.update(as_of_utc=now.isoformat(), sources=receipts)
    # Preserve the normalized version alongside its raw sources before moving the pointer.
    version = {**registry, "sources": [{**r, "raw_path": Path(r["raw_path"]).name} for r in receipts]}
    atomic_write(bundle / "registry.json", json.dumps(version, indent=2, sort_keys=True).encode())
    registry["immutable_snapshot"] = (bundle / "registry.json").relative_to(path.parent).as_posix()
    atomic_write(path, json.dumps(registry, indent=2, sort_keys=True).encode())
    return registry


def load(path: Path | None = None, date: str | None = None, *, now: datetime | None = None) -> dict:
    path = path or DEFAULT_PATH
    now = now or datetime.now(timezone.utc)
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        if registry.get("schema_version") != 1:
            raise RegistryError("Legacy/unverified registry: run refresh_nba_roster_registry.py")
        if not -300 <= (now - utc(registry["as_of_utc"])).total_seconds() <= 6 * 3600:
            raise RegistryError("Stale/future registry")
        sources = []
        for receipt in registry["sources"]:
            raw_path = path.parent / receipt["raw_path"]
            if not raw_path.resolve().is_relative_to(path.parent.resolve()) or raw_path.is_symlink():
                raise RegistryError("Source evidence escapes registry root")
            raw = raw_path.read_bytes()
            if digest(raw) != receipt["sha256"]:
                raise RegistryError("Source evidence hash mismatch")
            sources.append((receipt, json.loads(raw)))
        rebuilt = build_registry(sources, date or current_date(), now)
        if any(registry.get(key) != value for key, value in rebuilt.items()):
            raise RegistryError("Registry does not match its raw provider evidence")
        return registry
    except RegistryError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RegistryError(f"Missing/malformed registry or source evidence: {path}: {exc}") from exc
