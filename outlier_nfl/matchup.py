"""Prior-week tape matchup analysis for outlier_nfl.

Runs before (and feeds) player-prop calibration. For every slate game the
module compares each team's recent rush/pass/coverage tape to the opponent,
emits mismatch signals (RB rush, QB pass, slot/TE receiving, spread, total),
and applies those signals onto consensus player props.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from outlier_nfl.config import PROP_TIMES_SACKED, is_team_total, normalize_team
from outlier_nfl.calibration import attach_empirical_model_p
from outlier_nfl.models import NflGameLine, NflPlayerProp
from outlier_nfl.roster import NFL_2026_FULL_DEPTH_CHARTS, get_team_depth_chart

logger = logging.getLogger("outlier_nfl.matchup")

LEAKY_RUN_DEFENSE_GRADE = 45.0
LEAKY_RUN_YARDS_ALLOWED = 150.0
STRONG_RUSH_GRADE = 75.0
STRONG_RUSH_YARDS = 140.0
RUSH_GRADE_GAP = 25.0
LEAKY_PASS_DEFENSE_GRADE = 45.0
LEAKY_PASS_YARDS_ALLOWED = 280.0
STRONG_PASS_RUSH_GRADE = 70.0
STRONG_PASS_RUSH_SACKS = 3.0
FAVORITE_SPREAD = 5.5
GRIND_TOTAL = 47.5
SHOOTOUT_TOTAL = 50.5
HIGH_RUSH_ADJ = 0.20
# Strong pass rush -> QB sacks taken OVER. nflverse 2023-2025: next-game sacks
# 1.08x the QB's own prior rate vs 1.00x otherwise (n=319); passing yards were
# unaffected (r ~ 0), so the old PASS_YDS UNDER target was retired.
PASS_RUSH_SACK_ADJ = 0.08
REC_ADJ = 0.15
# Projected-score boosts (not yet backtested): an offense facing a defense missing
# first-string starters (tape ``defensive_starters_out``), and a run game that
# overpowers the opposing front.
DIDF_SCORE_BOOST = 3.5
# One missing starter is routine (16 of 27 injury-listed teams in 2026 week 4);
# the boost needs at least this many starters out on the same defense.
DIDF_MIN_STARTERS = 2
TRENCH_SCORE_BOOST = 3.5
# Indoor and retractable-roof home venues. In a competitive dome game with a
# trench or defensive-injury edge, totals above DOME_GRIND_TOTAL (up to
# GRIND_TOTAL) lean OVER instead of UNDER.
DOME_TEAMS: frozenset[str] = frozenset(
    {"ARI", "ATL", "DAL", "DET", "HOU", "IND", "LAC", "LAR", "LV", "MIN", "NO"}
)
DOME_GRIND_TOTAL = 43.5
# A favorite laying this many points or fewer keeps the lean when it owns the run game.
TRENCH_PROTECT_SPREAD = 2.5


@dataclass(frozen=True)
class PropSignal:
    """One recommended player or team-prop lean derived from a mismatch."""

    event_id: str
    player_name: str | None
    team: str
    market: str
    side: str
    tag: str
    reason: str
    confidence: str
    volume_adjustment: float


@dataclass(frozen=True)
class MatchupScript:
    """Highest-probability script and prop card for one NFL game."""

    event_id: str
    matchup: str
    home_team: str
    away_team: str
    script_type: str
    spread_lean: str
    total_lean: str
    home_score: float
    away_score: float
    home_spread: float
    total: float
    mismatches: tuple[str, ...]
    prop_signals: tuple[PropSignal, ...]
    notes: tuple[str, ...] = ()
    # market = quoted line; default = synthetic 45.5 / 24 / 21 placeholder
    total_source: str = "market"
    home_tt_source: str = "market"
    away_tt_source: str = "market"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["prop_signals"] = [asdict(s) for s in self.prop_signals]
        payload["mismatches"] = list(self.mismatches)
        payload["notes"] = list(self.notes)
        return payload


def load_prior_week_tape(nfl_dir: Path | str) -> dict[str, dict[str, Any]]:
    """Load prior-week unit tape from data/NFL/tape/prior_week.json or latest.json."""
    root = Path(nfl_dir)
    search_paths = [
        root / "tape" / "prior_week.json",
        root / "tape" / "latest.json",
        Path(__file__).resolve().parent / "tape" / "prior_week_tape.json",
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "nfl" / "prior_week_tape.json",
    ]
    for path in search_paths:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed reading matchup tape %s: %s", path, exc)
            continue
        teams = raw.get("teams", raw) if isinstance(raw, dict) else {}
        if not isinstance(teams, dict):
            continue
        loaded = {}
        for code, row in teams.items():
            if not isinstance(row, dict):
                continue
            canonical = normalize_team(str(code)) or str(code).strip().upper()
            loaded[canonical] = dict(row)
        if loaded:
            return loaded
    return {}


def _num(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _text(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return None


def _depth(team: str) -> dict[str, Any]:
    try:
        return get_team_depth_chart(team)
    except ValueError:
        return dict(NFL_2026_FULL_DEPTH_CHARTS.get(team.upper(), {}))


def _role_player(
    tape: Mapping[str, Any], team: str, role: str, inactive: Iterable[str] = ()
) -> str | None:
    """Tape role, else the depth chart's first player for it who is not ``inactive``.

    When every candidate is inactive the depth-chart default comes back and the
    caller's eligibility check drops it.
    """
    out = set(inactive)

    def active(name: str | None) -> bool:
        return bool(name) and str(name).strip().lower() not in out

    named = _text(tape, role)
    if active(named):
        return named
    chart = _depth(team)
    if role == "qb":
        qb = chart.get("starting_qb")
        return qb if active(qb) else None
    if role == "rb1":
        rbs = chart.get("rbs") or []
        return next((r for r in rbs if active(r)), rbs[0] if rbs else None)
    if role == "te":
        te = chart.get("te")
        return te if active(te) else None
    wrs = list(chart.get("wrs") or [])
    active_wrs = [w for w in wrs if active(w)]
    if role == "wr_slot":
        if len(active_wrs) > 1:
            return active_wrs[1]
        if active_wrs:
            return active_wrs[0]
        return wrs[1] if len(wrs) > 1 else (wrs[0] if wrs else None)
    if role == "wr_deep":
        if active_wrs:
            return active_wrs[0]
        return wrs[0] if wrs else None
    return None


EXPLICIT_GRADES = ("rush_offense", "rush_defense", "pass_defense", "pass_rush")


def _unit_score(tape: Mapping[str, Any]) -> dict[str, float | None]:
    """Unit grades; tape-provided grades are flagged ``<grade>_explicit`` = 1.0.

    Explicit grades (PFF, pressures, EPA, ...) decide their trigger alone; the
    raw yards/sacks fallbacks only apply when a grade was derived from them.
    """
    explicit = {
        f"{g}_explicit": 1.0 if _num(tape, g) is not None else None for g in EXPLICIT_GRADES
    }
    rush_off = _num(tape, "rush_offense")
    rush_def = _num(tape, "rush_defense")
    pass_off = _num(tape, "pass_offense", "qb_grade")
    pass_def = _num(tape, "pass_defense")
    pass_rush = _num(tape, "pass_rush")
    rush_yards = _num(tape, "rush_yards")
    pass_yards = _num(tape, "pass_yards")
    opp_rush = _num(tape, "opp_rush_yards_allowed")
    opp_pass = _num(tape, "opp_pass_yards_allowed")
    sacks = _num(tape, "sacks")
    qb_grade = _num(tape, "qb_grade", "pass_offense")

    if rush_off is None and rush_yards is not None:
        rush_off = max(20.0, min(99.0, 50.0 + (rush_yards - 110.0) * 0.25))
    if rush_def is None and opp_rush is not None:
        rush_def = max(20.0, min(99.0, 90.0 - (opp_rush - 90.0) * 0.30))
    if pass_off is None and pass_yards is not None:
        pass_off = max(20.0, min(99.0, 50.0 + (pass_yards - 220.0) * 0.12))
    if pass_def is None and opp_pass is not None:
        pass_def = max(20.0, min(99.0, 90.0 - (opp_pass - 200.0) * 0.20))
    if pass_rush is None and sacks is not None:
        pass_rush = max(20.0, min(99.0, 45.0 + sacks * 10.0))

    return {
        "rush_offense": rush_off,
        "rush_defense": rush_def,
        "pass_offense": pass_off,
        "pass_defense": pass_def,
        "pass_rush": pass_rush,
        "rush_yards": rush_yards,
        "pass_yards": pass_yards,
        "opp_rush_yards_allowed": opp_rush,
        "opp_pass_yards_allowed": opp_pass,
        "sacks": sacks,
        "qb_grade": qb_grade,
        **explicit,
    }


def _market_context(game_lines: Iterable[NflGameLine], home: str, away: str) -> dict[str, Any]:
    lines = [g for g in game_lines if g.scope == "full_game" and g.line is not None]

    home_spreads = [
        g for g in lines
        if g.market == "SPREAD" and (g.team == home or g.position == "HOME")
    ]
    if home_spreads:
        best_spread = max(
            home_spreads,
            key=lambda x: (len(x.books or ()), -abs(x.best_odds - (-110) if x.best_odds else 999)),
        )
        home_spread = float(best_spread.line or 0.0)
    else:
        home_spread = 0.0

    game_totals = [
        g for g in lines
        if g.market == "TOTAL" and g.market_type == "GAMELINE" and g.position == "OVER"
    ]
    if game_totals:
        best_total = max(
            game_totals,
            key=lambda x: (len(x.books or ()), -abs(x.best_odds - (-110) if x.best_odds else 999)),
        )
        if best_total.line is not None:
            total = float(best_total.line)
            total_source = "market"
        else:
            total = 45.5
            total_source = "default"
    else:
        total = 45.5
        total_source = "default"

    # `proposition` carries the raw feed string (normalizer sets
    # `proposition=str(raw_prop)`), so compare it through the canonical
    # is_team_total() predicate rather than against a handful of literal
    # spellings -- "TEAM_TOTAL_POINTS" / "Team Total Points" are real team
    # totals that a literal set silently drops, falling back to the 24/21
    # placeholder scores.
    team_totals = [
        g for g in lines if g.market_type == "TEAM_PROP" and is_team_total(g.proposition)
    ]
    home_tts = [g for g in team_totals if g.team == home]
    away_tts = [g for g in team_totals if g.team == away]

    if home_tts and max(home_tts, key=lambda x: len(x.books or ())).line is not None:
        home_tt = float(max(home_tts, key=lambda x: len(x.books or ())).line)
        home_tt_source = "market"
    else:
        home_tt = 24.0
        home_tt_source = "default"
    if away_tts and max(away_tts, key=lambda x: len(x.books or ())).line is not None:
        away_tt = float(max(away_tts, key=lambda x: len(x.books or ())).line)
        away_tt_source = "market"
    else:
        away_tt = 21.0
        away_tt_source = "default"

    return {
        "home_spread": home_spread,
        "total": total,
        "home_tt": home_tt,
        "away_tt": away_tt,
        "total_source": total_source,
        "home_tt_source": home_tt_source,
        "away_tt_source": away_tt_source,
    }


def _is_leaky_run_d(unit: Mapping[str, float | None]) -> bool:
    rush_def = unit.get("rush_defense")
    opp_rush = unit.get("opp_rush_yards_allowed")
    if rush_def is not None and rush_def <= LEAKY_RUN_DEFENSE_GRADE:
        return True
    if unit.get("rush_defense_explicit"):
        return False
    return opp_rush is not None and opp_rush >= LEAKY_RUN_YARDS_ALLOWED


def _is_strong_rush(unit: Mapping[str, float | None]) -> bool:
    rush_off = unit.get("rush_offense")
    rush_yards = unit.get("rush_yards")
    if rush_off is not None and rush_off >= STRONG_RUSH_GRADE:
        return True
    if unit.get("rush_offense_explicit"):
        return False
    return rush_yards is not None and rush_yards >= STRONG_RUSH_YARDS


def _rush_gap(attack: Mapping[str, float | None], defend: Mapping[str, float | None]) -> float:
    off = attack.get("rush_offense")
    deff = defend.get("rush_defense")
    if off is None or deff is None:
        return 0.0
    return off - deff


def _is_leaky_pass_d(unit: Mapping[str, float | None]) -> bool:
    pass_def = unit.get("pass_defense")
    opp_pass = unit.get("opp_pass_yards_allowed")
    if pass_def is not None and pass_def <= LEAKY_PASS_DEFENSE_GRADE:
        return True
    if unit.get("pass_defense_explicit"):
        return False
    return opp_pass is not None and opp_pass >= LEAKY_PASS_YARDS_ALLOWED


def _is_strong_pass_rush(unit: Mapping[str, float | None]) -> bool:
    grade = unit.get("pass_rush")
    sacks = unit.get("sacks")
    if grade is not None and grade >= STRONG_PASS_RUSH_GRADE:
        return True
    if unit.get("pass_rush_explicit"):
        return False
    return sacks is not None and sacks >= STRONG_PASS_RUSH_SACKS


def _signal(
    event_id: str,
    player: str | None,
    team: str,
    market: str,
    side: str,
    tag: str,
    reason: str,
    confidence: str,
    adj: float,
) -> PropSignal:
    return PropSignal(
        event_id=event_id,
        player_name=player,
        team=team,
        market=market,
        side=side,
        tag=tag,
        reason=reason,
        confidence=confidence,
        volume_adjustment=adj,
    )


def build_matchup_script(
    event_id: str,
    home_team: str,
    away_team: str,
    game_lines: list[NflGameLine],
    tapes: Mapping[str, Mapping[str, Any]] | None = None,
    injuries: Iterable[str] | None = None,
    defensive_out: Mapping[str, Sequence[str]] | None = None,
) -> MatchupScript:
    """Build the highest-probability script and mismatch card for one game.

    ``injuries`` is the flat inactive list used to drop players from signals;
    ``defensive_out`` maps team -> first-string defenders ruled out and drives
    the defensive-injury score boost.
    """
    home = home_team.strip().upper()
    away = away_team.strip().upper()
    tape_map = {str(k).upper(): dict(v) for k, v in (tapes or {}).items()}
    home_tape = tape_map.get(home, {})
    away_tape = tape_map.get(away, {})
    home_unit = _unit_score(home_tape)
    away_unit = _unit_score(away_tape)
    ctx = _market_context(game_lines, home, away)
    inactive = {name.strip().lower() for name in (injuries or []) if name}

    signals: list[PropSignal] = []
    mismatches: list[str] = []
    notes: list[str] = []
    score_boost: dict[str, float] = {home: 0.0, away: 0.0}

    # Points only: degrading the defense's unit grades as well would re-trigger the
    # trench mismatch below and count the same injuries twice.
    out_by_team = {
        str(t).strip().upper(): [n for n in names if n] for t, names in (defensive_out or {}).items()
    }
    for defend_team, attack_team in ((home, away), (away, home)):
        out = out_by_team.get(defend_team) or []
        if len(out) < DIDF_MIN_STARTERS:
            continue
        score_boost[attack_team] += DIDF_SCORE_BOOST
        names = ", ".join(out[:3])
        mismatches.append(f"{attack_team} offense vs depleted {defend_team} defense ({names})")
        notes.append(
            f"DIDF: {defend_team} missing defensive starters ({names}); "
            f"+{DIDF_SCORE_BOOST:.1f} pts to {attack_team}."
        )

    def _eligible(name: str | None) -> str | None:
        if not name:
            return None
        if name.strip().lower() in inactive:
            return None
        return name

    def rush_mismatch(attack_team: str, defend_team: str) -> None:
        attack_tape = home_tape if attack_team == home else away_tape
        attack_unit = home_unit if attack_team == home else away_unit
        defend_unit = home_unit if defend_team == home else away_unit
        gap = _rush_gap(attack_unit, defend_unit)
        if not (
            (_is_strong_rush(attack_unit) and _is_leaky_run_d(defend_unit)) or gap >= RUSH_GRADE_GAP
        ):
            return
        score_boost[attack_team] += TRENCH_SCORE_BOOST
        notes.append(
            f"Trench mismatch: {attack_team} run game overpowers {defend_team} front; "
            f"+{TRENCH_SCORE_BOOST:.1f} pts to {attack_team}."
        )
        rb1 = _eligible(_role_player(attack_tape, attack_team, "rb1", inactive))
        if not rb1:
            return
        mismatches.append(f"{rb1} rush vs {defend_team} run D")
        signals.append(
            _signal(
                event_id,
                rb1,
                attack_team,
                "RUSH_YDS",
                "OVER",
                "MATCHUP_RUSH_MISMATCH",
                f"{attack_team} rush tape overpowers {defend_team} run defense",
                "HIGH",
                HIGH_RUSH_ADJ,
            )
        )
        signals.append(
            _signal(
                event_id,
                rb1,
                attack_team,
                "ANYTIME_TD",
                "OVER",
                "MATCHUP_RUSH_MISMATCH",
                f"{rb1} goal-line and explosive rush role vs leaky run D",
                "HIGH",
                0.10,
            )
        )

    def pass_suppress(pass_rush_team: str, qb_team: str) -> None:
        rush_unit = home_unit if pass_rush_team == home else away_unit
        qb_tape = home_tape if qb_team == home else away_tape
        # The pass rush drives sacks; a weak-QB gate added nothing in the backtest.
        if not _is_strong_pass_rush(rush_unit):
            return
        qb = _eligible(_role_player(qb_tape, qb_team, "qb", inactive))
        if not qb:
            return
        grade = rush_unit.get("pass_rush")
        mismatches.append(f"{pass_rush_team} pass rush vs {qb}")
        signals.append(
            _signal(
                event_id,
                qb,
                qb_team,
                PROP_TIMES_SACKED,
                "OVER",
                "MATCHUP_PASS_SUPPRESS",
                f"{qb} sacks vs {pass_rush_team} pass rush (grade {grade:.0f})"
                if grade is not None else f"{qb} sacks vs {pass_rush_team} pass rush",
                "MEDIUM",
                PASS_RUSH_SACK_ADJ,
            )
        )

    def coverage_leak(pass_team: str, defend_team: str, favorite: bool) -> None:
        defend_unit = home_unit if defend_team == home else away_unit
        if not _is_leaky_pass_d(defend_unit):
            return
        pass_tape = home_tape if pass_team == home else away_tape
        te = _eligible(_role_player(pass_tape, pass_team, "te", inactive))
        slot = _eligible(_role_player(pass_tape, pass_team, "wr_slot", inactive))
        mismatches.append(f"{pass_team} underneath vs {defend_team} secondary")
        if te:
            signals.append(
                _signal(
                    event_id,
                    te,
                    pass_team,
                    "REC_YDS",
                    "OVER",
                    "MATCHUP_COVERAGE_LEAK",
                    f"{te} seams/YAC vs leaky {defend_team} secondary",
                    "HIGH" if favorite else "MEDIUM",
                    REC_ADJ,
                )
            )
        if slot:
            signals.append(
                _signal(
                    event_id,
                    slot,
                    pass_team,
                    "REC_YDS",
                    "OVER",
                    "MATCHUP_COVERAGE_LEAK",
                    f"{slot} slot volume vs leaky {defend_team} secondary",
                    "MEDIUM",
                    REC_ADJ,
                )
            )

    rush_mismatch(home, away)
    rush_mismatch(away, home)
    pass_suppress(home, away)
    pass_suppress(away, home)
    home_spread = ctx["home_spread"]
    if home_spread < 0:
        favorite_is_home: bool | None = True
        favorite: str | None = home
    elif home_spread > 0:
        favorite_is_home = False
        favorite = away
    else:
        favorite_is_home = None
        favorite = None

    coverage_leak(home, away, favorite=(favorite_is_home is True))
    coverage_leak(away, home, favorite=(favorite_is_home is False))

    abs_spread = abs(home_spread)
    total = ctx["total"]
    has_fav_rush = (
        any(
            s.tag == "MATCHUP_RUSH_MISMATCH" and s.team == favorite and s.market == "RUSH_YDS"
            for s in signals
        )
        if favorite
        else False
    )
    if favorite and abs_spread >= FAVORITE_SPREAD and total <= GRIND_TOTAL and (has_fav_rush or not tape_map):
        script_type = "FRONT_RUNNER_GRIND"
        spread_lean = "HOME" if favorite_is_home else "AWAY"
        total_lean = "UNDER"
        notes.append("Favorite grind: run the ball, cap opponent pass yards, under total.")
    elif total >= SHOOTOUT_TOTAL:
        script_type = "SHOOTOUT"
        spread_lean = (
            "HOME" if (favorite_is_home is True) else ("AWAY" if (favorite_is_home is False) else "NEUTRAL")
        )
        total_lean = "OVER"
        notes.append("High total: both pass games stay on schedule.")
    else:
        script_type = "COMPETITIVE"
        spread_lean = (
            "HOME" if (favorite_is_home is True) else ("AWAY" if (favorite_is_home is False) else "NEUTRAL")
        )
        dome_edge = (
            home in DOME_TEAMS
            and DOME_GRIND_TOTAL < total <= GRIND_TOTAL
            and any(score_boost.values())
        )
        if dome_edge:
            total_lean = "OVER"
            notes.append(
                f"Dome pace ({home}): indoor venue plus a trench/injury edge; "
                f"OVER {total:.1f} instead of the outdoor grind UNDER."
            )
        else:
            total_lean = "UNDER" if total <= GRIND_TOTAL else "OVER"

    # Every script branch already leans the favorite; this pins that lean for short
    # favorites that own the run game so a future branch cannot flip it to the dog.
    if favorite and abs_spread <= TRENCH_PROTECT_SPREAD and has_fav_rush:
        spread_lean = "HOME" if favorite_is_home else "AWAY"
        notes.append(
            f"Trench protection: {favorite} owns the run game at {abs_spread:.1f}; "
            "lean stays on the favorite."
        )

    home_score = round(ctx["home_tt"] + score_boost[home])
    away_score = round(ctx["away_tt"] + score_boost[away])
    if home_score == away_score and abs_spread > 0 and favorite:
        fav_pts = round((total + abs_spread) / 2.0)
        dog_pts = round((total - abs_spread) / 2.0)
        if favorite_is_home:
            home_score, away_score = fav_pts, dog_pts
        else:
            away_score, home_score = fav_pts, dog_pts

    if script_type == "FRONT_RUNNER_GRIND":
        if favorite_is_home is True and home_score <= away_score:
            home_score = away_score + 7
        elif favorite_is_home is False and away_score <= home_score:
            away_score = home_score + 7

    total_source = str(ctx.get("total_source") or "market")
    home_tt_source = str(ctx.get("home_tt_source") or "market")
    away_tt_source = str(ctx.get("away_tt_source") or "market")
    if total_source == "default":
        notes.append("PLACEHOLDER total 45.5 (no quoted game total).")
    if home_tt_source == "default" or away_tt_source == "default":
        notes.append(
            f"PLACEHOLDER team totals "
            f"(home={home_tt_source}, away={away_tt_source}; defaults 24/21)."
        )

    return MatchupScript(
        event_id=event_id,
        matchup=f"{away} @ {home}",
        home_team=home,
        away_team=away,
        script_type=script_type,
        spread_lean=spread_lean,
        total_lean=total_lean,
        home_score=float(home_score),
        away_score=float(away_score),
        home_spread=ctx["home_spread"],
        total=total,
        mismatches=tuple(mismatches),
        prop_signals=tuple(signals),
        notes=tuple(notes),
        total_source=total_source,
        home_tt_source=home_tt_source,
        away_tt_source=away_tt_source,
    )


def _event_team_codes(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return (home, away) canonical team codes from a schedule event."""
    home_val = event.get("home")
    away_val = event.get("away")
    home_obj: dict[str, Any] = home_val if isinstance(home_val, dict) else {}
    away_obj: dict[str, Any] = away_val if isinstance(away_val, dict) else {}
    home_raw = home_obj.get("alias") or home_obj.get("name") or event.get("home_team") or "HOME"
    away_raw = away_obj.get("alias") or away_obj.get("name") or event.get("away_team") or "AWAY"
    home = normalize_team(str(home_raw)) or str(home_raw).strip().upper()
    away = normalize_team(str(away_raw)) or str(away_raw).strip().upper()
    return home, away


