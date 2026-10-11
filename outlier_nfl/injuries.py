"""Same-day NFL injury / inactive status for pack filtering.

Primary source: ESPN public injuries feed (same site.api host pattern as
``outlier_nfl.boxscore``). Optional merge with nflverse tape inactives already
written by ``tape_nflverse`` / ``load_tape_inactives``.

Ruled-out statuses (stripped from packs): Out, Inactive, IR / Injured Reserve,
Doubtful. Questionable / Probable / Active are kept.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from outlier_nfl.config import NFL_TEAMS, normalize_team
from outlier_nfl.tape_nflverse import load_tape_inactives

logger = logging.getLogger(__name__)

ESPN_INJURIES_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
)
USER_AGENT = "outlier-nfl-pack-refresh/0.1"

# Status labels that mean the player should not appear in a same-day pack.
RULED_OUT_STATUSES: frozenset[str] = frozenset(
    {
        "out",
        "inactive",
        "ir",
        "injured reserve",
        "doubtful",
    }
)

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
_NAME_DISP_TO_CODE = {
    info.name.strip().lower(): code for code, info in NFL_TEAMS.items()
}


def name_key(name: Any) -> str:
    """Normalize a player name for fuzzy equality (strip punctuation / suffixes)."""
    text = re.sub(r"[^a-z ]", "", str(name or "").lower().replace("-", " "))
    return " ".join(_SUFFIX_RE.sub("", text).split())


def _status_key(raw: Any) -> str:
    if isinstance(raw, Mapping):
        raw = (
            raw.get("description")
            or raw.get("name")
            or raw.get("type")
            or raw.get("abbreviation")
            or ""
        )
    return str(raw or "").strip().lower()


def is_ruled_out(status: Any) -> bool:
    """True when ``status`` is Out / Inactive / IR / Doubtful (any casing)."""
    key = _status_key(status)
    if key in RULED_OUT_STATUSES:
        return True
    # Catch compound labels like "Injured Reserve - Designated to Return".
    if "injured reserve" in key or key.startswith("ir ") or key.endswith(" ir"):
        return True
    return False


def _team_code_from_espn(team_block: Mapping[str, Any]) -> str:
    display = str(team_block.get("displayName") or "").strip()
    code = _NAME_DISP_TO_CODE.get(display.lower())
    if code:
        return code
    # Fallback: last token of nickname ("Falcons") via NFL_TEAMS nicknames.
    nick = display.split()[-1].lower() if display else ""
    for c, info in NFL_TEAMS.items():
        if info.nickname.strip().lower() == nick:
            return c
    return normalize_team(display) or ""


def fetch_espn_injuries(
    timeout: float = 30.0,
    url: str = ESPN_INJURIES_URL,
) -> dict[str, list[dict[str, str]]]:
    """Fetch ESPN league-wide injuries keyed by canonical team code.

    Each entry is ``{"name": ..., "status": ..., "source": "espn"}``.
    Network failures raise; callers that want soft-fail should catch.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Refusing non-https URL: {url!r}")
    req = Request(url, headers={"User-Agent": USER_AGENT})  # nosec B310
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_espn_injuries_payload(payload)


def parse_espn_injuries_payload(
    payload: Mapping[str, Any] | list[Any],
) -> dict[str, list[dict[str, str]]]:
    """Parse an ESPN injuries JSON body into ``{team: [entries]}``."""
    teams_block: list[Any]
    if isinstance(payload, Mapping):
        teams_block = list(payload.get("injuries") or [])
    elif isinstance(payload, list):
        teams_block = payload
    else:
        return {}

    out: dict[str, list[dict[str, str]]] = {}
    for team in teams_block:
        if not isinstance(team, Mapping):
            continue
        code = _team_code_from_espn(team)
        if not code:
            continue
        entries: list[dict[str, str]] = []
        for item in team.get("injuries") or []:
            if not isinstance(item, Mapping):
                continue
            athlete = item.get("athlete") if isinstance(item.get("athlete"), Mapping) else {}
            name = str(
                (athlete or {}).get("displayName")
                or (athlete or {}).get("fullName")
                or ""
            ).strip()
            status = str(item.get("status") or "").strip()
            if not name:
                continue
            entries.append({"name": name, "status": status, "source": "espn"})
        out[code] = entries
    return out


