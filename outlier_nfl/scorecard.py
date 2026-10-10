"""Post-slate signal scorecard: grade every matchup/weather/usage signal against results.

Each signal is graded two ways once nflverse posts the slate's box scores:

- ``hit_vs_avg``: did the player go the signal's direction relative to his own
  per-game average from earlier weeks? Compared with a same-week baseline (the
  share of all players who went that direction), since skewed yardage means
  most players finish under their average.
- ``hit_vs_line``: when the slate's calibrated props carried a consensus line
  for that player/market, did the result clear it in the signal's direction?

Graded rows are appended to a JSONL ledger (idempotent per slate date) so the
record accumulates week over week; the summary reads the whole ledger.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date as date_cls
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from outlier_nfl.config import PROP_PASS_YARDS, PROP_TIMES_SACKED
from outlier_nfl.tape_nflverse import _name_key, _team

# Signal market -> nflverse stats_player_week column.
MARKET_COLUMNS: dict[str, str] = {
    "REC_YDS": "receiving_yards",
    "RUSH_YDS": "rushing_yards",
    "REC": "receptions",
    "RECEIVING_TARGETS": "targets",
    "RUSH_ATT": "carries",
    # Constants, not literals: Bandit B105 reads a "PASS..." string key as a password.
    PROP_PASS_YARDS: "passing_yards",
    PROP_TIMES_SACKED: "sacks_suffered",
}
TD_MARKET = "ANYTIME_TD"
MIN_PRIOR_GAMES = 2


@dataclass(frozen=True)
class GradedSignal:
    date: str
    week: int
    event_id: str
    tag: str
    player: str
    team: str
    market: str
    side: str
    prior_avg: float | None
    actual: float
    hit_vs_avg: bool | None
    line: float | None
    hit_vs_line: bool | None
    run_id: str | None = None  # pipeline run whose scripts were graded
    source_sha256: str | None = None  # sha256 of the graded scripts file


def _f(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "NA") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _player_key(team: Any, name: Any) -> tuple[str, str]:
    return _team(team), _name_key(name)


def _actual(row: Mapping[str, str], market: str) -> float | None:
    if market == TD_MARKET:
        return _f(row.get("rushing_tds")) + _f(row.get("receiving_tds"))
    column = MARKET_COLUMNS.get(market)
    return _f(row.get(column)) if column else None


def _direction_hit(side: str, actual: float, reference: float) -> bool | None:
    if actual == reference:
        return None  # push
    return actual > reference if side == "OVER" else actual < reference


def consensus_lines(props: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str, str], float]:
    """(event id, team, player key, market) -> full-game consensus OVER line.

    The event is part of the key because a line belongs to one game: two
    same-named players on the slate must not share a line.

    Only rows flagged ``is_consensus_line`` count (median if several). Alternate
    ladder lines are never used: grading against a low alt line makes every
    under look wrong and every over look right.
    """
    found: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for p in props:
        if p.get("position") != "OVER" or str(p.get("scope") or "full_game") != "full_game":
            continue
        if not p.get("is_consensus_line"):
            continue
        try:
            line = float(p["line"])
        except (KeyError, TypeError, ValueError):
            continue
        found[(
            str(p.get("event_id") or ""),
            *_player_key(p.get("team"), p.get("player_name")),
            str(p.get("market")),
        )].append(line)
    out: dict[tuple[str, str, str, str], float] = {}
    for key, values in found.items():
        values.sort()
        mid = len(values) // 2
        out[key] = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    return out


def lookup_consensus_line(
    lines: Mapping[tuple[str, str, str, str], float],
    event_id: str,
    team: str,
    name: str,
    market: str,
) -> float | None:
    """Consensus line for a signal, tolerating a blank team within its own game.

    ``props.extract_player_props`` leaves ``team`` empty when neither the alias
    table nor the event's home/away id map resolves the feed's team, so keying
    the lookup on an exact team match silently drops those lines and leaves
    ``hit_vs_line`` -- the betting-relevant grade -- blank.

    The codebase's other signal-to-prop joins tolerate a blank team the same
    way, but only inside one game: ``best_bets._matching_signals`` selects
    signals by ``prop.event_id`` first, and ``matchup.apply_matchup_signals``
    works within a single game script, before either reaches
    ``_names_match``'s blank-team check. So the fallback here is scoped to the
    signal's own event -- without that, an unresolved-team prop for one game's
    "Mike Williams" would hand its line to another game's same-named player.
    Two teams carrying the name in the same game stay unmatched unless the
    signal's own team pins it exactly.
    """
    exact = lines.get((event_id, team, name, market))
    if exact is not None:
        return exact
    # Same player and market within this game, keyed by the prop's team. Two
    # entries mean two distinct players, so no fallback is safe.
    in_event = {
        row_team: value
        for (row_event, row_team, row_name, row_market), value in lines.items()
        if row_name == name
        and row_market == market
        and (not row_event or not event_id or row_event == event_id)
    }
    if len(in_event) != 1:
        return None
    (only_team, only_line), = in_event.items()
    # A blank event on the prop side is unknown, not a different game, so a team
    # that still matches exactly is kept rather than dropped.
    return only_line if (not only_team or not team or only_team == team) else None


def grade_signals(
    date: str,
    week: int,
    scripts: Iterable[Mapping[str, Any]],
    player_rows: Iterable[Mapping[str, str]],
    props: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[GradedSignal], list[dict[str, Any]]]:
    """Grade each unique (event, tag, player, market, side) signal.

    Returns graded signals and the ungraded ones (player did not play, market
    has no box-score column, or too few prior games), with a reason.
    """
    rows = [r for r in player_rows if r.get("season_type", "REG") == "REG"]
    current: dict[tuple[str, str], Mapping[str, str]] = {}
    history: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for r in rows:
        key = _player_key(r.get("team"), r.get("player_display_name"))
        wk = int(_f(r.get("week")))
        if wk == week:
            current[key] = r
        elif wk < week:
            history[key].append(r)
    lines = consensus_lines(props)

    graded: list[GradedSignal] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for script in scripts:
        for s in script.get("prop_signals", []):
            ident = (str(s.get("event_id")), str(s.get("tag")), str(s.get("player_name")),
                     str(s.get("market")), str(s.get("side")))
            if ident in seen:
                continue
            seen.add(ident)
            key = _player_key(s.get("team"), s.get("player_name"))
            market, side = str(s.get("market")), str(s.get("side"))
            base = {"tag": s.get("tag"), "player": s.get("player_name"), "market": market}
            row = current.get(key)
            if row is None:
                skipped.append({**base, "reason": "no box score this week"})
                continue
            actual = _actual(row, market)
            if actual is None:
                skipped.append({**base, "reason": "market not gradable from box scores"})
                continue
            prior = history.get(key, [])
            prior_avg: float | None = None
            hit_avg: bool | None = None
            if market == TD_MARKET:
                hit_avg = (actual >= 1) if side == "OVER" else (actual == 0)
            elif len(prior) >= MIN_PRIOR_GAMES:
                prior_avg = round(sum(_actual(r, market) or 0.0 for r in prior) / len(prior), 2)
                hit_avg = _direction_hit(side, actual, prior_avg)
            line = lookup_consensus_line(
                lines, str(s.get("event_id") or ""), key[0], key[1], market
            )
            hit_line = _direction_hit(side, actual, line) if line is not None else None
            if hit_avg is None and hit_line is None and prior_avg is None:
                skipped.append({**base, "reason": "fewer than 2 prior games and no line"})
                continue
            graded.append(GradedSignal(
                date=date, week=week, event_id=ident[0], tag=ident[1],
                player=str(s.get("player_name")), team=key[0], market=market, side=side,
                prior_avg=prior_avg, actual=actual, hit_vs_avg=hit_avg,
                line=line, hit_vs_line=hit_line,
            ))
    return graded, skipped


def direction_baselines(
    week: int, player_rows: Iterable[Mapping[str, str]], markets: Iterable[str]
) -> dict[tuple[str, str], float]:
    """Share of all players (>= 2 prior games) finishing over / under their average."""
    rows = [r for r in player_rows if r.get("season_type", "REG") == "REG"]
    by_player: dict[tuple[str, str], dict[int, Mapping[str, str]]] = defaultdict(dict)
    for r in rows:
        by_player[_player_key(r.get("team"), r.get("player_display_name"))][int(_f(r.get("week")))] = r
    out: dict[tuple[str, str], float] = {}
    for market in markets:
        if market not in MARKET_COLUMNS:
            continue
        over = under = 0
        for games in by_player.values():
            cur = games.get(week)
            prior = [g for wk, g in games.items() if wk < week]
            if cur is None or len(prior) < MIN_PRIOR_GAMES:
                continue
            avg = sum(_actual(g, market) or 0.0 for g in prior) / len(prior)
            if avg <= 0:
                continue
            act = _actual(cur, market) or 0.0
            over += act > avg
            under += act < avg
        if over + under:
            out[(market, "OVER")] = over / (over + under)
            out[(market, "UNDER")] = under / (over + under)
    return out


def update_ledger(
    path: Path,
    graded: list[GradedSignal],
    date: str,
    event_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Replace this date's rows for the graded events; return the whole ledger.

    Only rows for ``date`` whose event is in ``event_ids`` (default: the graded
    signals' events) are replaced, so a partial replay never deletes other
    events' rows for the same date (F11).
    """
    scope = {str(e) for e in event_ids} if event_ids is not None else {g.event_id for g in graded}
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("date") != date or str(rec.get("event_id")) not in scope:
                    rows.append(rec)
    rows.extend(asdict(g) for g in graded)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)
    return rows