def build_matchup_scripts(
    game_lines: list[NflGameLine],
    tapes: Mapping[str, Mapping[str, Any]] | None = None,
    injuries_by_event: Mapping[str, Iterable[str]] | None = None,
    slate_events: Iterable[Mapping[str, Any]] | None = None,
    defensive_out_by_team: Mapping[str, Sequence[str]] | None = None,
) -> list[MatchupScript]:
    """Build one script per unique event_id on the slate, including games with no lines yet.

    ``defensive_out_by_team`` is the tape's ``defensive_starters_out`` block.
    """
    by_event: dict[str, list[NflGameLine]] = {}
    meta: dict[str, tuple[str, str]] = {}
    order: list[str] = []

    for event in slate_events or []:
        event_id = str(event.get("eventId") or event.get("id") or "")
        if not event_id or event_id in meta:
            continue
        home, away = _event_team_codes(event)
        meta[event_id] = (home, away)
        by_event[event_id] = []
        order.append(event_id)

    for line in game_lines:
        if not line.event_id:
            continue
        if line.event_id not in meta:
            meta[line.event_id] = (line.home_team, line.away_team)
            order.append(line.event_id)
        by_event.setdefault(line.event_id, []).append(line)

    scripts: list[MatchupScript] = []
    for event_id in order:
        home, away = meta[event_id]
        injured = (injuries_by_event or {}).get(event_id, ())
        scripts.append(
            build_matchup_script(
                event_id=event_id,
                home_team=home,
                away_team=away,
                game_lines=by_event.get(event_id, []),
                tapes=tapes,
                injuries=injured,
                defensive_out=defensive_out_by_team,
            )
        )
    return scripts