def tape_inactive_entries(
    nfl_dir: Path | str,
) -> dict[str, list[dict[str, str]]]:
    """Wrap tape ``inactive`` names as status=Out entries (nflverse weekly report)."""
    raw = load_tape_inactives(nfl_dir)
    return {
        team: [{"name": n, "status": "Out", "source": "tape"} for n in names]
        for team, names in raw.items()
        if names
    }


def merge_injury_maps(
    *maps: Mapping[str, list[dict[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    """Merge injury maps; later sources win on (team, name_key) collisions."""
    merged: dict[str, dict[str, dict[str, str]]] = {}
    for m in maps:
        for team, entries in m.items():
            code = normalize_team(team) or str(team).strip().upper()
            bucket = merged.setdefault(code, {})
            for entry in entries:
                key = name_key(entry.get("name"))
                if not key:
                    continue
                bucket[key] = {
                    "name": str(entry.get("name") or ""),
                    "status": str(entry.get("status") or ""),
                    "source": str(entry.get("source") or ""),
                }
    return {
        team: sorted(entries.values(), key=lambda e: e["name"].lower())
        for team, entries in sorted(merged.items())
    }


def ruled_out_name_keys(
    injury_map: Mapping[str, list[dict[str, str]]],
    teams: Iterable[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Map ``name_key -> {name, status, team, source}`` for ruled-out players.

    When ``teams`` is given, only those franchises are considered (slate filter).
    """
    wanted = None
    if teams is not None:
        wanted = {normalize_team(t) or str(t).strip().upper() for t in teams if t}

    out: dict[str, dict[str, str]] = {}
    for team, entries in injury_map.items():
        code = normalize_team(team) or str(team).strip().upper()
        if wanted is not None and code not in wanted:
            continue
        for entry in entries:
            if not is_ruled_out(entry.get("status")):
                continue
            key = name_key(entry.get("name"))
            if not key:
                continue
            out[key] = {
                "name": str(entry.get("name") or ""),
                "status": str(entry.get("status") or ""),
                "team": code,
                "source": str(entry.get("source") or ""),
            }
    return out


def row_player_name(row: Mapping[str, Any]) -> str:
    """Player display name from pack / alt-floor / best-bet style rows."""
    return str(row.get("player") or row.get("player_name") or "").strip()


def filter_pack_rows(
    rows: list[dict[str, Any]],
    ruled_out: Mapping[str, Mapping[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split pack rows into (kept, removed). Aggregate/market labels never match."""
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for row in rows:
        player = row_player_name(row)
        key = name_key(player)
        hit = ruled_out.get(key) if key else None
        if hit:
            removed.append({**row, "_ruled_out_status": hit.get("status", ""), "_ruled_out_source": hit.get("source", "")})
        else:
            kept.append(row)
    return kept, removed


def load_injury_status(
    nfl_dir: Path | str | None = None,
    *,
    use_espn: bool = True,
    use_tape: bool = True,
    timeout: float = 30.0,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    """Build merged injury map + meta about which sources loaded."""
    meta: dict[str, Any] = {
        "espn_ok": False,
        "tape_ok": False,
        "espn_error": None,
        "sources": [],
    }
    maps: list[dict[str, list[dict[str, str]]]] = []

    if use_tape and nfl_dir is not None:
        tape = tape_inactive_entries(nfl_dir)
        if tape:
            maps.append(tape)
            meta["tape_ok"] = True
            meta["sources"].append("tape")
        else:
            meta["tape_ok"] = True  # loaded, empty is fine
            meta["sources"].append("tape_empty")

    if use_espn:
        try:
            espn = fetch_espn_injuries(timeout=timeout)
            maps.append(espn)
            meta["espn_ok"] = True
            meta["sources"].append("espn")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            meta["espn_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("ESPN injuries fetch failed: %s", exc)

    return merge_injury_maps(*maps), meta


def filter_csv_file(
    csv_path: Path,
    *,
    out_path: Path | None = None,
    nfl_dir: Path | str | None = None,
    teams: Iterable[str] | None = None,
    use_espn: bool = True,
    use_tape: bool = True,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    """Filter a pack CSV in place (or to ``out_path``). Returns a summary dict."""
    csv_path = Path(csv_path)
    dest = Path(out_path) if out_path else csv_path

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if teams is None:
        teams = sorted({str(r.get("team") or "").strip().upper() for r in rows if r.get("team")})

    injury_map, meta = load_injury_status(
        nfl_dir, use_espn=use_espn, use_tape=use_tape
    )
    ruled = ruled_out_name_keys(injury_map, teams=teams)
    kept, removed = filter_pack_rows(rows, ruled)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    if cache_path is not None:
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "meta": meta,
                    "teams": list(teams),
                    "ruled_out": list(ruled.values()),
                    "removed": [
                        {
                            "player": r.get("player"),
                            "team": r.get("team"),
                            "market": r.get("market"),
                            "status": r.get("_ruled_out_status"),
                            "source": r.get("_ruled_out_source"),
                        }
                        for r in removed
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    summary = {
        "before": len(rows),
        "after": len(kept),
        "removed": len(removed),
        "ruled_out_players": len(ruled),
        "csv": str(dest),
        "removals": [
            {
                "player": r.get("player"),
                "team": r.get("team"),
                "market": r.get("market"),
                "status": r.get("_ruled_out_status"),
                "source": r.get("_ruled_out_source"),
            }
            for r in removed
        ],
        "meta": meta,
    }
    return summary



def ruled_out_map_from_entries(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    """Build ``name_key -> {name,status,team,source}`` from cache/summary entries."""
    out: dict[str, dict[str, str]] = {}
    for entry in entries:
        key = name_key(entry.get("name") or entry.get("player") or entry.get("player_name"))
        if not key:
            continue
        out[key] = {
            "name": str(entry.get("name") or entry.get("player") or entry.get("player_name") or ""),
            "status": str(entry.get("status") or ""),
            "team": str(entry.get("team") or ""),
            "source": str(entry.get("source") or ""),
        }
    return out


def filter_best_bets_payload(
    payload: Mapping[str, Any],
    ruled_out: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop ruled-out player picks, re-rank, refresh counts, scrub audit orphans."""
    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for pick in payload.get("picks") or []:
        if not isinstance(pick, Mapping):
            continue
        key = name_key(row_player_name(pick))
        if key and key in ruled_out:
            removed.append(dict(pick))
        else:
            kept.append(dict(pick))

    for rank, pick in enumerate(kept, 1):
        pick["rank"] = rank

    verdicts = ("VALIDATED", "PROVISIONAL", "REJECTED")
    counts = {v: sum(1 for p in kept if p.get("verdict") == v) for v in verdicts}

    out = dict(payload)
    out["picks"] = kept
    out["candidates"] = len(kept)
    out["counts"] = counts

    audit = dict(out.get("audit") or {})
    signals = dict(audit.get("signals") or {})
    orphans = list(signals.get("orphaned") or [])
    if orphans:
        signals["orphaned"] = [
            o for o in orphans
            if name_key((o or {}).get("player") if isinstance(o, Mapping) else "") not in ruled_out
        ]
        audit["signals"] = signals
        out["audit"] = audit

    return out, removed


def write_filtered_best_bets(
    json_path: Path,
    ruled_out: Mapping[str, Mapping[str, str]],
    *,
    md_path: Path | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Filter ``nfl_best_bets_*.json`` and regenerate the companion markdown."""
    from outlier_nfl.best_bets import render_best_bets_markdown

    json_path = Path(json_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    filtered, removed = filter_best_bets_payload(payload, ruled_out)
    json_path.write_text(json.dumps(filtered, indent=2), encoding="utf-8")

    dest_md = Path(md_path) if md_path else json_path.with_suffix(".md")
    slate = filtered.get("date") or filtered.get("run_date") or json_path.stem.replace("nfl_best_bets_", "")
    md_title = title or f"NFL Traced Best Bets - slate {slate}"
    dest_md.write_text(
        render_best_bets_markdown(filtered, title=md_title),
        encoding="utf-8",
    )

    # Keep rolling latest mirror aligned when it exists beside the dated card.
    latest_json = json_path.parent / "nfl_best_bets_latest.json"
    if latest_json.is_file() and latest_json.resolve() != json_path.resolve():
        latest_json.write_text(json.dumps(filtered, indent=2), encoding="utf-8")

    return {
        "json": str(json_path),
        "md": str(dest_md),
        "before": len(payload.get("picks") or []),
        "after": len(filtered.get("picks") or []),
        "removed": len(removed),
        "removals": [
            {
                "player": r.get("player_name") or r.get("player"),
                "team": r.get("team"),
                "market": r.get("market"),
                "verdict": r.get("verdict"),
                "status": (ruled_out.get(name_key(row_player_name(r))) or {}).get("status"),
                "source": (ruled_out.get(name_key(row_player_name(r))) or {}).get("source"),
            }
            for r in removed
        ],
    }


def filter_matchup_scripts_payload(
    payload: Mapping[str, Any],
    ruled_out: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Strip ruled-out players from matchup ``prop_signals`` lists."""
    removed: list[dict[str, Any]] = []
    out = dict(payload)
    records = []
    for rec in payload.get("records") or []:
        if not isinstance(rec, Mapping):
            continue
        rec2 = dict(rec)
        kept_signals = []
        for sig in rec.get("prop_signals") or []:
            if not isinstance(sig, Mapping):
                continue
            key = name_key(row_player_name(sig))
            if key and key in ruled_out:
                removed.append(dict(sig))
            else:
                kept_signals.append(dict(sig))
        rec2["prop_signals"] = kept_signals
        records.append(rec2)
    out["records"] = records
    return out, removed


def filter_game_script_markdown(
    text: str,
    ruled_out: Mapping[str, Mapping[str, str]],
) -> tuple[str, list[str]]:
    """Remove prop-signal table rows and calibrated-prop bullets for ruled-out players."""
    removed: list[str] = []
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        drop = False
        if stripped.startswith("|") and not stripped.startswith("|:") and not stripped.lower().startswith("| player"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells:
                key = name_key(cells[0])
                if key and key in ruled_out:
                    drop = True
        elif stripped.startswith("- **"):
            end = stripped.find("**", 4)
            if end > 4:
                key = name_key(stripped[4:end])
                if key and key in ruled_out:
                    drop = True
        if drop:
            removed.append(stripped)
        else:
            out_lines.append(line)
    result = "\n".join(out_lines)
    if text.endswith("\n") or text.endswith("\r\n"):
        result += "\n"
    return result, removed


__all__ = [
    "ESPN_INJURIES_URL",
    "RULED_OUT_STATUSES",
    "name_key",
    "is_ruled_out",
    "fetch_espn_injuries",
    "parse_espn_injuries_payload",
    "tape_inactive_entries",
    "merge_injury_maps",
    "ruled_out_name_keys",
    "row_player_name",
    "filter_pack_rows",
    "load_injury_status",
    "filter_csv_file",
    "ruled_out_map_from_entries",
    "filter_best_bets_payload",
    "write_filtered_best_bets",
    "filter_matchup_scripts_payload",
    "filter_game_script_markdown",
]
