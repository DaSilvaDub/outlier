"""Daily team-model steps: ``ratings`` (fit as of today) and the shadow ``run`` (price the slate).

Everything published here is SHADOW: board ``SHADOW``, units 0, never actionable. The
model blend weight is read from the promotion evaluator and is 0 until a family passes
its gate, so ``final_prob`` is the vig-free market probability and the model's own
probabilities are recorded beside it as evidence. A row with no usable probability is
emitted with a flag and a blank ``final_prob`` rather than a guessed one.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from outlier_nba.identity import resolve_team
from outlier_nba.ingest import outlier as ingest
from outlier_nba.ingest.espn import GameRow, load_games
from outlier_nba.market.devig import american_to_prob
from outlier_nba.pipeline import NbaPipeline, PipelineResult, Stage
from outlier_nba.pricing import blend, team_markets
from outlier_nba.ratings.model import AsOf, TeamModel, TeamParams, predict
from outlier_nba.ratings.possessions import TEAM_INDEX, TEAMS, Dataset, build_dataset
from outlier_nba.receipts import StageReceipt, receipt
from outlier_nba.run_context import AsOfReader, RunContext, make_run_context, nba_season_for_date
from outlier_nba.run_writer import RunWriter
from outlier_nba.sim.engine import SimDraws, SimInputError, SimParams, simulate
from outlier_nba.track import capture as capture_mod
from outlier_nba.track import promotion
from outlier_nba.track.backtest import Predictions, walk_forward
from outlier_scrapers import paths

logger = structlog.get_logger("outlier_nba.team_run")

MODEL_CONFIG_PATH = paths.PROJECT_ROOT / "config" / "nba_team_model.json"
MAX_RESULT_AGE_DAYS = 7  # newest finished game in the store vs the slate date
DEFAULT_DRAWS = 20_000
FAMILY = {
    "ML": "team.ML",
    "SPREAD": "team.SPREAD",
    "TOTAL": "team.TOTAL",
    "TEAM_TOTAL": "team.TEAM_TOTAL",
}
ALT_FAMILY = {
    "SPREAD": "team.ALT_SPREAD",
    "TOTAL": "team.ALT_TOTAL",
    "TEAM_TOTAL": "team.ALT_TEAM_TOTAL",
}
ROW_FIELDS = [
    "event_id", "market_id", "outcome_id", "market", "family", "side", "line", "alt_line",
    "away", "home", "team", "selection", "book", "price", "model_prob", "model_win",
    "model_push", "model_loss", "model_se", "model_fair_decimal", "market_prob",
    "model_weight", "final_prob", "edge_final", "edge_model_only", "flags", "board",
    "units", "actionable", "event_starts_at", "as_of",
]  # fmt: skip


class TeamStateError(RuntimeError):
    """No usable team model for the requested day (missing store, stale results, cold start)."""


@dataclass(frozen=True)
class TeamState:
    ds: Dataset
    model: TeamModel
    asof: AsOf
    preds: Predictions
    params: TeamParams
    slate_day: int
    season: str
    last_game_day: int
    params_source: str

    @property
    def tracker(self):
        return self.preds.noise


def load_params(path: Path = MODEL_CONFIG_PATH) -> tuple[TeamParams, str]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamParams.from_dict(data["params"]), f"tuned:{path.name}"
    return TeamParams(), "UNTUNED_DEFAULTS"


def compute_team_state(
    games: Sequence[GameRow],
    slate_date: str,
    params: TeamParams,
    params_source: str = "given",
    *,
    max_age_days: int = MAX_RESULT_AGE_DAYS,
) -> TeamState:
    if not games:
        raise TeamStateError("the games store is empty")
    ds = build_dataset(list(games))
    slate_day = date.fromisoformat(slate_date).toordinal()
    season = nba_season_for_date(slate_date)
    usable = ds.day < slate_day  # a result can never inform a game on its own day or later
    if not usable.any():
        raise TeamStateError(f"no results before {slate_date}")
    last = int(ds.day[usable].max())
    if slate_day - last > max_age_days:
        raise TeamStateError(
            f"newest result is {slate_day - last} days before {slate_date} "
            f"(limit {max_age_days}); refresh the store"
        )
    past = build_dataset([g for g, u in zip(ds.games, usable) if u])
    preds = walk_forward(past, params)
    if not preds.noise.ready:
        raise TeamStateError(f"only {preds.noise.n_obs} out-of-sample games to estimate noise")
    model = TeamModel(past, params, extra_season=season)
    asof = model.fit_asof(season, slate_day)
    return TeamState(past, model, asof, preds, params, slate_day, season, last, params_source)


def ratings_rows(state: TeamState) -> list[dict[str, Any]]:
    r, p = state.asof.rating, state.asof.pace
    elo = state.preds.elo.r if state.preds.elo is not None else np.full(len(TEAMS), math.nan)
    rows = []
    for t, code in enumerate(TEAMS):
        rows.append(
            {
                "team": code,
                "off_per100": round(float(r.off[t]), 4),
                "def_per100": round(float(r.deff[t]), 4),
                "net_per100": round(float(r.off[t] + r.deff[t]), 4),
                "pace_effect": round(float(p.team[t]), 5),
                "elo": round(float(elo[t]), 2),
            }
        )
    return sorted(rows, key=lambda x: -x["net_per100"])


def state_summary(state: TeamState) -> dict[str, Any]:
    t = state.tracker
    r = state.asof.rating
    return {
        "season": state.season,
        "slate_day": date.fromordinal(state.slate_day).isoformat(),
        "last_result_day": date.fromordinal(state.last_game_day).isoformat(),
        "games_in_store": len(state.ds),
        "season_games_fitted": r.n_games,
        "params_source": state.params_source,
        "league": {"mu": r.mu, "home_per100": r.h, "playoff_shift": r.playoff},
        "noise": {
            "n": t.n,
            "sigma_e": t.sigma_e,
            "rho": t.rho,
            "sigma_p": t.sigma_p,
            "sigma_margin": t.sigma_margin,
            "sigma_total": t.sigma_total,
            "ot_ratio": t.ot_ratio,
        },
    }


# --------------------------------------------------------------------------- pricing


def _decimal(american: Any) -> float | None:
    prob = american_to_prob(int(american)) if american not in ("", None) else None
    return None if prob is None else 1.0 / prob


def _side_for_team(rec: ingest.OutcomeRecord) -> str | None:
    team = resolve_team(rec.team)
    if team is None:
        return None
    return "HOME" if team == rec.home else "AWAY" if team == rec.away else None


def _is_playoff_window(slate_date: str) -> bool:
    d = date.fromisoformat(slate_date)
    return d.month in (5, 6) or (d.month == 4 and d.day >= 15)


def event_draws(
    state: TeamState, home: str, away: str, slate_date: str, n: int, rng: np.random.Generator
) -> SimDraws:
    pr = predict(
        state.asof,
        TEAM_INDEX[home],
        TEAM_INDEX[away],
        playoff=_is_playoff_window(slate_date),
        sigma_p=state.tracker.sigma_p,
    )
    t = state.tracker
    return simulate(
        SimParams.single(
            log_poss=pr.log_poss, sigma_p=t.sigma_p, e_home=pr.e_home, e_away=pr.e_away,
            sigma_e=t.sigma_e, rho=t.rho, ot_ratio=t.ot_ratio,
        ),
        n,
        rng,
    )  # fmt: skip


def _price_record(rec: ingest.OutcomeRecord, draws: SimDraws) -> team_markets.Priced | None:
    if rec.market == "ML":
        return team_markets.price_moneyline(draws)[rec.side]
    if rec.line is None:
        return None
    if rec.market == "SPREAD":
        if rec.side == "HOME":
            return team_markets.price_spread(draws, rec.line)["HOME"]
        return team_markets.price_spread(draws, -rec.line)["AWAY"]  # away handicap = -home
    if rec.market == "TOTAL":
        return team_markets.price_total(draws, rec.line)[rec.side]
    if rec.market == "TEAM_TOTAL":
        side = _side_for_team(rec)
        if side is None:
            return None
        return team_markets.price_team_total(draws, side, rec.line)[rec.side]  # type: ignore[arg-type]
    return None


def _main_lines(
    records: Sequence[ingest.OutcomeRecord], market_probs: Mapping[str, float]
) -> set[str]:
    """Outcome ids on each market's main line: the rung whose consensus is nearest 50/50."""
    best: dict[tuple[str, str, str | None, str], tuple[float, str]] = {}
    for rec in records:
        p = market_probs.get(rec.outcome_id)
        if rec.market == "ML" or p is None or rec.line is None:
            continue
        key = (rec.event_id, rec.market, rec.team, rec.side)
        gap = abs(p - 0.5)
        if key not in best or gap < best[key][0]:
            best[key] = (gap, rec.outcome_id)
    return {oid for _gap, oid in best.values()}