def _names_match(
    signal_name: str | None,
    prop_name: str,
    signal_team: str | None = None,
    prop_team: str | None = None,
) -> bool:
    if not signal_name:
        return False
    if signal_team and prop_team and signal_team.strip().upper() != prop_team.strip().upper():
        return False
    left = signal_name.casefold().split()
    right = prop_name.casefold().split()
    if left == right:
        return True
    if len(left) >= 2 and right[: len(left)] == left:
        return True
    if len(right) >= 2 and left[: len(right)] == right:
        return True
    return False


REGRESSION_TAGS = frozenset({"EFFICIENCY_HOT", "EFFICIENCY_COLD"})


def _direction(signal: PropSignal) -> int:
    return 1 if signal.side == "OVER" else -1


def resolve_signal_conflicts(
    signals: list[PropSignal],
) -> tuple[list[PropSignal], list[str]]:
    """Regression signals win conflicts on the same prop.

    When an efficiency-regression signal and any other signal on one prop point
    in opposite directions, the opposing non-regression signals are dropped and
    reported as ``OVERRIDDEN_<tag>`` audit tags. Same-direction signals stack.
    """
    regression = [s for s in signals if s.tag in REGRESSION_TAGS]
    if not regression:
        return signals, []
    direction = _direction(regression[0])
    kept: list[PropSignal] = []
    overridden: list[str] = []
    for signal in signals:
        if signal.tag not in REGRESSION_TAGS and _direction(signal) != direction:
            overridden.append(f"OVERRIDDEN_{signal.tag}")
            continue
        kept.append(signal)
    return kept, overridden