def load_slate_records(normalized_dir: Path, kind: str, slate: date_cls) -> list[dict[str, Any]] | None:
    """``records`` from ``nfl_<kind>_<date>.json``; None when the file is missing."""
    if kind not in ("matchup_scripts", "calibrated_props"):
        raise ValueError(f"Unknown slate file kind: {kind}")
    path = normalized_dir / f"nfl_{kind}_{slate.isoformat()}.json"
    if not path.exists():
        return None
    return list(json.loads(path.read_text(encoding="utf-8")).get("records", []))


def write_report(reports_dir: Path, slate: date_cls, text: str) -> Path:
    """Write ``<date>_Signal_Scorecard.md`` under ``reports_dir``; return its path."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{slate.isoformat()}_Signal_Scorecard.md"
    out.write_text(text, encoding="utf-8")
    return out


def summarize(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-tag hit rates vs average and vs line (pushes excluded)."""
    acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        a = acc[str(r["tag"])]
        a["signals"] += 1
        if r.get("hit_vs_avg") is not None:
            a["avg_n"] += 1
            a["avg_hits"] += bool(r["hit_vs_avg"])
        if r.get("hit_vs_line") is not None:
            a["line_n"] += 1
            a["line_hits"] += bool(r["hit_vs_line"])
    out = []
    for tag, a in sorted(acc.items()):
        out.append({
            "tag": tag,
            "signals": a["signals"],
            "vs_avg": f"{a['avg_hits']}/{a['avg_n']}" if a["avg_n"] else "-",
            "vs_avg_rate": round(a["avg_hits"] / a["avg_n"], 3) if a["avg_n"] else None,
            "vs_line": f"{a['line_hits']}/{a['line_n']}" if a["line_n"] else "-",
            "vs_line_rate": round(a["line_hits"] / a["line_n"], 3) if a["line_n"] else None,
        })
    return out


