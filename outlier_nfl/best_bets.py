"""Traced NFL best bets: every external metric is followed into the final ranking.

Each candidate prop (consensus line, full game, OVER/UNDER) is scored on six
pillars, and every pillar records the evidence it read, the source file/field,
and the probability change it contributed:

1. ``historical``    L5/L10/L20/season hit rates -> Laplace-shrunk base P(hit);
                     efficiency-regression signals (EFFICIENCY_HOT/COLD).
2. ``opportunity``   usage profile (target/carry share) and Next Gen Stats volume;
                     vacated-volume signals (VACATED_TARGETS/CARRIES).
3. ``matchup``       opponent play-by-play EPA allowed for the prop's unit, the
                     matchup-script mismatch signals, positional sub-grades
                     (opponent pass-rush grade for QBs, via the pass-suppress signal; NGS
                     receiver separation vs league, a capped adjustment).
4. ``injury_weather`` official injury report (Out/Doubtful), kickoff forecast and
                     weather signals; only a game-day refresh counts as current.
5. ``market``        intraweek price history (``outlier_nfl.snapshots``): first-seen
                     vs latest line/price; sharp-book comparison when one is quoted.
6. ``price``         no-vig fair probability from both sides of the line, edge,
                     EV and a capped quarter-Kelly stake.

Pillar statuses: VERIFIED, ESTIMATED, MISSING, STALE, CONTRADICTS. A pick is
VALIDATED only when all six are VERIFIED and the edge is positive; any
CONTRADICTS (or the player being inactive) rejects it; everything else is
PROVISIONAL with the gaps listed.

Deltas are deliberately small and capped (per pillar and in total) because they
are not yet calibrated against settled results. Each signal is counted once, in
one pillar. The opportunity pillar adds no recent-trend delta: the usage
backtest (``outlier_nfl.usage``) found last-2-week volume less predictive than
the season average, so recent volume only gates the pillar (role collapse).

The audit block lists every external signal or metric that reached no prop, so a
silent join failure (like the LONG_PASS weather mismatch) shows up in the report.
Pure functions over plain dicts; no network access and no reasoning models.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean, median, pstdev
from typing import Any, Iterable, Mapping

from outlier_nfl.calibration import compute_shrunk_empirical_model_p, select_empirical_hit_rate
from outlier_nfl.matchup import PropSignal, _names_match, resolve_signal_conflicts
from outlier_nfl.snapshots import prop_key
from outlier_nfl.tape_nflverse import _name_key
from outlier_nfl.utils import to_eastern_date

VERIFIED = "VERIFIED"
ESTIMATED = "ESTIMATED"
MISSING = "MISSING"
STALE = "STALE"
CONTRADICTS = "CONTRADICTS"

VALIDATED = "VALIDATED"
PROVISIONAL = "PROVISIONAL"
REJECTED = "REJECTED"

PILLARS = ("historical", "opportunity", "matchup", "injury_weather", "market", "price")

PILLAR_CAP = 0.03  # max |probability change| one pillar may contribute
TOTAL_CAP = 0.08  # max |sum of pillar changes| on the base probability
SIGNAL_P_PER_VOLUME = 0.25  # a +10% volume signal moves P(hit) by 2.5 pts
EPA_P_PER_Z = 0.01  # one league SD of EPA allowed moves P(hit) by 1 pt
EPA_SHRINK_WEEKS = 2.0  # EPA z shrunk by n / (n + 2) weeks of defense data
CONTRADICT_DELTA = -0.015  # a pillar pulling at least this hard against the side
SEPARATION_P_PER_Z = 0.005  # one SD of receiver separation moves P(hit) by 0.5 pt
SEPARATION_SHRINK_WEEKS = 2.0
ROLE_COLLAPSE = 0.5  # last week's volume under half the player's average

MOVE_LINE_STEP = 0.5
MOVE_IMPLIED_PTS = 1.5
MOVE_DELTA = 0.01
SHARP_BOOKS = frozenset({"pinnacle", "circa", "circasports", "circa sports", "bookmaker", "betcris"})
SHARP_GAP_PTS = 2.0

ASSUMED_OVERROUND = 1.0476  # -110 / -110 two-way market, used only when one side is quoted
QUARTER_KELLY = 0.25
MAX_STAKE = 0.02  # bankroll fraction

SIDES = ("OVER", "UNDER")

PASS_MARKETS = frozenset(
    {"PASS_YDS", "PASS_TD", "PASS_COMP", "PASS_ATT", "LONG_PASS", "INT", "PASS_RUSH_YDS",
     "PASSING_COMPLETIONS", "PASSING_ATTEMPTS", "INTERCEPTIONS_THROWN"}
)
RUSH_MARKETS = frozenset({"RUSH_YDS", "RUSH_ATT", "LONG_RUSH", "RUSH_TD"})
REC_MARKETS = frozenset(
    {"REC_YDS", "REC", "LONG_REC", "REC_TD", "RECEIVING_TARGETS", "TARGETS"}
)
SCRIMMAGE_MARKETS = frozenset({"RUSH_REC_YDS", "ANYTIME_TD"})

# NGS volume stat that measures a player's opportunity for each market family.
NGS_VOLUME: dict[str, str] = dict(
    (("passing", "attempts"), ("rushing", "rush_attempts"), ("receiving", "targets"))
)

# Opponent EPA-allowed column per market family. Built from pairs, not a dict
# literal: Bandit B105 reads a "pass" string key as a hardcoded password.
DEFENSE_EPA_KEY: dict[str, str] = dict(
    (
        ("pass", "pass_epa_per_play"),
        ("rec", "pass_epa_per_play"),
        ("rush", "rush_epa_per_play"),
        ("scrimmage", "epa_per_play"),
    )
)

OPPORTUNITY_TAGS = frozenset({"VACATED_TARGETS", "VACATED_CARRIES"})
HISTORICAL_TAGS = frozenset({"EFFICIENCY_HOT", "EFFICIENCY_COLD"})


def _epa_weight(row: Mapping[str, Any], key: str) -> float:
    plays = float(row.get("plays") or 0)
    pass_rate = _f(row.get("pass_rate"))
    if key == "epa_per_play" or pass_rate is None:
        return plays
    return plays * (pass_rate if key == "pass_epa_per_play" else 1.0 - pass_rate)


def market_family(market: str) -> str | None:
    market = market.upper()
    if market in PASS_MARKETS:
        return "pass"
    if market in RUSH_MARKETS:
        return "rush"
    if market in REC_MARKETS:
        return "rec"
    if market in SCRIMMAGE_MARKETS:
        return "scrimmage"
    return None


def signal_pillar(tag: str) -> str:
    if tag in OPPORTUNITY_TAGS:
        return "opportunity"
    if tag in HISTORICAL_TAGS:
        return "historical"
    if tag.startswith("WEATHER"):
        return "injury_weather"
    return "matchup"


def _clamp(value: float, cap: float) -> float:
    return max(-cap, min(cap, value))


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def american_to_decimal(odds: int) -> float:
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / -odds)


def _pct(value: Any) -> float | None:
    """Implied probability as a 0-1 fraction (feed stores percentages)."""
    p = _f(value)
    if p is None:
        return None
    return p / 100.0 if p > 1.0 else p


@dataclass
class Pillar:
    status: str = MISSING
    delta: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "delta": round(self.delta, 4),
            "evidence": self.evidence,
            "notes": list(self.notes),
        }


@dataclass
class TraceInputs:
    """Everything the ranking reads; each list is the pipeline's own artifact."""

    props: list[dict[str, Any]]
    run_date: str
    scripts: list[dict[str, Any]] = field(default_factory=list)
    external_metrics: list[dict[str, Any]] = field(default_factory=list)
    usage_players: list[dict[str, Any]] = field(default_factory=list)
    weather: list[dict[str, Any]] = field(default_factory=list)
    inactive_by_team: dict[str, list[str]] | None = None  # None: injury report not loaded
    tapes: dict[str, dict[str, Any]] = field(default_factory=dict)
    movement: dict[tuple[str, ...], dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Indexes over the external sources
# ---------------------------------------------------------------------------


class _Sources:
    def __init__(self, inputs: TraceInputs) -> None:
        self.inputs = inputs
        self.used: dict[str, set[Any]] = defaultdict(set)

        self.scripts = {str(s.get("event_id")): s for s in inputs.scripts}
        self.weather = {str(w.get("event_id")): w for w in inputs.weather}

        self.ngs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        defense: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.loaded: dict[str, int] = defaultdict(int)
        for rec in inputs.external_metrics:
            source = str(rec.get("source") or "")
            self.loaded[source] += 1
            if source == "ngs":
                self.ngs[(_name_key(rec.get("player")), str(rec.get("kind")))].append(rec)
            elif source == "pbp" and rec.get("kind") == "team_defense":
                defense[str(rec.get("team") or "")].append(rec)
        self.defense = self._defense_profiles(defense)
        self.separation = self._separation_profiles()

        self.usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in inputs.usage_players:
            self.usage[_name_key(p.get("player"))].append(p)

        self.inactive_keys: dict[str, set[str]] = {
            team: {_name_key(n) for n in names}
            for team, names in (inputs.inactive_by_team or {}).items()
        }

        self.ngs_misses: set[str] = set()
        self.matched_signals: dict[str, list[PropSignal]] = defaultdict(list)
        self.signals: dict[str, list[PropSignal]] = defaultdict(list)
        for script in inputs.scripts:
            for raw in script.get("prop_signals") or []:
                try:
                    sig = PropSignal(**{k: raw.get(k) for k in PropSignal.__dataclass_fields__})
                except TypeError:
                    continue
                self.signals[sig.event_id].append(sig)

    @staticmethod
    def _defense_profiles(
        by_team: Mapping[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        """Play-weighted EPA allowed per team plus league z-scores."""
        profiles: dict[str, dict[str, Any]] = {}
        for team, rows in by_team.items():
            prof: dict[str, Any] = {"weeks": len(rows), "plays": sum(int(r.get("plays") or 0) for r in rows)}
            for key in ("pass_epa_per_play", "rush_epa_per_play", "epa_per_play"):
                # Pass/rush EPA are per pass/rush play, so weight each week by that
                # week's pass/rush plays, not by all plays.
                pairs: list[tuple[float, float]] = []
                for r in rows:
                    value, weight = _f(r.get(key)), _epa_weight(r, key)
                    if value is not None and weight > 0:
                        pairs.append((value, weight))
                total = sum(n for _, n in pairs)
                prof[key] = round(sum(v * n for v, n in pairs) / total, 4) if total else None
            profiles[team] = prof
        for key in ("pass_epa_per_play", "rush_epa_per_play", "epa_per_play"):
            values = [p[key] for p in profiles.values() if p.get(key) is not None]
            if len(values) < 4:
                continue
            mu, sd = mean(values), pstdev(values)
            for prof in profiles.values():
                if prof.get(key) is not None and sd > 0:
                    prof[f"{key}_z"] = round((prof[key] - mu) / sd, 3)
        return profiles

    def _separation_profiles(self) -> dict[str, Any]:
        """League mean/SD of per-receiver average NGS separation (weeks pooled per player)."""
        means = []
        for (_, kind), rows in self.ngs.items():
            if kind != "receiving":
                continue
            values = [v for v in (_f(r.get("avg_separation")) for r in rows) if v is not None]
            if values:
                means.append(mean(values))
        if len(means) < 4:
            return {}
        return {"mean": mean(means), "sd": pstdev(means), "median": median(means), "n": len(means)}

    def ngs_rows(self, player: str, kind: str, team: str | None) -> list[dict[str, Any]]:
        rows = self.ngs.get((_name_key(player), kind), [])
        if team:
            same = [r for r in rows if str(r.get("team") or "") == team]
            rows = same or rows
        return sorted(rows, key=lambda r: int(r.get("week") or 0))

    def usage_profile(self, player: str, team: str | None) -> dict[str, Any] | None:
        rows = self.usage.get(_name_key(player), [])
        same_team = [r for r in rows if team and str(r.get("team") or "") == team]
        if same_team:
            return same_team[0]
        return rows[0] if len(rows) == 1 else None  # ambiguous name collision: no match


# ---------------------------------------------------------------------------
# Pillars
# ---------------------------------------------------------------------------


def _side_sign(position: str) -> int:
    return 1 if position == "OVER" else -1


def _matching_signals(prop: Mapping[str, Any], src: _Sources) -> tuple[list[PropSignal], list[str]]:
    candidates = [
        s
        for s in src.signals.get(str(prop.get("event_id")), [])
        if str(s.market).upper() == str(prop.get("market")).upper()
        and _names_match(s.player_name, str(prop.get("player_name") or ""), s.team, prop.get("team"))
    ]
    return resolve_signal_conflicts(candidates)


def _apply_signal(pillar: Pillar, sig: PropSignal, position: str) -> None:
    aligned = 1 if sig.side == position else -1
    delta = abs(float(sig.volume_adjustment or 0.0)) * SIGNAL_P_PER_VOLUME * aligned
    pillar.delta += delta
    pillar.evidence.setdefault("signals", []).append(
        {"tag": sig.tag, "side": sig.side, "volume_adjustment": sig.volume_adjustment,
         "confidence": sig.confidence, "reason": sig.reason, "delta": round(delta, 4)}
    )


def _historical(prop: Mapping[str, Any]) -> tuple[Pillar, float | None]:
    pillar = Pillar()
    rates = {k: prop.get(f"{k}_hit_rate") for k in ("l5", "l10", "l20", "season")}
    pillar.evidence = {f"{k}_hit_rate": v for k, v in rates.items()}
    shrunk = compute_shrunk_empirical_model_p(
        l5_hit_rate=rates["l5"], l10_hit_rate=rates["l10"],
        l20_hit_rate=rates["l20"], season_hit_rate=rates["season"],
    )
    if shrunk is None:
        pillar.notes.append("no usable hit rate")
        return pillar, None
    selected = select_empirical_hit_rate(
        l5_hit_rate=rates["l5"], l10_hit_rate=rates["l10"],
        l20_hit_rate=rates["l20"], season_hit_rate=rates["season"],
    )
    base = shrunk[0]
    pillar.evidence["base_window"] = selected[2] if selected else None
    pillar.evidence["base_p"] = base
    present = [k for k, v in rates.items() if _f(v) is not None]
    pillar.status = VERIFIED if len(present) >= 2 else ESTIMATED
    if len(present) < 2:
        pillar.notes.append("single hit-rate window; cannot cross-check")
    l5, l10 = _f(rates["l5"]), _f(rates["l10"])
    if l5 is not None and l10 is not None:
        l5 = l5 / 100.0 if l5 > 1 else l5
        l10 = l10 / 100.0 if l10 > 1 else l10
        if l5 < 0.5 and l10 < 0.5:
            pillar.status = CONTRADICTS
            pillar.notes.append("L5 and L10 both below 50% for this side")
    return pillar, base


def _ngs_kinds(market: str) -> tuple[str, ...]:
    fam = market_family(market)
    if fam == "pass":
        return ("passing",)
    if fam == "rush":
        return ("rushing",)
    if fam == "rec":
        return ("receiving",)
    if fam == "scrimmage":
        return ("rushing", "receiving")
    return ()


def _opportunity(prop: Mapping[str, Any], src: _Sources, team: str | None) -> Pillar:
    pillar = Pillar()
    player = str(prop.get("player_name") or "")
    market = str(prop.get("market") or "").upper()
    position = str(prop.get("position") or "").upper()

    weekly: dict[int, float] = defaultdict(float)
    for kind in _ngs_kinds(market):
        for r in src.ngs_rows(player, kind, team):
            vol = _f(r.get(NGS_VOLUME[kind]))
            if vol is not None:
                weekly[int(r.get("week") or 0)] += vol
                src.used["ngs"].add((_name_key(player), kind, r.get("week")))
    if _ngs_kinds(market) and not weekly:
        src.ngs_misses.add(player)
    if weekly:
        weeks = sorted(weekly)
        avg = mean(weekly.values())
        pillar.evidence["ngs_volume_weeks"] = len(weeks)
        pillar.evidence["ngs_volume_avg"] = round(avg, 2)
        pillar.evidence["ngs_volume_last"] = round(weekly[weeks[-1]], 2)
        if len(weeks) >= 2 and avg > 0 and weekly[weeks[-1]] < ROLE_COLLAPSE * avg:
            pillar.evidence["role_collapse"] = True

    profile = src.usage_profile(player, team)
    if profile:
        src.used["usage"].add(_name_key(player))
        for key in ("games", "target_share", "carry_share", "targets_pg", "carries_pg"):
            if profile.get(key) is not None:
                pillar.evidence[f"usage_{key}"] = profile.get(key)

    if not weekly and not profile:
        pillar.notes.append("no NGS volume or usage profile matched this player")
        if market_family(market) is None:
            pillar.notes.append(f"market {market} has no opportunity metric mapped")
        return pillar
    pillar.status = VERIFIED
    if pillar.evidence.get("role_collapse") and position == "OVER":
        pillar.status = CONTRADICTS
        pillar.notes.append("last week's volume under half of the player's average")
    return pillar


def _matchup(
    prop: Mapping[str, Any], src: _Sources, team: str | None, opponent: str | None
) -> Pillar:
    pillar = Pillar()
    market = str(prop.get("market") or "").upper()
    position = str(prop.get("position") or "").upper()
    fam = market_family(market)
    key = DEFENSE_EPA_KEY.get(fam or "")
    defense = src.defense.get(opponent or "") if opponent else None
    if key and defense and defense.get(f"{key}_z") is not None:
        z = defense[f"{key}_z"]
        weeks = int(defense.get("weeks") or 0)
        shrunk = z * weeks / (weeks + EPA_SHRINK_WEEKS)
        delta = shrunk * EPA_P_PER_Z * _side_sign(position)
        pillar.delta += delta
        pillar.evidence.update(
            {"opponent": opponent, f"opp_{key}_allowed": defense.get(key), "opp_epa_z": z,
             "opp_defense_weeks": weeks, "epa_delta": round(delta, 4)}
        )
        src.used["pbp"].add(opponent)
        pillar.status = VERIFIED
    elif key:
        pillar.notes.append(f"no play-by-play defense profile for opponent {opponent or '?'}")

    # Positional sub-grades: real data only, evidence (signals already carry the effect).
    opp_tape = src.inputs.tapes.get(opponent or "", {}) if opponent else {}
    if fam == "pass":
        for grade in ("pass_rush", "pressure_rate"):
            if opp_tape.get(grade) is not None:
                pillar.evidence[f"opp_{grade}"] = opp_tape.get(grade)
        if opp_tape.get("pass_rush") is not None:
            pillar.evidence["opp_pressure_effect"] = "applied via MATCHUP_PASS_SUPPRESS signal"
    if fam == "rec":
        rows = src.ngs_rows(str(prop.get("player_name") or ""), "receiving", team)
        seps = [v for v in (_f(r.get("avg_separation")) for r in rows) if v is not None]
        league = src.separation
        if seps and league and league["sd"] > 0:
            z = (mean(seps) - league["mean"]) / league["sd"]
            shrunk = z * len(seps) / (len(seps) + SEPARATION_SHRINK_WEEKS)
            sep_delta = shrunk * SEPARATION_P_PER_Z * _side_sign(position)
            pillar.delta += sep_delta
            pillar.evidence.update(
                {"ngs_avg_separation": round(mean(seps), 2),
                 "league_median_separation": round(league["median"], 2),
                 "separation_z": round(z, 3), "separation_delta": round(sep_delta, 4)}
            )
    pillar.evidence["ol_vs_dl_grade"] = "not available (no offensive-line grade source)"

    for sig in src.matched_signals.get("matchup", []):
        _apply_signal(pillar, sig, position)
        pillar.status = VERIFIED if pillar.status != CONTRADICTS else pillar.status
    pillar.delta = _clamp(pillar.delta, PILLAR_CAP)
    if pillar.status == VERIFIED and pillar.delta <= CONTRADICT_DELTA:
        pillar.status = CONTRADICTS
        pillar.notes.append("matchup evidence leans against this side")
    return pillar


def _injury_weather(
    prop: Mapping[str, Any], src: _Sources, team: str | None, opponent: str | None,
    event_date: str | None,
) -> tuple[Pillar, bool]:
    pillar = Pillar()
    player_key = _name_key(prop.get("player_name"))
    position = str(prop.get("position") or "").upper()
    disqualified = False
    injury_ok = src.inputs.inactive_by_team is not None
    if injury_ok:
        if team:
            out_keys = src.inactive_keys.get(team, set())
        else:  # team unknown: any team's report naming this player counts
            out_keys = {k for keys in src.inactive_keys.values() for k in keys}
        if player_key in out_keys:
            disqualified = True
            pillar.notes.append("player listed Out/Doubtful on the injury report")
        pillar.evidence["team_inactive"] = sorted((src.inputs.inactive_by_team or {}).get(team or "", []))
        pillar.evidence["opponent_inactive"] = sorted((src.inputs.inactive_by_team or {}).get(opponent or "", []))
        src.used["injury"].add(team)
    else:
        pillar.notes.append("injury report not loaded")

    weather = src.weather.get(str(prop.get("event_id")))
    if weather:
        src.used["weather"].add(str(prop.get("event_id")))
        pillar.evidence["weather"] = {
            k: weather.get(k) for k in ("venue", "wind_mph", "gust_mph", "temp_f", "precip_prob",
                                        "pass_adjustment", "tags")
        }
    else:
        pillar.notes.append("no kickoff forecast for this game")

    for sig in src.matched_signals.get("injury_weather", []):
        _apply_signal(pillar, sig, position)
    pillar.delta = _clamp(pillar.delta, PILLAR_CAP)

    if disqualified:
        pillar.status = CONTRADICTS
    elif not injury_ok or not weather:
        pillar.status = MISSING
    elif event_date != src.inputs.run_date:
        pillar.status = STALE
        pillar.notes.append(
            f"refresh ran {src.inputs.run_date}, game is {event_date}; validates on its game-day run"
        )
    elif pillar.delta <= CONTRADICT_DELTA:
        pillar.status = CONTRADICTS
        pillar.notes.append("weather/injury adjustments lean against this side")
    else:
        pillar.status = VERIFIED
    return pillar, disqualified


def _sharp_proxy(prop: Mapping[str, Any], books: Mapping[str, int]) -> dict[str, Any]:
    sharp = {b: o for b, o in books.items() if b in SHARP_BOOKS}
    if not sharp:
        return {"sharp_money": "NOT_VERIFIED", "note": "no sharp book quoted; no betting-split source"}
    retail = [o for b, o in books.items() if b not in SHARP_BOOKS]
    sharp_p = mean(1.0 / american_to_decimal(o) for o in sharp.values())
    out: dict[str, Any] = {"sharp_books": sorted(sharp), "sharp_implied": round(sharp_p, 4)}
    if retail:
        retail_p = mean(1.0 / american_to_decimal(o) for o in retail)
        gap = (sharp_p - retail_p) * 100
        out["retail_implied"] = round(retail_p, 4)
        out["sharp_minus_retail_pts"] = round(gap, 2)
        out["sharp_money"] = (
            "SHARP_PROXY_SUPPORTS" if gap >= SHARP_GAP_PTS
            else "SHARP_PROXY_AGAINST" if gap <= -SHARP_GAP_PTS else "SHARP_PROXY_NEUTRAL"
        )
    else:
        out["sharp_money"] = "SHARP_ONLY_QUOTE"
    return out


def _market(prop: Mapping[str, Any], src: _Sources) -> Pillar:
    pillar = Pillar()
    position = str(prop.get("position") or "").upper()
    move = src.inputs.movement.get(prop_key(prop))
    books = {
        str(b.get("book")).lower(): int(b["odds"])
        for b in prop.get("books") or []
        if isinstance(b, Mapping) and b.get("book") and isinstance(b.get("odds"), (int, float))
    }
    pillar.evidence["sharp"] = _sharp_proxy(prop, books)
    if not move or int(move.get("snapshots") or 0) < 2:
        pillar.notes.append("fewer than 2 price snapshots this week; no movement history")
        return pillar
    src.used["snapshots"].add(prop_key(prop))
    open_line, last_line = _f(move.get("open_line")), _f(move.get("last_line"))
    open_imp, last_imp = _f(move.get("open_implied")), _f(move.get("last_implied"))
    pillar.evidence.update(
        {k: move.get(k) for k in ("snapshots", "open_taken_at", "open_line", "open_odds",
                                  "last_taken_at", "last_line", "last_odds")}
    )
    direction = 0
    if open_line is not None and last_line is not None and abs(last_line - open_line) >= MOVE_LINE_STEP:
        direction = (1 if last_line > open_line else -1) * _side_sign(position)
        pillar.evidence["line_move"] = round(last_line - open_line, 2)
    elif open_imp is not None and last_imp is not None:
        pts = (last_imp - open_imp) * (100.0 if abs(last_imp) <= 1 and abs(open_imp) <= 1 else 1.0)
        pillar.evidence["implied_move_pts"] = round(pts, 2)
        if abs(pts) >= MOVE_IMPLIED_PTS:
            direction = 1 if pts > 0 else -1
    if direction > 0:
        pillar.delta = MOVE_DELTA
        pillar.status = VERIFIED
        pillar.notes.append("market moved toward this side")
    elif direction < 0:
        pillar.delta = -MOVE_DELTA
        pillar.status = CONTRADICTS
        pillar.notes.append("market moved against this side")
    else:
        pillar.status = VERIFIED
        pillar.notes.append("market stable since first snapshot")
    return pillar


def _opposite_implied(prop: Mapping[str, Any], by_line: Mapping[tuple[Any, ...], Mapping[str, Any]]) -> float | None:
    position = str(prop.get("position") or "").upper()
    other = "UNDER" if position == "OVER" else "OVER"
    key = prop_key({**prop, "position": other}) + (_f(prop.get("line")),)
    row = by_line.get(key)
    return _pct(row.get("implied_probability")) if row else None


def _price(prop: Mapping[str, Any], final_p: float, by_line: Mapping[tuple[Any, ...], Mapping[str, Any]]) -> Pillar:
    pillar = Pillar()
    odds = prop.get("best_odds")
    implied = _pct(prop.get("implied_probability"))
    if odds is None:
        pillar.notes.append("no price quoted")
        return pillar
    dec = american_to_decimal(int(odds))
    if implied is None:
        implied = 1.0 / dec
    opp = _opposite_implied(prop, by_line)
    if opp is not None and implied + opp > 0:
        fair = implied / (implied + opp)
        pillar.evidence["devig"] = "two_sided"
        pillar.evidence["opposite_implied"] = round(opp, 4)
        status = VERIFIED
    else:
        fair = implied / ASSUMED_OVERROUND
        pillar.evidence["devig"] = "assumed_-110_overround"
        pillar.notes.append("opposite side not quoted; fair price assumes standard vig")
        status = ESTIMATED
    edge = final_p - fair
    ev = final_p * dec - 1.0
    b = dec - 1.0
    kelly = (b * final_p - (1.0 - final_p)) / b if b > 0 else 0.0
    stake = max(0.0, min(MAX_STAKE, kelly * QUARTER_KELLY))
    pillar.evidence.update(
        {"best_odds": int(odds), "decimal": round(dec, 4), "implied": round(implied, 4),
         "fair_p": round(fair, 4), "model_p": round(final_p, 4), "edge": round(edge, 4),
         "ev_per_unit": round(ev, 4), "stake_if_validated": round(stake, 4)}
    )
    if edge <= 0:
        pillar.status = CONTRADICTS
        pillar.notes.append("no edge at the best available price")
    else:
        pillar.status = status
    return pillar


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _teams(prop: Mapping[str, Any], src: _Sources) -> tuple[str | None, str | None]:
    team = str(prop.get("team") or "") or None
    opponent = str(prop.get("opponent") or "") or None
    script = src.scripts.get(str(prop.get("event_id")))
    if script and team and not opponent:
        home, away = script.get("home_team"), script.get("away_team")
        opponent = away if team == home else home if team == away else None
    return team, opponent


def is_candidate(prop: Mapping[str, Any]) -> bool:
    return (
        bool(prop.get("is_consensus_line"))
        and str(prop.get("scope") or "full_game") == "full_game"
        and str(prop.get("position") or "").upper() in SIDES
        and prop.get("best_odds") is not None
        and _f(prop.get("line")) is not None
    )


def trace_prop(prop: Mapping[str, Any], src: _Sources, by_line: Mapping[tuple[Any, ...], Mapping[str, Any]]) -> dict[str, Any] | None:
    position = str(prop.get("position") or "").upper()
    team, opponent = _teams(prop, src)
    event_date = to_eastern_date(prop.get("event_starts_at"))

    signals, overridden = _matching_signals(prop, src)
    src.matched_signals.clear()
    for sig in signals:
        src.matched_signals[signal_pillar(sig.tag)].append(sig)
        src.used["signals"].add((sig.event_id, sig.player_name, sig.market, sig.tag))

    historical, base = _historical(prop)
    if base is None:
        return None
    for sig in src.matched_signals.get("historical", []):
        _apply_signal(historical, sig, position)
    _finish_signal_pillar(historical, "regression signal leans against this side")
    if overridden:
        historical.evidence["overridden_signals"] = overridden

    opportunity = _opportunity(prop, src, team)
    for sig in src.matched_signals.get("opportunity", []):
        _apply_signal(opportunity, sig, position)
    _finish_signal_pillar(opportunity, "vacated-volume signal leans against this side")

    matchup = _matchup(prop, src, team, opponent)
    injury, disqualified = _injury_weather(prop, src, team, opponent, event_date)
    market = _market(prop, src)

    pillars = {"historical": historical, "opportunity": opportunity, "matchup": matchup,
               "injury_weather": injury, "market": market}
    total = _clamp(sum(p.delta for p in pillars.values()), TOTAL_CAP)
    final_p = max(0.01, min(0.99, base + total))
    pillars["price"] = _price(prop, final_p, by_line)

    statuses = {name: p.status for name, p in pillars.items()}
    gaps = [f"{name}:{status}" for name, status in statuses.items() if status != VERIFIED]
    if disqualified or CONTRADICTS in statuses.values():
        verdict = REJECTED
    elif all(s == VERIFIED for s in statuses.values()):
        verdict = VALIDATED
    else:
        verdict = PROVISIONAL

    trace = [
        {"step": "base", "pillar": "historical", "source": "outlier hit rates (Laplace a=2)",
         "value": base, "delta": 0.0}
    ]
    for name, p in pillars.items():
        if name == "price":
            continue
        trace.append({"step": name, "pillar": name, "status": p.status, "delta": round(p.delta, 4)})
    trace.append({"step": "total_adjustment", "delta": round(total, 4), "cap": TOTAL_CAP})
    trace.append({"step": "final_p", "value": round(final_p, 4)})

    price = pillars["price"].evidence
    return {
        "verdict": verdict,
        "gaps": gaps,
        "event_id": prop.get("event_id"),
        "event_date": event_date,
        "event_starts_at": prop.get("event_starts_at"),
        "matchup": prop.get("matchup"),
        "team": team,
        "opponent": opponent,
        "player_name": prop.get("player_name"),
        "market": prop.get("market"),
        "position": position,
        "line": prop.get("line"),
        "best_odds": prop.get("best_odds"),
        "best_book": _best_book(prop),
        "confidence_tier": prop.get("confidence_tier"),
        "base_p": round(base, 4),
        "final_p": round(final_p, 4),
        "fair_p": price.get("fair_p"),
        "edge": price.get("edge"),
        "ev_per_unit": price.get("ev_per_unit"),
        "stake_fraction": price.get("stake_if_validated") if verdict == VALIDATED else 0.0,
        "pillars": {name: p.to_dict() for name, p in pillars.items()},
        "trace": trace,
    }


def _finish_signal_pillar(pillar: Pillar, note: str) -> None:
    pillar.delta = _clamp(pillar.delta, PILLAR_CAP)
    if pillar.status in (VERIFIED, ESTIMATED) and pillar.delta <= CONTRADICT_DELTA:
        pillar.status = CONTRADICTS
        pillar.notes.append(note)


def _rank_key(pick: Mapping[str, Any]) -> tuple[int, float]:
    ev = pick.get("ev_per_unit")
    return (VERDICT_ORDER[pick["verdict"]], -(ev if ev is not None else -9.0))


def _best_book(prop: Mapping[str, Any]) -> str | None:
    best = prop.get("best_odds")
    for entry in prop.get("books") or []:
        if isinstance(entry, Mapping) and entry.get("odds") == best:
            return str(entry.get("book"))
    return None


VERDICT_ORDER = {VALIDATED: 0, PROVISIONAL: 1, REJECTED: 2}


def trace_settings() -> dict[str, Any]:
    return {
        "pillar_cap": PILLAR_CAP, "total_cap": TOTAL_CAP,
        "signal_p_per_volume": SIGNAL_P_PER_VOLUME, "epa_p_per_z": EPA_P_PER_Z,
        "separation_p_per_z": SEPARATION_P_PER_Z,
        "quarter_kelly": QUARTER_KELLY, "max_stake": MAX_STAKE,
        "note": "deltas are uncalibrated; tune against nfl_signal_scorecard results",
    }


def build_best_bets(inputs: TraceInputs) -> dict[str, Any]:
    """Trace and rank every candidate; returns the ``nfl_best_bets`` payload."""
    src = _Sources(inputs)
    by_line: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for p in inputs.props:
        by_line.setdefault(prop_key(p) + (_f(p.get("line")),), p)

    picks: list[dict[str, Any]] = []
    for prop in inputs.props:
        if not is_candidate(prop):
            continue
        traced = trace_prop(prop, src, by_line)
        if traced is not None:
            picks.append(traced)
    picks.sort(key=_rank_key)
    for rank, pick in enumerate(picks, 1):
        pick["rank"] = rank

    counts = {v: sum(1 for p in picks if p["verdict"] == v) for v in VERDICT_ORDER}
    return {
        "run_date": inputs.run_date,
        "candidates": len(picks),
        "counts": counts,
        "settings": trace_settings(),
        "picks": picks,
        "audit": _audit(inputs, src, picks),
    }


def _audit(inputs: TraceInputs, src: _Sources, picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Every external input that reached no prop: silent joins become visible."""
    prop_index: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for p in inputs.props:
        prop_index[str(p.get("event_id"))].append(p)
    orphan_signals = []
    for event_id, sigs in src.signals.items():
        for sig in sigs:
            player_props = [
                p for p in prop_index.get(event_id, [])
                if _names_match(sig.player_name, str(p.get("player_name") or ""), sig.team, p.get("team"))
            ]
            if any(str(p.get("market")).upper() == str(sig.market).upper() for p in player_props):
                continue
            # market_not_joined: the player has props but none under this market name --
            # the LONG_PASS class of silent join bug. no_prop_offered: nothing to price.
            orphan_signals.append(
                {"event_id": event_id, "player": sig.player_name, "team": sig.team,
                 "market": sig.market, "tag": sig.tag,
                 "reason": "market_not_joined" if player_props else "no_prop_offered",
                 "player_markets": sorted({str(p.get("market")) for p in player_props})}
            )
    candidate_players = {_name_key(p["player_name"]) for p in picks}
    # Players whose market's NGS kind (receiving/rushing/passing) found no rows.
    no_ngs = sorted(src.ngs_misses)
    events = {str(p.get("event_id")) for p in picks}
    opponents = {p.get("opponent") for p in picks if p.get("opponent")}
    return {
        "external_sources": {
            source: {"loaded": src.loaded.get(source, 0), "consumed_keys": len(src.used.get(source, ()))}
            for source in ("ngs", "pbp", "schedule")
        },
        "usage_profiles": {"loaded": len(inputs.usage_players), "consumed": len(src.used.get("usage", ()))},
        "weather_games": {"loaded": len(inputs.weather), "consumed": len(src.used.get("weather", ()))},
        "signals": {
            "loaded": sum(len(v) for v in src.signals.values()),
            "consumed": len(src.used.get("signals", ())),
            "orphaned": orphan_signals,
        },
        "snapshots": {"props_with_history": len(src.used.get("snapshots", ()))},
        "injury_report_loaded": inputs.inactive_by_team is not None,
        "candidates_without_ngs": no_ngs,
        "candidate_players": len(candidate_players),
        "events_without_weather": sorted(e for e in events if e not in src.weather),
        "opponents_without_pbp_defense": sorted(o for o in opponents if o not in src.defense),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _fmt_p(value: Any) -> str:
    v = _f(value)
    return "-" if v is None else f"{v * 100:.1f}%"


def render_best_bets_markdown(payload: Mapping[str, Any], *, title: str, limit: int = 25) -> str:
    picks = [p for p in payload.get("picks", []) if p["verdict"] != REJECTED][:limit]
    counts = payload.get("counts", {})
    lines = [
        f"# {title}",
        "",
        f"Run date: {payload.get('run_date')} | candidates {payload.get('candidates', 0)} | "
        f"validated {counts.get(VALIDATED, 0)} | provisional {counts.get(PROVISIONAL, 0)} | "
        f"rejected {counts.get(REJECTED, 0)}",
        "",
        "VALIDATED = all six pillars verified on the game-day refresh with a positive edge. "
        "PROVISIONAL picks list their gaps and carry no stake.",
        "",
        "| # | Verdict | Pick | Odds | Base P | Final P | Fair P | Edge | EV/u | Stake | Gaps |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in picks:
        pick = f"{p['player_name']} {p['market']} {p['position']} {p['line']} ({p.get('matchup') or ''})"
        lines.append(
            f"| {p['rank']} | {p['verdict']} | {pick} | {p['best_odds']} {p.get('best_book') or ''} | "
            f"{_fmt_p(p['base_p'])} | {_fmt_p(p['final_p'])} | {_fmt_p(p.get('fair_p'))} | "
            f"{_fmt_p(p.get('edge'))} | {p.get('ev_per_unit')} | {_fmt_p(p.get('stake_fraction'))} | "
            f"{', '.join(p['gaps']) or '-'} |"
        )
    lines += ["", "## Pick traces", ""]
    for p in picks[: min(limit, 15)]:
        lines += [
            f"### {p['rank']}. {p['player_name']} {p['market']} {p['position']} {p['line']} - {p['verdict']}",
            "",
            "| Pillar | Status | Delta | Evidence | Notes |",
            "|---|---|---|---|---|",
        ]
        for name in PILLARS:
            pillar = p["pillars"][name]
            evidence = "; ".join(
                f"{k}={v}" for k, v in pillar["evidence"].items() if k != "signals" and v not in (None, [], {})
            )
            sigs = pillar["evidence"].get("signals") or []
            if sigs:
                evidence += ("; " if evidence else "") + "signals=" + ", ".join(
                    f"{s['tag']}({s['delta']:+.3f})" for s in sigs
                )
            lines.append(
                f"| {name} | {pillar['status']} | {pillar['delta']:+.3f} | {evidence or '-'} | "
                f"{'; '.join(pillar['notes']) or '-'} |"
            )
        lines.append("")
    audit = payload.get("audit", {})
    lines += ["## Data trace audit", ""]
    for source, row in (audit.get("external_sources") or {}).items():
        lines.append(f"- {source}: loaded {row['loaded']}, consumed {row['consumed_keys']} keys")
    for key in ("usage_profiles", "weather_games"):
        row = audit.get(key) or {}
        lines.append(f"- {key}: loaded {row.get('loaded', 0)}, consumed {row.get('consumed', 0)}")
    sig = audit.get("signals") or {}
    lines.append(
        f"- signals: loaded {sig.get('loaded', 0)}, consumed {sig.get('consumed', 0)}, "
        f"orphaned {len(sig.get('orphaned') or [])}"
    )
    orphans = sorted(sig.get("orphaned") or [], key=lambda o: o.get("reason") != "market_not_joined")
    for orphan in orphans[:20]:
        lines.append(
            f"  - ORPHAN {orphan['tag']} {orphan['player']} {orphan['market']} "
            f"[{orphan.get('reason')}] ({orphan['event_id']})"
        )
    lines.append(f"- props with price history: {(audit.get('snapshots') or {}).get('props_with_history', 0)}")
    lines.append(f"- injury report loaded: {audit.get('injury_report_loaded')}")
    for key in ("candidates_without_ngs", "events_without_weather", "opponents_without_pbp_defense"):
        values = audit.get(key) or []
        lines.append(f"- {key}: {len(values)}" + (f" ({', '.join(map(str, values[:12]))})" if values else ""))
    return "\n".join(lines) + "\n"


def merge_payloads(payloads: Iterable[Mapping[str, Any]], run_date: str) -> dict[str, Any]:
    """Combine per-slate-date payloads into one weekly ranking."""
    picks: list[dict[str, Any]] = []
    audits: list[Mapping[str, Any]] = []
    settings: list[Any] = []
    for payload in payloads:
        picks.extend(dict(p) for p in payload.get("picks", []))
        audits.append(payload.get("audit", {}))
        if payload.get("settings") not in settings:
            settings.append(payload.get("settings"))
    if len(settings) > 1:
        raise ValueError(f"slate cards were traced with different settings: {settings}")
    picks.sort(key=_rank_key)
    for rank, pick in enumerate(picks, 1):
        pick["rank"] = rank
    merged_audit: dict[str, Any] = {
        "external_sources": {}, "usage_profiles": {"loaded": 0, "consumed": 0},
        "weather_games": {"loaded": 0, "consumed": 0},
        "signals": {"loaded": 0, "consumed": 0, "orphaned": []}, "snapshots": {"props_with_history": 0},
        "injury_report_loaded": all(a.get("injury_report_loaded") for a in audits) if audits else False,
        "candidates_without_ngs": [], "events_without_weather": [], "opponents_without_pbp_defense": [],
    }
    for a in audits:
        for source, row in (a.get("external_sources") or {}).items():
            agg = merged_audit["external_sources"].setdefault(source, {"loaded": 0, "consumed_keys": 0})
            agg["loaded"] = max(agg["loaded"], row.get("loaded", 0))
            agg["consumed_keys"] += row.get("consumed_keys", 0)
        for key in ("usage_profiles", "weather_games"):
            for k in ("loaded", "consumed"):
                merged_audit[key][k] += (a.get(key) or {}).get(k, 0)
        for k in ("loaded", "consumed"):
            merged_audit["signals"][k] += (a.get("signals") or {}).get(k, 0)
        merged_audit["signals"]["orphaned"] += (a.get("signals") or {}).get("orphaned") or []
        merged_audit["snapshots"]["props_with_history"] += (a.get("snapshots") or {}).get("props_with_history", 0)
        for key in ("candidates_without_ngs", "events_without_weather", "opponents_without_pbp_defense"):
            merged_audit[key] = sorted(set(merged_audit[key]) | set(a.get(key) or []))
    return {
        "run_date": run_date,
        "candidates": len(picks),
        "counts": {v: sum(1 for p in picks if p["verdict"] == v) for v in VERDICT_ORDER},
        "settings": settings[0] if settings else trace_settings(),
        "picks": picks,
        "audit": merged_audit,
    }