def apply_matchup_signals(
    props: list[NflPlayerProp],
    scripts: list[MatchupScript],
) -> list[NflPlayerProp]:
    """Stack matchup mismatch tags and volume adjustments onto calibrated props.

    Ensures ``model_p`` is present from empirical hit rates when available
    (preserves an already-set model_p). Never copies implied_probability.
    """
    by_event = {script.event_id: script for script in scripts}
    updated: list[NflPlayerProp] = []
    for prop in props:
        script = by_event.get(prop.event_id)
        if script is None:
            updated.append(attach_empirical_model_p(prop))
            continue
        tags = list(prop.calibration_tags)
        protected = bool({"DEFICIT_VOLUME_RISK", "SHELL_COVERAGE_DEEP_HAIRCUT"} & set(tags))
        adj = prop.calibrated_volume_adjustment
        matched = False
        fade = False
        high_over = False
        candidates = [
            signal
            for signal in script.prop_signals
            if signal.market == prop.market
            and _names_match(signal.player_name, prop.player_name, signal.team, prop.team)
        ]
        candidates, overridden = resolve_signal_conflicts(candidates)
        for tag in overridden:
            if tag not in tags:
                tags.append(tag)
        for signal in candidates:
            matched = True
            if signal.tag not in tags:
                tags.append(signal.tag)
            if signal.side == "UNDER" and prop.position == "OVER":
                adj = (adj or 0.0) + signal.volume_adjustment
                fade = True
            elif signal.side == "OVER" and prop.position == "OVER":
                if protected:
                    continue
                adj = (adj or 0.0) + signal.volume_adjustment
                if signal.confidence == "HIGH":
                    high_over = True
            elif signal.side == "UNDER" and prop.position == "UNDER":
                # Play the under: tag only. Do not apply the fade haircut.
                continue
        if not matched:
            updated.append(attach_empirical_model_p(prop))
            continue
        if fade and "MATCHUP_FADE" not in tags:
            tags.append("MATCHUP_FADE")
        tier = prop.confidence_tier or "STANDARD"
        if high_over and not protected and tier in {"STANDARD", None, ""}:
            tier = "TIER_2_STRONG"
        if adj is not None:
            adj = round(adj, 2)
        updated.append(
            attach_empirical_model_p(
                replace(
                    prop,
                    calibration_tags=tuple(tags),
                    calibrated_volume_adjustment=adj,
                    confidence_tier=tier,
                )
            )
        )
    return updated


