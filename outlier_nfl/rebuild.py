"""Rebuild NFL slates from admissible archived originals (#232 part a).

A rebuild replays one archived slate through today's pipeline:

- **Admissible slates only.** :func:`admissibility` reads the 9c archive manifest
  and the checker's verdicts. A slate is admissible only when the checker finds no
  gap for it, the manifest has no invalid lines, and the archived run is a
  complete pregame original. That means the run manifest says ``status: OK``,
  ``mode: live`` and not a replay, with ``as_of_utc`` before ``first_kickoff_utc``.
  Anything else is refused with its reasons.
- **Replay mode only.** The archived raw feeds are served through
  :class:`ArchivedOutlierClient` at the original run's ``as_of_utc`` while the
  wall clock is now, so :class:`~outlier_nfl.run_context.RunContext` marks it
  ``replay=True``. Its mode is ``retrospective`` once kickoff has passed (always,
  for a historical slate) or ``replay`` before kickoff. Nothing else is accepted. Phase 1's as-of cutoffs and phase 2's stage gates apply unchanged,
  and a replay never publishes.
- **Versioned and write-once.** Everything the rebuild writes lives under
  ``<nfl_dir>/versions/<rebuild_id>/``: the replay's own data dir (``work/``),
  ``rebuild_manifest.json`` (with the sha256 of every archived input used) and
  the comparison report. nflverse inputs are pinned copies in ``nflverse/``,
  weather comes only from the original bundle, and the tape only from the
  archived tape role; nothing is fetched. The folder must not exist beforehand. Original
  predictions, ledgers, calibration, snapshots and the archive are only read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import uuid
from typing import Any

from outlier_nfl import archive as ar

logger = logging.getLogger(__name__)

VERSIONS_DIR = "versions"
# A rebuild after kickoff is ``retrospective`` (RunContext: the wall clock is past the
# first kickoff). Before kickoff it is ``replay``. Both carry ``replay=True`` and are
# bundle-only. ``live`` and ``fixture`` are never accepted.
REPLAY_MODES = ("replay", "retrospective")


class RebuildRefused(RuntimeError):
    """The slate is not admissible, or the rebuild would overwrite something."""


@dataclass
class SlateVerdict:
    slate: str
    admissible: bool
    run_id: str | None
    reasons: list[str] = field(default_factory=list)
    entries: list[ar.ArchiveEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"slate": self.slate, "admissible": self.admissible, "run_id": self.run_id,
                "reasons": list(self.reasons),
                "inputs": [asdict(e) for e in self.entries]}


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def admissibility(nfl_dir: Path | str, slates: list[str] | None = None) -> list[SlateVerdict]:
    """One verdict per expected slate (dated pointers plus ``slates``). Read-only."""
    root = Path(nfl_dir)
    report = ar.check_archive(root, slates)
    entries, _ = ar.read_manifest(ar.manifest_path(root))
    manifest_gaps = [g.detail for g in report.gaps if g.kind == "invalid_line"]
    pointers = ar.pointed_runs(root)
    verdicts: list[SlateVerdict] = []
    for slate in report.slates:
        reasons = list(manifest_gaps)
        reasons += [f"{g.kind}: {g.detail}" for g in report.gaps if g.slate == slate]
        run_id = pointers.get(slate)
        mine = [e for e in entries if e.slate == slate and e.run_id == run_id]
        if run_id is None:
            reasons.append("no dated run pointer names an archived run for this slate")
        else:
            reasons += _original_problems(root, mine)
        verdicts.append(SlateVerdict(slate, not reasons, run_id, reasons, mine))
    return verdicts


def _original_problems(root: Path, mine: list[ar.ArchiveEntry]) -> list[str]:
    manifest_entry = next((e for e in mine if e.role == "run_manifest"), None)
    if manifest_entry is None:
        return ["the pointer's run is not archived"]
    try:
        body = json.loads(ar.resolve_path(root, manifest_entry.path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"run manifest unreadable: {exc}"]
    ctx = body.get("context") or {}
    problems = []
    if body.get("status") != "OK":
        problems.append(f"original run status {body.get('status')!r}, not OK (phase 2 gates)")
    if ctx.get("mode") != "live" or ctx.get("replay"):
        problems.append(
            f"original run mode {ctx.get('mode')!r} (replay={ctx.get('replay')}); only a live "
            "pregame run captured its own inputs")
    as_of, first = _parse(ctx.get("as_of_utc")), _parse(ctx.get("first_kickoff_utc"))
    if as_of is None:
        problems.append("original run has no as_of_utc")
    elif first is not None and as_of >= first:
        problems.append(f"original as_of {as_of.isoformat()} is not before first kickoff "
                        f"{first.isoformat()}")
    return problems


# ---------------------------------------------------------------------------
# Archived inputs served as an Outlier client
# ---------------------------------------------------------------------------

def _verified_bytes(root: Path, entry: ar.ArchiveEntry) -> bytes:
    data = ar.resolve_path(root, entry.path).read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise RebuildRefused(f"{entry.path} no longer matches its archived sha256")
    return data


class ArchivedOutlierClient:
    """Serves one archived run's raw feeds in the live client's shapes.

    ``raw/event_markets.json`` is the live pipeline's ``{event_id: [markets]}``
    (game lines and team props already merged, ``pipeline.py`` live path), so it
    is served once under the game-line request and empty for the team-prop one.
    """

    def __init__(self, schedule: Any, event_markets: Any, player_props: Any) -> None:
        self._schedule = schedule
        self._markets = event_markets if isinstance(event_markets, dict) else {}
        self._props = player_props
        self._served: set[str] = set()

    def fetch_schedule(self, *_a: Any, **_k: Any) -> Any:
        return self._schedule

    def fetch_event_markets(self, event_id: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        if event_id in self._served:
            return {"markets": []}
        self._served.add(event_id)
        markets = self._markets.get(event_id)
        if not isinstance(markets, list):
            raise KeyError(f"event {event_id} has no archived markets")
        return {"markets": markets}

    def fetch_player_props(self, *_a: Any, **_k: Any) -> Any:
        return self._props


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------

def new_rebuild_id(slate: str, now: datetime) -> str:
    return f"{slate}-rebuild-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


class UnavailableInRebuild(RuntimeError):
    """A source the rebuild has no archived copy of; the caller treats it as missing."""


class PinnedInputs:
    """External metrics, usage and weather for a rebuild, offline only.

    - nflverse: only the pinned copies in ``versions/<rebuild_id>/nflverse/``
      (``games.csv`` for the schedule cutoff, ``stats_player_week_<season>.csv``
      for usage). Every other URL (NGS, PBP, ffopportunity, ...) raises
      :class:`UnavailableInRebuild` and is recorded in :attr:`refused`.
    - weather: only the original run bundle's ``nfl_weather.json`` records, and
      never a fresh forecast. Without them, weather raises, so the pillar is MISSING.
    """

    def __init__(self, csv_by_url: dict[str, bytes],
                 weather_records: list[dict[str, Any]] | None) -> None:
        self._csv = csv_by_url
        self._weather = weather_records
        self.served: list[str] = []
        self.refused: list[str] = []

    def fetch_rows(self, url: str, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        from outlier_nfl.tape_nflverse import parse_csv_bytes

        if url not in self._csv:
            self.refused.append(url)
            raise UnavailableInRebuild(f"no pinned copy of {url}")
        self.served.append(url)
        return parse_csv_bytes(self._csv[url])

    def external_client(self) -> PinnedInputs:
        return self

    def fetch_csv(self, url: str, *_a: Any, **_k: Any) -> list[dict[str, str]]:
        return self.fetch_rows(url)

    def weather(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        from dataclasses import fields

        from outlier_nfl.weather import GameWeather

        if self._weather is None:
            self.refused.append("weather")
            raise UnavailableInRebuild("no weather archived in the original run bundle")
        names = {f.name for f in fields(GameWeather)}
        out: dict[str, Any] = {}
        for rec in self._weather:
            kw = {k: v for k, v in rec.items() if k in names}
            kw["tags"] = tuple(kw.get("tags") or ())
            w = GameWeather(**kw)
            out[w.event_id] = w
        return out


def _stage_nflverse(root: Path, out: Path, season: int,
                    entries: list[ar.ArchiveEntry]) -> list[dict[str, Any]]:
    """Copy the season's archived nflverse files into ``out/nflverse/`` under their names.

    For each CSV, the newest archived copy (by ``fetched_at``) is chosen, together
    with the sidecar from the same ``<sha256>`` folder. Each copy is checked against
    its manifest entry. #252's pinned loaders then verify that folder (sidecar hash
    and schema). ``archive/nflverse/`` itself and the default cache are never passed.
    """
    from outlier_nfl.boxscore_nflverse import ensure_games_csv, ensure_week_stats_csv

    dest_dir = out / "nflverse"
    used: list[dict[str, Any]] = []
    for name, by_season in ((f"stats_player_week_{season}.csv", True), ("games.csv", False)):
        csvs = [e for e in entries if e.role == ar.NFLVERSE_ROLE and Path(e.path).name == name
                and (e.season == season or not by_season)]
        if not csvs:
            continue
        csv = max(csvs, key=lambda e: (e.fetched_at or "", e.path))
        folder = Path(csv.path).parent.as_posix()
        side = next((e for e in entries if e.role == ar.NFLVERSE_ROLE
                     and e.path == f"{folder}/{name}.meta.json"), None)
        if side is None:
            continue
        for e in (csv, side):
            data = _verified_bytes(root, e)
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / Path(e.path).name).write_bytes(data)
            if hashlib.sha256((dest_dir / Path(e.path).name).read_bytes()).hexdigest() != e.sha256:
                raise RebuildRefused(f"staged copy of {e.path} does not match its entry")
            used.append({"name": Path(e.path).name, "archive_path": e.path,
                         "sha256": e.sha256, "fetched_at": e.fetched_at})
    names = {u["name"] for u in used}
    if f"stats_player_week_{season}.csv" in names:
        ensure_week_stats_csv(season, cache_dir=dest_dir, pinned=True)
    if "games.csv" in names:
        ensure_games_csv(cache_dir=dest_dir, pinned=True)
    return used


def _archived_weather(root: Path, run_id: str,
                      run_manifest: dict[str, Any]) -> list[Any] | None:
    path = root / "runs" / run_id / "nfl_weather.json"
    sealed = {a.get("name"): a.get("sha256") for a in run_manifest.get("artifacts") or []}
    if "nfl_weather.json" not in sealed or not path.is_file():
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sealed["nfl_weather.json"]:
        raise RebuildRefused(f"{path} does not match the sha256 sealed in its run manifest")
    records = json.loads(data).get("records")
    return records if isinstance(records, list) else None


def context_labels(sources: dict[str, str]) -> dict[str, list[str]]:
    """Extra cause labels for differences the rebuild's evidence sources explain.

    nflverse data (usage, NGS, PBP and the schedule cutoff) can differ upstream
    from what the original run fetched, so ``evidence_changed:nflverse`` goes on
    every opportunity, matchup or probability difference. Weather and the tape
    (injuries) feed the ``injury_weather`` pillar. They are labelled when the
    rebuild had no archived copy.
    """
    out: dict[str, list[str]] = {
        "evidence_changed:opportunity": ["evidence_changed:nflverse"],
        "evidence_changed:matchup": ["evidence_changed:nflverse"],
        "probability_changed": ["evidence_changed:nflverse"],
    }
    iw: list[str] = []
    if sources.get("weather") != "archived":
        iw.append("evidence_changed:weather")
    if sources.get("tape") != "archived":
        iw.append("evidence_changed:injury")
    if iw:
        out["evidence_changed:injury_weather"] = iw
    return out


def rebuild_slate(
    nfl_dir: Path | str, slate: str, *, rebuild_id: str | None = None,
    clock: Any = None, code_version: str | None = None,
) -> dict[str, Any]:
    """Rebuild one admissible slate under ``versions/<rebuild_id>/``; returns its manifest."""
    from outlier_nfl.external.schedule import SCHEDULE_URL
    from outlier_nfl.pipeline import NflPipeline
    from outlier_nfl.rebuild_compare import compare_cards, render_markdown
    from outlier_nfl.usage import PLAYER_WEEK_URL

    root = Path(nfl_dir)
    verdict = next((v for v in admissibility(root, [slate]) if v.slate == slate), None)
    if verdict is None or not verdict.admissible:
        reasons = verdict.reasons if verdict else ["slate unknown to the archive"]
        raise RebuildRefused(f"slate {slate} is not admissible: " + "; ".join(reasons))
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    rid = rebuild_id or new_rebuild_id(slate, now)
    if "/" in rid or "\\" in rid or rid in {"", ".", ".."}:
        raise RebuildRefused(f"invalid rebuild id {rid!r}")
    out = root / VERSIONS_DIR / rid
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise RebuildRefused(f"{out} already exists; a rebuild never overwrites a version") from None

    by_role = {e.role: e for e in verdict.entries}
    raw = {role: json.loads(_verified_bytes(root, by_role[role]))
           for role in ("pregame_schedule", "pregame_event_markets", "pregame_player_props")}
    original_manifest = json.loads(_verified_bytes(root, by_role["run_manifest"]))
    ctx = original_manifest["context"]
    season = int(ctx["season"])
    run_id = verdict.run_id or ""

    all_entries, _ = ar.read_manifest(ar.manifest_path(root))
    nflverse_used = _stage_nflverse(root, out, season, all_entries)
    csv_by_url: dict[str, bytes] = {}
    for u in nflverse_used:
        data = (out / "nflverse" / u["name"]).read_bytes()
        if u["name"] == "games.csv":
            csv_by_url[SCHEDULE_URL] = data
        elif u["name"] == f"stats_player_week_{season}.csv":
            csv_by_url[PLAYER_WEEK_URL.format(season=season)] = data

    work = out / "work"
    tape_entry = by_role.get(ar.TAPE_ROLE)
    if tape_entry is not None:  # phase 1's tape envelope gate still decides admission
        dest = work / "NFL" / "tape" / "prior_week.json"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(_verified_bytes(root, tape_entry))
    weather = _archived_weather(root, run_id, original_manifest)
    inputs = PinnedInputs(csv_by_url, weather)
    client = ArchivedOutlierClient(raw["pregame_schedule"], raw["pregame_event_markets"],
                                   raw["pregame_player_props"])
    summary = NflPipeline(client=client, data_dir=work, clock=lambda: now,  # type: ignore[arg-type]
                          inputs=inputs).run(
        date=str(ctx["slate_date"]), window=ctx.get("window"), as_of_utc=ctx["as_of_utc"],
        reports_dir=out / "reports")
    rebuilt_run = Path(summary["run_dir"])
    rebuilt_manifest = json.loads((rebuilt_run / "manifest.json").read_text(encoding="utf-8"))
    rctx = rebuilt_manifest["context"]
    if not (rctx.get("replay") and rctx.get("mode") in REPLAY_MODES
            and summary.get("publication") == "bundle_only"):
        raise RebuildRefused(
            f"rebuild ran as mode {rctx.get('mode')!r} (replay={rctx.get('replay')}, "
            f"publication={summary.get('publication')!r}), not a bundle-only replay; outputs "
            f"left in {out} for inspection")

    sources = {
        "nflverse": "pinned" if nflverse_used else "unavailable",
        "weather": "archived" if weather is not None else "unavailable",
        "tape": "archived" if tape_entry is not None else "unavailable",
    }
    manifest: dict[str, Any] = {
        "rebuild_id": rid,
        "slate": slate,
        "created_utc": now.isoformat(),
        "code_version": code_version,
        "source_run_id": run_id,
        "as_of_utc": ctx["as_of_utc"],
        "inputs": [asdict(e) for e in verdict.entries],
        "nflverse_files": nflverse_used,
        "evidence_sources": sources,
        "refused_sources": sorted(set(inputs.refused)),
        "rebuilt_run_id": summary["run_id"],
        "rebuilt_run_dir": rebuilt_run.relative_to(out).as_posix(),
        "rebuilt_status": summary["status"],
        "rebuilt_mode": rctx["mode"],
        "rebuilt_replay": bool(rctx.get("replay")),
        "publication": summary.get("publication"),
    }
    original_card = _original_card(root, run_id, original_manifest)
    rebuilt_card = json.loads((rebuilt_run / CARD).read_text(encoding="utf-8"))
    comparison = compare_cards(original_card, rebuilt_card, context_labels(sources))
    manifest["comparison"] = {"json": "comparison.json", "markdown": "comparison.md",
                              "differences": len(comparison["differences"]),
                              "cause_counts": comparison["cause_counts"]}
    (out / "comparison.json").write_text(
        json.dumps({**comparison, "rebuild_id": rid, "slate": slate}, indent=2) + "\n",
        encoding="utf-8")
    (out / "comparison.md").write_text(render_markdown(comparison, manifest), encoding="utf-8")
    (out / "rebuild_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


CARD = "nfl_best_bets.json"


def _original_card(root: Path, run_id: str, run_manifest: dict[str, Any]) -> dict[str, Any]:
    """The original run's card, verified against the sha256 its run manifest sealed."""
    path = root / "runs" / run_id / CARD
    sealed = {a.get("name"): a.get("sha256") for a in run_manifest.get("artifacts") or []}
    data = path.read_bytes()
    if sealed.get(CARD) != hashlib.sha256(data).hexdigest():
        raise RebuildRefused(f"{path} does not match the sha256 sealed in its run manifest")
    card: dict[str, Any] = json.loads(data)
    return card