def render_markdown(
    date: str,
    week: int,
    slate: list[dict[str, Any]],
    cumulative: list[dict[str, Any]],
    baselines: Mapping[tuple[str, str], float],
    graded: list[GradedSignal],
    skipped: list[dict[str, Any]],
) -> str:
    def pct(v: float | None) -> str:
        return f"{v:.0%}" if v is not None else "-"

    def table(summary: list[dict[str, Any]]) -> list[str]:
        out = ["| Signal | Graded | vs player avg | vs consensus line |", "|---|---|---|---|"]
        for s in summary:
            out.append(
                f"| {s['tag']} | {s['signals']} | {s['vs_avg']} ({pct(s['vs_avg_rate'])}) | "
                f"{s['vs_line']} ({pct(s['vs_line_rate'])}) |"
            )
        return out

    lines = [f"# NFL Signal Scorecard - {date} (Week {week})", ""]
    lines += ["## This slate", "", *table(slate), ""]
    lines += ["## Cumulative (all graded slates)", "", *table(cumulative), ""]
    if baselines:
        lines += ["## Same-week direction baselines (all players, >= 2 prior games)", "",
                  "| Market | Over avg | Under avg |", "|---|---|---|"]
        for market in sorted({m for m, _ in baselines}):
            lines.append(f"| {market} | {pct(baselines.get((market, 'OVER')))} | "
                         f"{pct(baselines.get((market, 'UNDER')))} |")
        lines.append("")
    lines += ["## Graded signals", "",
              "| Signal | Player | Market | Side | Prior avg | Line | Actual | vs avg | vs line |",
              "|---|---|---|---|---|---|---|---|---|"]

    def mark(v: bool | None) -> str:
        return "-" if v is None else ("HIT" if v else "miss")

    for g in sorted(graded, key=lambda x: (x.tag, x.player)):
        lines.append(
            f"| {g.tag} | {g.player} | {g.market} | {g.side} | "
            f"{'-' if g.prior_avg is None else g.prior_avg} | {'-' if g.line is None else g.line} | "
            f"{g.actual:g} | {mark(g.hit_vs_avg)} | {mark(g.hit_vs_line)} |"
        )
    if skipped:
        reasons: dict[str, int] = defaultdict(int)
        for s in skipped:
            reasons[s["reason"]] += 1
        lines += ["", "Ungraded: " + ", ".join(f"{n} {r}" for r, n in sorted(reasons.items()))]
    lines += ["", "_vs player avg is directional (most players finish under their average, "
              "see baselines); vs consensus line is the betting-relevant grade._", ""]
    return "\n".join(lines)