def scripts_to_records(scripts: list[MatchupScript]) -> list[dict[str, Any]]:
    return [script.to_dict() for script in scripts]


def render_matchup_markdown(
    script: MatchupScript | None,
    env: Mapping[str, Any] | None = None,
    props: Sequence[Mapping[str, Any]] | None = None,
    date: str = "",
) -> str:
    """Render a per-game betting script from matchup analysis + market env."""
    env = env or {}
    home = (script.home_team if script else env.get("home_team")) or "HOME"
    away = (script.away_team if script else env.get("away_team")) or "AWAY"
    matchup = script.matchup if script else env.get("matchup") or f"{away} @ {home}"
    lines = [
        f"# {matchup} — MATCHUP GAME SCRIPT",
        f"**Date:** {date}",
        "",
        "## 1. Highest-probability outcome",
        "",
    ]
    if script:
        src_bits = []
        if script.total_source == "default":
            src_bits.append("total=default")
        if script.home_tt_source == "default" or script.away_tt_source == "default":
            src_bits.append("team_totals=default")
        src_note = f" ⚠️ PLACEHOLDER ({', '.join(src_bits)})" if src_bits else ""
        lines.append(
            f"- **Script:** `{script.script_type}` | "
            f"**Projected:** {home} {script.home_score:.0f}, {away} {script.away_score:.0f}"
            f"{src_note}"
        )
        # Name the backed side with its own line: "AWAY (ATL -1.5)", not "AWAY (+1.5 NO)".
        if script.spread_lean == "AWAY":
            backed = f"{away} {-script.home_spread:+.1f}"
        else:
            backed = f"{home} {script.home_spread:+.1f}"
        lines.append(f"- **Spread lean:** {script.spread_lean} ({backed})")
        total_tag = " (placeholder)" if script.total_source == "default" else ""
        lines.append(
            f"- **Total lean:** {script.total_lean} {script.total:.1f}{total_tag}"
        )
        if script.mismatches:
            lines.append("- **Mismatches:**")
            for item in script.mismatches:
                lines.append(f"  - {item}")
    spread = env.get("primary_spread_line")
    total = env.get("primary_total_line")
    if spread is not None or total is not None:
        lines.extend(["", "## 2. Consensus market", ""])
        if script is not None:
            lines.append(f"- **Spread:** {home} {script.home_spread:+.1f}")
        elif spread is not None:
            lines.append(f"- **Spread:** {home} {float(spread):+.1f}")
        if total is not None:
            lines.append(f"- **Total:** {total}")

    lines.extend(["", "## 3. Matchup prop signals", ""])
    if script and script.prop_signals:
        lines.append("| Player | Market | Side | Conf | Tag | Why |")
        lines.append("| :--- | :--- | :---: | :---: | :--- | :--- |")
        for signal in script.prop_signals:
            player = signal.player_name or signal.team
            lines.append(
                f"| {player} | {signal.market} | {signal.side} | {signal.confidence} | "
                f"`{signal.tag}` | {signal.reason} |"
            )
    else:
        lines.append("_No prior-week tape mismatches. Market script only._")

    tagged = [
        p
        for p in (props or [])
        if any(str(tag).startswith("MATCHUP_") for tag in (p.get("calibration_tags") or []))
    ]
    if tagged:
        lines.extend(["", "## 4. Calibrated props carrying matchup tags", ""])
        for prop in tagged:
            adj = prop.get("calibrated_volume_adjustment")
            adj_txt = f" adj={adj:+.2f}" if adj is not None else ""
            lines.append(
                f"- **{prop.get('player_name')}** {prop.get('position')} "
                f"{prop.get('market')} {prop.get('line')} "
                f"[{', '.join(prop.get('calibration_tags') or [])}]{adj_txt}"
            )

    if script:
        if script.spread_lean == "AWAY":
            fav = away
            dog = home
            fav_score = script.away_score
            dog_score = script.home_score
        else:
            fav = home
            dog = away
            fav_score = script.home_score
            dog_score = script.away_score
        lines.extend(["", "## 5. Three canonical scripts", ""])
        lines.append(
            f"### Scenario 1: Front-runner grind ({'BASE' if script.script_type == 'FRONT_RUNNER_GRIND' else 'ALT'})"
        )
        lines.append(
            f"- {fav} controls with the run game. Projected {fav} {fav_score:.0f}, {dog} {dog_score:.0f}."
        )
        lines.append(
            f"- Correlated: {fav} spread, UNDER {script.total:.1f}, favorite RB rush OVER / TD."
        )
        lines.append("")
        lines.append(
            f"### Scenario 2: Competitive ({'BASE' if script.script_type == 'COMPETITIVE' else 'ALT'})"
        )
        lines.append(
            f"- One-score game. {dog} stays on schedule; total still leans {script.total_lean}."
        )
        lines.append("")
        lines.append(
            f"### Scenario 3: Shootout / upset ({'BASE' if script.script_type == 'SHOOTOUT' else 'ALT'})"
        )
        lines.append(
            f"- Both QBs push the ball. Only path that reliably wants OVER {script.total:.1f}."
        )
    lines.append("")
    return "\n".join(lines)
