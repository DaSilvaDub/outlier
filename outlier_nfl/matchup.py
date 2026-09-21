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

from outlier_nfl.config import normalize_team
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
WEAK_QB_GRADE = 60.0
STRONG_PASS_RUSH_GRADE = 70.0
STRONG_PASS_RUSH_SACKS = 3.0
FAVORITE_SPREAD = 5.5
GRIND_TOTAL = 47.5
SHOOTOUT_TOTAL = 50.5
HIGH_RUSH_ADJ = 0.20
HIGH_PASS_FADE_ADJ = -0.20
REC_ADJ = 0.15


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


def _role_player(tape: Mapping[str, Any], team: str, role: str) -> str | None:
    named = _text(tape, role)
    if named:
        return named
    chart = _depth(team)
    if role == "qb":
        return chart.get("starting_qb")
    if role == "rb1":
        rbs = chart.get("rbs") or []
        return rbs[0] if rbs else None
    if role == "te":
        return chart.get("te")
    wrs = list(chart.get("wrs") or [])
    if role == "wr_slot":
        return wrs[1] if len(wrs) > 1 else (wrs[0] if wrs else None)
    if role == "wr_deep":
        return wrs[0] if wrs else None
    return None


def _unit_score(tape: Mapping[str, Any]) -> dict[str, float | None]:
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
    }


def _market_context(game_lines: Iterable[NflGameLine], home: str, away: str) -> dict[str, float]:
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
        total = float(best_total.line or 45.5)
    else:
        total = 45.5

    tt_markets = {"POINTS", "TOTAL_POINTS", "TEAM_TOTAL", "TOTAL"}
    home_tts = [
        g for g in lines
        if g.market_type == "TEAM_PROP" and g.proposition in tt_markets and g.team == home
    ]
    away_tts = [
        g for g in lines
        if g.market_type == "TEAM_PROP" and g.proposition in tt_markets and g.team == away
    ]

    home_tt = float(max(home_tts, key=lambda x: len(x.books or ())).line or 24.0) if home_tts else 24.0
    away_tt = float(max(away_tts, key=lambda x: len(x.books or ())).line or 21.0) if away_tts else 21.0

    return {
        "home_spread": home_spread,
        "total": total,
        "home_tt": home_tt,
        "away_tt": away_tt,
    }


def _is_leaky_run_d(unit: Mapping[str, float | None]) -> bool:
    rush_def = unit.get("rush_defense")
    opp_rush = unit.get("opp_rush_yards_allowed")
    if rush_def is not None and rush_def <= LEAKY_RUN_DEFENSE_GRADE:
        return True
    return opp_rush is not None and opp_rush >= LEAKY_RUN_YARDS_ALLOWED


def _is_strong_rush(unit: Mapping[str, float | None]) -> bool:
    rush_off = unit.get("rush_offense")
    rush_yards = unit.get("rush_yards")
    if rush_off is not None and rush_off >= STRONG_RUSH_GRADE:
        return True
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
    return opp_pass is not None and opp_pass >= LEAKY_PASS_YARDS_ALLOWED


def _is_strong_pass_rush(unit: Mapping[str, float | None]) -> bool:
    grade = unit.get("pass_rush")
    sacks = unit.get("sacks")
    if grade is not None and grade >= STRONG_PASS_RUSH_GRADE:
        return True
    return sacks is not None and sacks >= STRONG_PASS_RUSH_SACKS


def _is_weak_qb(unit: Mapping[str, float | None]) -> bool:
    qb = unit.get("qb_grade")
    if qb is not None:
        return qb < WEAK_QB_GRADE
    pass_off = unit.get("pass_offense")
    if pass_off is not None:
        return pass_off < WEAK_QB_GRADE
    pass_yards = unit.get("pass_yards")
    return pass_yards is not None and pass_yards <= 180.0


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
) -> MatchupScript:
    """Build the highest-probability script and mismatch card for one game."""
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
        rb1 = _eligible(_role_player(attack_tape, attack_team, "rb1"))
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
        qb_unit = home_unit if qb_team == home else away_unit
        qb_tape = home_tape if qb_team == home else away_tape
        if not (_is_strong_pass_rush(rush_unit) and _is_weak_qb(qb_unit)):
            return
        qb = _eligible(_role_player(qb_tape, qb_team, "qb"))
        if not qb:
            return
        mismatches.append(f"{pass_rush_team} pass rush vs {qb}")
        signals.append(
            _signal(
                event_id,
                qb,
                qb_team,
                "PASS_YDS",
                "UNDER",
                "MATCHUP_PASS_SUPPRESS",
                f"{qb} week-1/tape grade vs {pass_rush_team} pass rush",
                "HIGH",
                HIGH_PASS_FADE_ADJ,
            )
        )

    def coverage_leak(pass_team: str, defend_team: str, favorite: bool) -> None:
        defend_unit = home_unit if defend_team == home else away_unit
        if not _is_leaky_pass_d(defend_unit):
            return
        pass_tape = home_tape if pass_team == home else away_tape
        te = _eligible(_role_player(pass_tape, pass_team, "te"))
        slot = _eligible(_role_player(pass_tape, pass_team, "wr_slot"))
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
        total_lean = "UNDER" if total <= GRIND_TOTAL else "OVER"

    home_score = round(ctx["home_tt"])
    away_score = round(ctx["away_tt"])
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
) -> list[MatchupScript]:
    """Build one script per unique event_id on the slate, including games with no lines yet."""
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


def apply_matchup_signals(
    props: list[NflPlayerProp],
    scripts: list[MatchupScript],
) -> list[NflPlayerProp]:
    """Stack matchup mismatch tags and volume adjustments onto calibrated props."""
    by_event = {script.event_id: script for script in scripts}
    updated: list[NflPlayerProp] = []
    for prop in props:
        script = by_event.get(prop.event_id)
        if script is None:
            updated.append(prop)
            continue
        tags = list(prop.calibration_tags)
        protected = bool({"DEFICIT_VOLUME_RISK", "SHELL_COVERAGE_DEEP_HAIRCUT"} & set(tags))
        adj = prop.calibrated_volume_adjustment
        matched = False
        fade = False
        high_over = False
        for signal in script.prop_signals:
            if signal.market != prop.market:
                continue
            if not _names_match(
                signal.player_name,
                prop.player_name,
                signal.team,
                prop.team,
            ):
                continue
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
            updated.append(prop)
            continue
        if fade and "MATCHUP_FADE" not in tags:
            tags.append("MATCHUP_FADE")
        tier = prop.confidence_tier or "STANDARD"
        if high_over and not protected and tier in {"STANDARD", None, ""}:
            tier = "TIER_2_STRONG"
        if adj is not None:
            adj = round(adj, 2)
        updated.append(
            replace(
                prop,
                calibration_tags=tuple(tags),
                calibrated_volume_adjustment=adj,
                confidence_tier=tier,
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
        lines.append(
            f"- **Script:** `{script.script_type}` | "
            f"**Projected:** {home} {script.home_score:.0f}, {away} {script.away_score:.0f}"
        )
        lines.append(f"- **Spread lean:** {script.spread_lean} ({script.home_spread:+.1f} {home})")
        lines.append(f"- **Total lean:** {script.total_lean} {script.total:.1f}")
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
