"""Slate report: simulate today's games, price the quoted lines, gate, size and report.

A thin layer over the team model (``team_run``), the pricing helpers and the shared-core
sizing. Everything is **SHADOW / UNPROMOTED**: the repo's own backtest found this model
worse than the closing market, so each model-only edge is shown beside a market-anchored one
and the verdict is allowed to be "pass". The lines come from one book with no timestamp
(``ingest.espn_slate``), so nothing here is a verified consensus price.

Gates (a candidate must clear all; ``fails`` lists every one it misses):
F1 pre-tip · F2 fresh results (``compute_team_state``) · F3 teams resolved and venue known ·
F4 price window · F5 probabilities available · F6 EV >= min and quarter-Kelly >= 0.5u ·
F7 EV <= max · F8 model-market gap · F9 EV survives an adverse shift · F10 no fresh injury
news · F11 overround · F12 per-bet/game/day caps, one exposure per side per game.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray

from outlier_nba import team_run
from outlier_nba.ingest.espn_slate import BookLines, InjuryNote, SlateGame
from outlier_nba.market.devig import american_to_decimal, american_to_prob, devig
from outlier_nba.pricing import blend, team_markets
from outlier_nba.sim.engine import SimDraws
from outlier_scrapers.sizing import compute_sizing

BoolArr = NDArray[np.bool_]
Priced = team_markets.Priced


@dataclass(frozen=True)
class SlateConfig:
    n_draws: int = 50_000
    seed: int = 20_260_101
    stress_pts: float = 0.75  # adverse shift of margin/total for the F9 robustness gate
    min_edge: float = 0.02
    max_edge: float = 0.10
    price_min: int = -250
    price_max: int = 150
    max_gap: float = 0.07  # |model - devigged market| probability gap
    max_overround: float = 1.10
    pretip_buffer_min: float = 10.0
    cap_bet: float = 1.0
    cap_game: float = 1.5
    cap_day: float = 3.0
    fresh_injury_hours: float = 48.0
    blend_weight: float = 0.175  # fitted on earlier seasons (docs/nba/phase2-market-backtest.md)


@dataclass(frozen=True)
class Candidate:
    event_id: str
    game: str  # "AWAY@HOME"
    market: str  # ML | SPREAD | TOTAL
    side: str  # HOME | AWAY | OVER | UNDER
    line: float | None  # SPREAD: the home handicap
    team: str
    odds: int
    decimal: float
    p_win: float
    p_push: float
    p_model: float  # win probability among non-push outcomes
    p_market: float  # two-way devigged
    edge: float  # model-only EV per unit
    edge_stress: float
    edge_blend: float  # EV with the model at ``blend_weight`` against the market
    kelly_units: float
    units_raw: float  # sized units if every gate passed, else 0
    fair_odds: str
    breakeven: float | None
    overround: float
    fails: tuple[str, ...] = ()
    units: float = 0.0

    @property
    def codes(self) -> str:
        return ",".join(f.split(" ")[0] for f in self.fails) or "PASS"


@dataclass(frozen=True)
class GameProjection:
    event_id: str
    away: str
    home: str
    tip: datetime
    status: str
    started: bool
    home_pts: float
    away_pts: float
    margin_mean: float
    margin_sd: float
    total_mean: float
    total_sd: float
    total_p10: float
    total_p90: float
    p_home: float
    p_ot: float
    poss: float
    lines: BookLines | None
    injuries_home: tuple[InjuryNote, ...] = ()
    injuries_away: tuple[InjuryNote, ...] = ()


@dataclass(frozen=True)
class Portfolio:
    n_bets: int
    units: float
    ev_units: float
    ev_units_blend: float
    p_profit: float | None
    p5: float | None
    p50: float | None
    p95: float | None
    worst: float | None


@dataclass(frozen=True)
class SlateResult:
    day: str
    run_utc: datetime
    config: SlateConfig
    state_summary: dict[str, Any]
    projections: tuple[GameProjection, ...]
    candidates: tuple[Candidate, ...]
    portfolio: Portfolio
    ratings: dict[str, dict[str, Any]]
    skipped: tuple[str, ...] = ()
    injuries_verified: bool = True


# --------------------------------------------------------------------------- pricing


def price_outcome(draws: SimDraws, market: str, side: str, line: float | None) -> Priced:
    if market == "ML":
        return team_markets.price_moneyline(draws)[side]
    if line is None:
        raise ValueError(f"{market} needs a line")
    if market == "SPREAD":
        return team_markets.price_spread(draws, line)[side]
    return team_markets.price_total(draws, line)[side]


def outcome_arrays(
    draws: SimDraws, market: str, side: str, line: float | None
) -> tuple[BoolArr, BoolArr]:
    """Per-draw ``(win, push)`` of one outcome, from the same draws that priced it."""
    home = np.asarray(draws.home[0], dtype=np.float64)
    away = np.asarray(draws.away[0], dtype=np.float64)
    margin = home - away
    if market == "ML":
        win = margin > 0 if side == "HOME" else margin < 0
        return win, np.zeros_like(win)
    if line is None:
        raise ValueError(f"{market} needs a line")
    if market == "SPREAD":
        adj = margin + line
        return (adj > 0 if side == "HOME" else adj < 0), adj == 0
    total = home + away
    return (total > line if side == "OVER" else total < line), total == line


def _shifted(draws: SimDraws, market: str, side: str, pts: float) -> SimDraws:
    """The draws moved ``pts`` against a bet (margin for sides, total for over/under)."""
    half = pts / 2.0
    if market == "TOTAL":
        dh = da = -half if side == "OVER" else half
    else:
        dh, da = (-half, half) if side == "HOME" else (half, -half)
    # SimDraws is annotated for integer scores; pricing converts to float64, so the fractional
    # shift is safe here and nowhere else.
    return SimDraws(
        home=draws.home.astype(np.float64) + dh,  # type: ignore[arg-type]
        away=draws.away.astype(np.float64) + da,  # type: ignore[arg-type]
        ot=draws.ot,
        poss=draws.poss,
    )


def _fair_odds(p: float) -> str:
    if not 0.0 < p < 1.0:
        return "n/a"
    return f"{round(-100 * p / (1 - p)):+d}" if p >= 0.5 else f"{round(100 * (1 - p) / p):+d}"


def _break_even(
    draws: SimDraws, market: str, side: str, line: float, decimal: float
) -> float | None:
    """Worst half-point line at which the bet still has EV >= 0 at the same price."""
    step = {("SPREAD", "HOME"): -0.5, ("SPREAD", "AWAY"): 0.5, ("TOTAL", "OVER"): 0.5,
            ("TOTAL", "UNDER"): -0.5}[(market, side)]  # fmt: skip
    best: float | None = None
    cur = line
    for _ in range(40):
        if price_outcome(draws, market, side, cur).ev(decimal) < 0:
            break
        best, cur = cur, cur + step
    return best


# --------------------------------------------------------------------------- gates


def _quotes(lines: BookLines) -> list[tuple[str, str, float | None, int, int]]:
    """``(market, side, line, own_price, other_side_price)`` for every quoted side."""
    return [
        ("ML", "HOME", None, lines.ml_home, lines.ml_away),
        ("ML", "AWAY", None, lines.ml_away, lines.ml_home),
        ("SPREAD", "HOME", lines.sp_home_line, lines.sp_home_odds, lines.sp_away_odds),
        ("SPREAD", "AWAY", lines.sp_home_line, lines.sp_away_odds, lines.sp_home_odds),
        ("TOTAL", "OVER", lines.tot_line, lines.tot_over_odds, lines.tot_under_odds),
        ("TOTAL", "UNDER", lines.tot_line, lines.tot_under_odds, lines.tot_over_odds),
    ]


def _started(game: SlateGame, now: datetime, cfg: SlateConfig) -> bool:
    return not game.scheduled or game.tip <= now + timedelta(minutes=cfg.pretip_buffer_min)


def _gate_failures(
    cfg: SlateConfig, *, started: bool, odds: int, overround: float, edge: float, gap: float,
    stress_edge: float, fresh: bool, kelly_units: float, p_final_missing: bool,
    venue_known: bool, injuries_verified: bool,
) -> list[str]:  # fmt: skip
    fails: list[str] = []
    if started:
        fails.append("F1 not pre-tip")
    if not venue_known:
        fails.append("F3 venue unknown (neutral-site flag missing)")
    if not cfg.price_min <= odds < cfg.price_max:
        fails.append(f"F4 price outside {cfg.price_min}/+{cfg.price_max}")
    if p_final_missing:
        fails.append("F5 probability unavailable")
    if not 1.0 <= overround <= cfg.max_overround:
        fails.append("F11 overround out of range")
    if edge < cfg.min_edge:
        fails.append(f"F6 edge < {cfg.min_edge:.0%}")
    elif kelly_units <= 0:
        fails.append("F6 quarter-Kelly rounds to 0u")
    if edge > cfg.max_edge:
        fails.append(f"F7 edge > {cfg.max_edge:.0%} (implausible)")
    if abs(gap) > cfg.max_gap:
        fails.append(f"F8 model-market gap > {cfg.max_gap * 100:.0f} pts")
    if stress_edge <= 0:
        fails.append(f"F9 fails {cfg.stress_pts:g}-pt adverse stress")
    if not injuries_verified:
        fails.append("F10 injury feed unavailable or empty")
    elif fresh:
        fails.append("F10 unmodeled fresh injury news")
    return fails


def evaluate_game(
    draws: SimDraws,
    game: SlateGame,
    injuries_home: tuple[InjuryNote, ...] | list[InjuryNote],
    injuries_away: tuple[InjuryNote, ...] | list[InjuryNote],
    *,
    now: datetime,
    cfg: SlateConfig,
    injuries_verified: bool = True,
) -> list[Candidate]:
    """Every quoted side of the game priced from ``draws`` and run through the gates."""
    if game.lines is None:
        return []
    started = _started(game, now, cfg)
    fresh = any(n.is_fresh(now, cfg.fresh_injury_hours) for n in (*injuries_home, *injuries_away))
    out: list[Candidate] = []
    for market, side, line, odds, other in _quotes(game.lines):
        implied = (american_to_prob(odds), american_to_prob(other))
        decimal = american_to_decimal(odds)
        if implied[0] is None or implied[1] is None or decimal is None:
            continue  # parse_american already rejects stubs; this is a defensive guard
        overround = implied[0] + implied[1]
        p_market = devig([implied[0], implied[1]], "multiplicative")[0]
        priced = price_outcome(draws, market, side, line)
        stress = price_outcome(_shifted(draws, market, side, cfg.stress_pts), market, side, line)
        sizing = compute_sizing(
            decimal, model_prob=priced.p_win, push_prob=priced.p_push, max_units=cfg.cap_bet,
            min_edge=cfg.min_edge, max_edge=cfg.max_edge,
        )  # fmt: skip
        edge = priced.ev(decimal)
        final = blend.final_probability(priced.p_win_given_no_push, p_market, cfg.blend_weight)
        edge_blend = (
            0.0 if final.p_final is None else (1 - priced.p_push) * (final.p_final * decimal - 1)
        )
        kelly = max(sizing.kelly_025_units or 0.0, 0.0)
        fails = _gate_failures(
            cfg, started=started, odds=odds, overround=overround, edge=edge,
            gap=priced.p_win_given_no_push - p_market, stress_edge=stress.ev(decimal),
            fresh=fresh, kelly_units=kelly, p_final_missing=final.p_final is None,
            venue_known=game.neutral is not None, injuries_verified=injuries_verified,
        )  # fmt: skip
        out.append(
            Candidate(
                event_id=game.event_id, game=f"{game.away}@{game.home}", market=market, side=side,
                line=line, team=game.home if side == "HOME" else game.away if side == "AWAY" else "",
                odds=odds, decimal=decimal, p_win=priced.p_win, p_push=priced.p_push,
                p_model=priced.p_win_given_no_push, p_market=p_market, edge=edge,
                edge_stress=stress.ev(decimal), edge_blend=edge_blend, kelly_units=kelly,
                units_raw=0.0 if fails else float(sizing.recommended_units_pre_news or 0.0),
                fair_odds=_fair_odds(priced.p_win_given_no_push),
                breakeven=(
                    _break_even(draws, market, side, line, decimal)
                    if market != "ML" and line is not None and edge > 0
                    else None
                ),
                overround=overround, fails=tuple(fails),
            )
        )  # fmt: skip
    return out


def apply_caps(cands: list[Candidate] | tuple[Candidate, ...], cfg: SlateConfig) -> list[Candidate]:
    """Allocate units best-EV first under the bet/game/day caps (F12); inputs are not mutated.

    ML and spread on the same side of a game are one exposure (the better EV is kept); a
    total is its own exposure, and the opposite side of a game already taken is refused.
    Units are floored to the 0.5 grid.
    """
    order = sorted(
        (i for i, c in enumerate(cands) if not c.fails and c.units_raw > 0),
        key=lambda i: -cands[i].edge,
    )
    out = list(cands)
    taken: set[tuple[str, str]] = set()
    used_game: dict[str, float] = {}
    used_day = 0.0
    for i in order:
        c = cands[i]
        exposure = (c.event_id, "TOTAL" if c.market == "TOTAL" else c.side)
        opposite = (c.event_id, {"HOME": "AWAY", "AWAY": "HOME"}.get(c.side, ""))
        if opposite in taken:
            out[i] = replace(c, units=0.0, fails=(*c.fails, "F12 opposite side already taken"))
            continue
        if exposure in taken:
            out[i] = replace(c, units=0.0, fails=(*c.fails, "F12 same-side exposure already taken"))
            continue
        room = min(c.units_raw, cfg.cap_bet, cfg.cap_game - used_game.get(c.event_id, 0.0),
                   cfg.cap_day - used_day)  # fmt: skip
        units = math.floor(room * 2) / 2
        if units <= 0:
            out[i] = replace(c, units=0.0, fails=(*c.fails, "F12 game/day cap reached"))
            continue
        taken.add(exposure)
        used_game[c.event_id] = used_game.get(c.event_id, 0.0) + units
        used_day += units
        out[i] = replace(c, units=units)
    return out


def portfolio_stats(
    bets: list[Candidate] | tuple[Candidate, ...], draws_by_event: dict[str, SimDraws]
) -> Portfolio:
    """Profit distribution of the card. Same-game bets share draws; games are independent."""
    live = [b for b in bets if b.units > 0]
    if not live:
        return Portfolio(0, 0.0, 0.0, 0.0, None, None, None, None, None)
    n = draws_by_event[live[0].event_id].home.shape[1]
    profit = np.zeros(n)
    for b in live:
        win, push = outcome_arrays(draws_by_event[b.event_id], b.market, b.side, b.line)
        profit += np.where(win, b.units * (b.decimal - 1), np.where(push, 0.0, -b.units))
    return Portfolio(
        n_bets=len(live),
        units=sum(b.units for b in live),
        ev_units=sum(b.units * b.edge for b in live),
        ev_units_blend=sum(b.units * b.edge_blend for b in live),
        p_profit=float((profit > 0).mean()),
        p5=float(np.percentile(profit, 5)),
        p50=float(np.percentile(profit, 50)),
        p95=float(np.percentile(profit, 95)),
        worst=float(profit.min()),
    )


# --------------------------------------------------------------------------- run


def _project(
    draws: SimDraws, game: SlateGame, injuries: dict[str, list[InjuryNote]], started: bool
) -> GameProjection:
    home = draws.home[0].astype(np.float64)
    away = draws.away[0].astype(np.float64)
    margin, total = home - away, home + away
    return GameProjection(
        event_id=game.event_id, away=game.away, home=game.home, tip=game.tip, status=game.status,
        started=started, home_pts=float(home.mean()), away_pts=float(away.mean()),
        margin_mean=float(margin.mean()), margin_sd=float(margin.std()),
        total_mean=float(total.mean()), total_sd=float(total.std()),
        total_p10=float(np.percentile(total, 10)), total_p90=float(np.percentile(total, 90)),
        p_home=float((margin > 0).mean()), p_ot=float((draws.ot[0] > 0).mean()),
        poss=float(draws.poss[0].mean()), lines=game.lines,
        injuries_home=tuple(injuries.get(game.home, ())),
        injuries_away=tuple(injuries.get(game.away, ())),
    )  # fmt: skip


def run_slate(
    state: team_run.TeamState,
    games: list[SlateGame],
    injuries: dict[str, list[InjuryNote]],
    *,
    now: datetime,
    day: str,
    cfg: SlateConfig = SlateConfig(),
    skipped: tuple[str, ...] = (),
    injuries_verified: bool = True,
) -> SlateResult:
    """Simulate every game once, price its quoted lines, gate, cap and summarise."""
    rng = np.random.default_rng(cfg.seed)
    draws_by_event: dict[str, SimDraws] = {}
    projections: list[GameProjection] = []
    cands: list[Candidate] = []
    unsimulated = list(skipped)
    for game in games:
        try:
            # None is unknown, not a home court. F3 already zeros the stake.
            neutral = game.neutral is not False
            draws = team_run.event_draws(
                state, game.home, game.away, day, cfg.n_draws, rng, neutral=neutral
            )
        except (KeyError, ValueError) as exc:  # SimInputError is a ValueError
            unsimulated.append(f"{game.event_id}: {exc}")
            continue
        draws_by_event[game.event_id] = draws
        started = _started(game, now, cfg)
        projections.append(_project(draws, game, injuries, started))
        cands += evaluate_game(
            draws,
            game,
            injuries.get(game.home, ()),
            injuries.get(game.away, ()),
            now=now,
            cfg=cfg,
            injuries_verified=injuries_verified,
        )
    capped = apply_caps(cands, cfg)
    teams = {t for g in games for t in (g.home, g.away)}
    return SlateResult(
        day=day, run_utc=now, config=cfg, state_summary=team_run.state_summary(state),
        projections=tuple(projections), candidates=tuple(capped),
        portfolio=portfolio_stats([c for c in capped if c.units > 0], draws_by_event),
        ratings={r["team"]: r for r in team_run.ratings_rows(state) if r["team"] in teams},
        skipped=tuple(unsimulated), injuries_verified=injuries_verified,
    )  # fmt: skip


# --------------------------------------------------------------------------- output


def _note(n: InjuryNote) -> dict[str, Any]:
    return {"player": n.player, "status": n.status, "note": n.note,
            "reported": n.reported.isoformat() if n.reported else None}  # fmt: skip


def result_to_dict(result: SlateResult) -> dict[str, Any]:
    """JSON-safe form of the whole result (no arrays; every value is a primitive)."""
    board = []
    for p in result.projections:
        row = asdict(p)
        row["tip"] = p.tip.isoformat()
        row["injuries_home"] = [_note(n) for n in p.injuries_home]
        row["injuries_away"] = [_note(n) for n in p.injuries_away]
        board.append(row)
    cands = []
    for c in result.candidates:
        row = asdict(c)
        row["fails"] = list(c.fails)
        cands.append(row)
    return {
        "run_utc": result.run_utc.isoformat(), "day": result.day,
        "n_draws": result.config.n_draws, "seed": result.config.seed,
        "config": asdict(result.config), "state": result.state_summary, "board": board,
        "candidates": cands, "portfolio": asdict(result.portfolio), "ratings": result.ratings,
        "skipped": list(result.skipped), "injuries_verified": result.injuries_verified,
    }  # fmt: skip


def run_stamp(result: SlateResult) -> str:
    """Eastern run time as ``HHMMET``: outputs are per run, never overwritten by a later one."""
    return result.run_utc.astimezone(ZoneInfo("America/New_York")).strftime("%H%MET")


def write_outputs(result: SlateResult, out_dir: Path) -> tuple[Path, Path]:
    """Write ``NBA_Master_Report_<day>_<HHMMET>.md`` and ``nba_master_<day>_<HHMMET>.json``.

    The lines are untimestamped, so each run's files are the only record of what was quoted;
    a later run must not replace them.
    """
    from outlier_nba.slate_render import render_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{result.day}_{run_stamp(result)}"
    md = out_dir / f"NBA_Master_Report_{tag}.md"
    js = out_dir / f"nba_master_{tag}.json"
    md.write_text(render_markdown(result), encoding="utf-8")
    js.write_text(json.dumps(result_to_dict(result), indent=1, sort_keys=True), encoding="utf-8")
    return md, js


def render_markdown(result: SlateResult) -> str:
    from outlier_nba.slate_render import render_markdown as _render

    return _render(result)