def price_slate(
    state: TeamState,
    records: Sequence[ingest.OutcomeRecord],
    capture: capture_mod.ShadowCapture,
    *,
    slate_date: str,
    captured_at: datetime,
    n_draws: int = DEFAULT_DRAWS,
    seed: int = 20260101,
    promotion_path: Path = promotion.DEFAULT_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One shadow row per quoted team-market outcome (main lines and alt rungs alike)."""
    cons = {
        str(r["outcome_id"]): float(r["market_consensus_prob"])
        for r in capture.rows
        if r["market_consensus_prob"] != ""
    }
    price_by_outcome = {str(r["outcome_id"]): r for r in capture.rows}
    main = _main_lines(records, cons)
    rng = np.random.default_rng(seed)
    draws_by_event: dict[str, SimDraws | None] = {}
    rows: list[dict[str, Any]] = []
    stats: dict[str, int] = defaultdict(int)
    for rec in records:
        if (
            rec.kind not in ("GAME", "TEAM")
            or rec.home not in TEAM_INDEX
            or rec.away not in TEAM_INDEX
        ):
            stats["skipped_unresolved"] += 1
            continue
        if rec.event_id not in draws_by_event:
            try:
                if rec.home is None or rec.away is None:
                    raise ValueError("event teams unresolved")
                draws_by_event[rec.event_id] = event_draws(
                    state, rec.home, rec.away, slate_date, n_draws, rng
                )
            except (SimInputError, KeyError, ValueError) as exc:
                logger.warning("event_sim_failed", event=rec.event_id, error=str(exc))
                draws_by_event[rec.event_id] = None
        draws = draws_by_event[rec.event_id]
        alt = rec.market != "ML" and rec.outcome_id not in main and rec.outcome_id in cons
        family = (ALT_FAMILY if alt else FAMILY)[rec.market] if rec.market != "ML" else FAMILY["ML"]
        flags = ["SHADOW"] + (["ALT_LINE"] if alt else [])
        priced: team_markets.Priced | None = None
        if draws is None:
            flags.append("MODEL_UNAVAILABLE")
        else:
            try:
                priced = _price_record(rec, draws)
            except team_markets.PricingError as exc:
                flags.append(f"PRICING_ERROR:{exc}")
            if priced is None and "MODEL_UNAVAILABLE" not in flags:
                flags.append("UNPRICEABLE_OUTCOME")
        base = price_by_outcome.get(rec.outcome_id, {})
        p_market = cons.get(rec.outcome_id)
        weight = promotion.model_weight(family, promotion_path)
        p_model = None if priced is None else priced.p_win_given_no_push
        final = blend.final_probability(p_model, p_market, weight)
        flags.extend(final.flags)
        flags.extend(
            f for f in str(base.get("data_quality_flags", "")).split(";") if f and f not in flags
        )
        dec = _decimal(base.get("price", ""))
        push = 0.0 if priced is None else priced.p_push
        row = {
            "event_id": rec.event_id, "market_id": rec.market_id, "outcome_id": rec.outcome_id,
            "market": rec.market, "family": family, "side": rec.side, "line": rec.line if rec.line is not None else "",
            "alt_line": str(alt).lower(), "away": rec.away, "home": rec.home, "team": rec.team or "",
            "selection": base.get("selection", ""), "book": base.get("book", ""), "price": base.get("price", ""),
            "model_prob": "" if p_model is None else f"{p_model:.6f}",
            "model_win": "" if priced is None else f"{priced.p_win:.6f}",
            "model_push": "" if priced is None else f"{priced.p_push:.6f}",
            "model_loss": "" if priced is None else f"{priced.p_loss:.6f}",
            "model_se": "" if priced is None else f"{priced.se:.6f}",
            "model_fair_decimal": "" if priced is None or not math.isfinite(priced.fair_decimal) else f"{priced.fair_decimal:.4f}",
            "market_prob": "" if p_market is None else f"{p_market:.6f}",
            "model_weight": weight,
            "final_prob": "" if final.p_final is None else f"{final.p_final:.6f}",
            "edge_final": "" if final.p_final is None or dec is None else f"{(1 - push) * (final.p_final * dec - 1):.6f}",
            "edge_model_only": "" if priced is None or dec is None else f"{priced.ev(dec):.6f}",
            "flags": ";".join(dict.fromkeys(flags)),
            "board": "SHADOW", "units": 0, "actionable": "false",
            "event_starts_at": rec.tip.isoformat(), "as_of": captured_at.isoformat(),
        }  # fmt: skip
        stats["rows"] += 1
        stats["model_priced"] += priced is not None
        stats["final_missing"] += final.p_final is None
        rows.append(row)
    return rows, dict(stats)


def fair_ladders(
    state: TeamState, events: Sequence[Mapping[str, Any]], slate_date: str, n: int, seed: int
) -> list[dict[str, Any]]:
    """Model fair odds on a one-point grid around each event's expected values (alt lines)."""
    rng = np.random.default_rng(seed + 1)
    out: list[dict[str, Any]] = []
    for ev in events:
        home, away = (
            resolve_team((ev.get("home") or {}).get("alias")),
            resolve_team((ev.get("away") or {}).get("alias")),
        )
        if home is None or away is None or home not in TEAM_INDEX or away not in TEAM_INDEX:
            continue
        draws = event_draws(state, home, away, slate_date, n, rng)
        mid_margin = float(np.median(draws.margin))
        mid_total = float(np.median(draws.total))
        mid_h, mid_a = float(np.median(draws.home)), float(np.median(draws.away))
        specs = {
            "SPREAD": (round(-mid_margin - 0.5) + 0.5, "SPREAD"),
            "TOTAL": (math.floor(mid_total) + 0.5, "TOTAL"),
            "HOME_TOTAL": (math.floor(mid_h) + 0.5, "HOME_TOTAL"),
            "AWAY_TOTAL": (math.floor(mid_a) + 0.5, "AWAY_TOTAL"),
        }
        for name, (centre, kind) in specs.items():
            lines = [centre + k for k in range(-10, 11)]
            ladder = team_markets.price_ladder(draws, kind, lines)  # type: ignore[arg-type]
            for line, sides in ladder.items():
                for side, pr in sides.items():
                    out.append(
                        {
                            "event_id": ev.get("eventId") or ev.get("id"), "home": home, "away": away,
                            "ladder": name, "line": line, "side": side,
                            "p_win": round(pr.p_win, 5), "p_push": round(pr.p_push, 5),
                            "fair_decimal": round(pr.fair_decimal, 4) if math.isfinite(pr.fair_decimal) else "",
                        }
                    )  # fmt: skip
    return out


def rows_to_csv(rows: Sequence[Mapping[str, Any]], fields: Sequence[str] = ROW_FIELDS) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ------------------------------------------------------------------------- pipeline


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_team_shadow(
    client: ingest.NbaClient,
    *,
    games_loader: Callable[[], Sequence[GameRow]],
    nba_dir: Path,
    published_dir: Path | None = None,
    clock: Callable[[], datetime] = _utc_now,
    fixture: bool = False,
    params: TeamParams | None = None,
    params_source: str = "given",
    n_draws: int = DEFAULT_DRAWS,
    promotion_path: Path = promotion.DEFAULT_PATH,
    slate_date: str | None = None,
) -> PipelineResult:
    """Fetch the slate, price every team market from the model, publish SHADOW rows."""
    from outlier_nba.cli import _slate_date  # one definition of "the slate's evening date"

    bundle = ingest.fetch_raw(client, clock=clock, include_props=False)
    as_of = bundle.fetch_finished
    slate = ingest.normalize_slate(bundle, as_of)
    day = slate_date or _slate_date(slate, as_of)
    ctx = make_run_context(
        day, as_of, fixture=fixture, slate_events=slate.events, run_started_utc=as_of
    )
    if params is None:
        params, params_source = load_params()
    pipe = NbaPipeline(nba_dir=nba_dir, published_dir=published_dir or nba_dir / "published")

    def feeds(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> list[StageReceipt]:
        return [r for r in ingest.raw_receipts(bundle, slate) if r.name != "player_props"]

    def team_state(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        st = compute_team_state(games_loader(), ctx.slate_date, params, params_source)
        state["team_state"] = st
        table = ratings_rows(st)
        writer.stage_text("team_ratings.csv", rows_to_csv(table, list(table[0])))
        writer.stage_json("team_model_state.json", state_summary(st))
        return receipt("team_state", "OK", received=st.asof.rating.n_games)

    def normalize(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        reader = AsOfReader(ctx)
        stamped = [
            {"rec": r, "captured_at": bundle.fetch_started.isoformat(), "tip": r.tip.isoformat()}
            for r in slate.records
            if r.kind in ("GAME", "TEAM")
        ]
        state["records"] = [
            row["rec"] for row in reader.admit(stamped, stamp_key="captured_at", tip_key="tip")
        ]
        return receipt(
            "normalize", "OK" if state["records"] else "EMPTY", received=len(state["records"])
        )

    def price(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        records = state["records"]
        if not records:
            return receipt("price_team_markets", "EMPTY", reason="no team-market outcomes")
        cap = capture_mod.build_shadow_capture(records, captured_at=bundle.fetch_started)
        rows, stats = price_slate(
            state["team_state"], records, cap, slate_date=ctx.slate_date,
            captured_at=bundle.fetch_started, n_draws=n_draws, promotion_path=promotion_path,
        )  # fmt: skip
        writer.stage_text("team_shadow.csv", rows_to_csv(rows))
        ladders = fair_ladders(state["team_state"], slate.events, ctx.slate_date, n_draws, 7)
        writer.stage_text(
            "team_fair_ladders.csv",
            rows_to_csv(ladders, list(ladders[0]) if ladders else ["event_id"]),
        )
        writer.stage_json("team_run_summary.json", {**stats, "run_id": ctx.run_id,
                          "n_draws": n_draws, "note": "SHADOW: units 0, model weight from evaluator"})  # fmt: skip
        return receipt("price_team_markets", "OK" if rows else "EMPTY", received=len(rows))

    stages = [
        Stage("feeds", True, feeds),
        Stage("team_state", True, team_state),
        Stage("normalize", True, normalize),
        Stage("price_team_markets", True, price),
    ]
    result = pipe.run(ctx, stages)
    logger.info("nba_team_shadow", run_id=result.run_id, status=result.status,
                published=result.published, withheld=result.withheld_reason)  # fmt: skip
    return result


def load_store_games(store: Path) -> list[GameRow]:
    if not store.exists():
        raise TeamStateError(f"games store not found at {store}; run `backfill --stage build`")
    return load_games(store)
