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
from datetime import UTC, date, datetime, timedelta
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
from outlier_nba.receipts import StageReceipt, failing, receipt
from outlier_nba.run_context import (
    AsOfReader,
    RunContext,
    make_run_context,
    nba_season_for_date,
    try_parse_utc,
)
from outlier_nba.run_writer import RunWriter
from outlier_nba.sim.engine import SimDraws, SimInputError, SimParams, simulate
from outlier_nba.track import capture as capture_mod
from outlier_nba.track import promotion
from outlier_nba.track.model_ledger import record_model_probs
from outlier_nba.track.backtest import Predictions, walk_forward
from outlier_scrapers import paths

logger = structlog.get_logger("outlier_nba.team_run")

MODEL_CONFIG_PATH = paths.PROJECT_ROOT / "config" / "nba_team_model.json"
MAX_RESULT_AGE_DAYS = 7  # newest finished game in the store vs the slate date
OPENING_WINDOW_DAYS = 45  # days after Oct 1 in which a prior-season-only model is acceptable
DEFAULT_DRAWS = 20_000
# Only games starting within this many hours are priced. The schedule runs for months, but the
# ratings are as of now and far-out quotes are thin; a forecast made weeks ahead would also
# become the "first pre-tip forecast" the promotion evidence is built on.
DEFAULT_HORIZON_HOURS = 36.0
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
    "units", "actionable", "event_starts_at", "as_of", "venue",
]  # fmt: skip


def load_feed_receipt(cache_root: Path | None = None) -> dict[str, Any] | None:
    """What the last successful ESPN refresh covered, or None if there never was one."""
    from outlier_nba.ingest.espn import EspnCache, default_cache_dir

    return EspnCache(cache_root or default_cache_dir()).read_receipt()


def opener_verified(receipt: Mapping[str, Any] | None, slate_date: str, season: str) -> bool:
    """True only if a recent refresh covered the previous day and saw no game of ``season``.

    The local store alone cannot tell "the season has not started" from "the feed stopped
    updating"; this evidence comes from the refresh itself.
    """
    if not receipt:
        return False
    fetched = try_parse_utc(receipt.get("fetched_at"))
    if fetched is None:
        return False
    slate = date.fromisoformat(slate_date)
    if not 0 <= (slate - fetched.astimezone(UTC).date()).days <= 1:
        return False
    if (slate - timedelta(days=1)).strftime("%Y%m%d") not in (receipt.get("covered_days") or []):
        return False
    return int((receipt.get("final_games_by_season") or {}).get(season, 0)) == 0


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
    prior_only: bool = False  # season opener: no result from this season yet

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
    feed_receipt: Mapping[str, Any] | None = None,
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
    in_season = any(s == season for s, u in zip(ds.season, usable) if u)
    prior_only = False
    if slate_day - last > max_age_days:
        # The first slate after the offseason has no result from its own season: the model
        # then runs on last season's final ratings. That is allowed only inside the opening
        # weeks of the new season; once the season has started, stale results are an error.
        opening = date(int(season[:4]), 10, 1).toordinal()
        if in_season or not (0 <= slate_day - opening <= OPENING_WINDOW_DAYS):
            raise TeamStateError(
                f"newest result is {slate_day - last} days before {slate_date} "
                f"(limit {max_age_days}); refresh the store"
            )
        if not opener_verified(feed_receipt, slate_date, season):
            raise TeamStateError(
                f"no verified refresh shows {season} has not started "
                f"(newest result {slate_day - last} days old); run `ratings` first"
            )
        prior_only = True
    past = build_dataset([g for g, u in zip(ds.games, usable) if u])
    preds = walk_forward(past, params)
    if not preds.noise.ready:
        from outlier_nba.ingest.espn import BOOTSTRAP_HINT

        raise TeamStateError(
            f"only {preds.noise.n_obs} out-of-sample games to estimate noise: the store holds "
            f"{len(games)} games and the model needs about two seasons of history; {BOOTSTRAP_HINT}"
        )
    model = TeamModel(past, params, extra_season=season)
    asof = model.fit_asof(season, slate_day)
    return TeamState(
        past, model, asof, preds, params, slate_day, season, last, params_source, prior_only
    )


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
        "prior_only_season_opener": state.prior_only,
        "league": {"mu": r.mu, "home_per100": r.h, "playoff_shift": r.playoff},
        "noise": {
            "n": t.n,
            "sigma_e": t.sigma_e,
            "rho": t.rho,
            "sigma_p": t.sigma_p,
            "sigma_margin": t.sigma_margin,
            "sigma_total": t.sigma_total,
            "ot_ratio": t.ot_ratio,
            "level_bias_per100": state.params.bias_weight * t.level_bias,
            "pace_efficiency_beta": t.beta_pe,
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


NEUTRAL_KEYS = ("neutralSite", "neutral_site", "neutral", "isNeutral")
_TRUE = frozenset({"true", "1", "yes", "y"})
_FALSE = frozenset({"false", "0", "no", "n"})


def parse_flag(value: Any) -> bool | None:
    """Strict boolean: real bools, 0/1 and the usual strings; anything else is unknown."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def _venue(neutral: bool | None) -> str:
    return "unknown" if neutral is None else "neutral" if neutral else "home"


def event_neutral(event: Mapping[str, Any]) -> bool | None:
    """Venue flag from the schedule payload; None when absent or not a recognised value."""
    for key in NEUTRAL_KEYS:
        if key in event and event[key] is not None:
            flag = parse_flag(event[key])
            if flag is None:
                logger.warning("neutral_flag_unparsed", key=key, value=repr(event[key])[:40])
            return flag
    return None


TEAM_NAME_KEYS = ("alias", "abbr", "abbreviation", "teamName", "name")


def side_team(side: Mapping[str, Any] | None) -> str | None:
    """Canonical tricode from a schedule side, trying every field the normalizer accepts."""
    side = side or {}
    for key in TEAM_NAME_KEYS:
        team = resolve_team(side.get(key))
        if team is not None:
            return team
    market, name = side.get("market"), side.get("name")
    return resolve_team(f"{market} {name}") if market and name else None


def event_teams(
    events: Sequence[Mapping[str, Any]], records: Sequence[ingest.OutcomeRecord] = ()
) -> dict[str, tuple[str, str]]:
    """``{event_id: (home, away)}``; the normalizer's own resolution (records) wins."""
    out: dict[str, tuple[str, str]] = {}
    for ev in events:
        home, away = side_team(ev.get("home")), side_team(ev.get("away"))
        if home and away:
            out[str(ev.get("eventId") or ev.get("id") or "")] = (home, away)
    for rec in records:
        if rec.home and rec.away:
            out[rec.event_id] = (rec.home, rec.away)
    return out


def event_draws(
    state: TeamState,
    home: str,
    away: str,
    slate_date: str,
    n: int,
    rng: np.random.Generator,
    *,
    neutral: bool = False,
) -> SimDraws:
    pr = predict(
        state.asof,
        TEAM_INDEX[home],
        TEAM_INDEX[away],
        neutral=neutral,
        playoff=_is_playoff_window(slate_date),
        sigma_p=state.tracker.sigma_p,
    )
    t = state.tracker
    bias = state.params.bias_weight * t.level_bias  # same correction the backtest applied
    return simulate(
        SimParams.single(
            log_poss=pr.log_poss, sigma_p=t.sigma_p, e_home=pr.e_home + bias,
            e_away=pr.e_away + bias, sigma_e=t.sigma_e, rho=t.rho, ot_ratio=t.ot_ratio,
            beta_pe=t.beta_pe,
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
    captured_at: datetime,
    draws_by_event: Mapping[str, SimDraws | None],
    neutral_by_event: Mapping[str, bool | None],
    promotion_path: Path = promotion.DEFAULT_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One shadow row per quoted team-market outcome (main lines and alt rungs alike).

    ``draws_by_event`` holds the single simulation per game that every quoted outcome and
    the fair ladders are read from, so one outcome has one probability everywhere.
    """
    cons = {
        str(r["outcome_id"]): float(r["market_consensus_prob"])
        for r in capture.rows
        if r["market_consensus_prob"] != ""
    }
    price_by_outcome = {str(r["outcome_id"]): r for r in capture.rows}
    main = _main_lines(records, cons)
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
        draws = draws_by_event.get(rec.event_id)
        alt = rec.market != "ML" and rec.outcome_id not in main and rec.outcome_id in cons
        family = (ALT_FAMILY if alt else FAMILY)[rec.market] if rec.market != "ML" else FAMILY["ML"]
        flags = ["SHADOW"] + (["ALT_LINE"] if alt else [])
        if neutral_by_event.get(rec.event_id) is None:
            flags.append("HOME_COURT_ASSUMED")  # the schedule feed does not state the venue
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
            "venue": _venue(neutral_by_event.get(rec.event_id)),
        }  # fmt: skip
        stats["rows"] += 1
        stats["model_priced"] += priced is not None
        stats["final_missing"] += final.p_final is None
        rows.append(row)
    return rows, dict(stats)


def simulate_events(
    state: TeamState,
    events: Sequence[Mapping[str, Any]],
    slate_date: str,
    n: int,
    seed: int,
    records: Sequence[ingest.OutcomeRecord] = (),
) -> tuple[dict[str, SimDraws | None], dict[str, bool | None]]:
    """One simulation per open game (None when it cannot be run) and its venue flag."""
    rng = np.random.default_rng(seed)
    draws: dict[str, SimDraws | None] = {}
    neutral: dict[str, bool | None] = {}
    teams = event_teams(events, records)
    event_ids = [str(ev.get("eventId") or ev.get("id") or "") for ev in events]
    event_ids += [e for e in teams if e not in event_ids]
    by_id = {str(ev.get("eventId") or ev.get("id") or ""): ev for ev in events}
    for event_id in event_ids:
        neutral[event_id] = event_neutral(by_id.get(event_id, {}))
        if event_id not in teams:
            draws[event_id] = None
            continue
        home, away = teams[event_id]
        try:
            draws[event_id] = event_draws(
                state, home, away, slate_date, n, rng, neutral=bool(neutral[event_id])
            )
        except (SimInputError, KeyError, ValueError) as exc:
            logger.warning("event_sim_failed", event=event_id, error=str(exc))
            draws[event_id] = None
    return draws, neutral


def fair_ladders(
    teams: Mapping[str, tuple[str, str]], draws_by_event: Mapping[str, SimDraws | None]
) -> list[dict[str, Any]]:
    """Model fair odds on a one-point grid around each event's expected values (alt lines).

    Reads the same draws the quoted outcomes were priced from.
    """
    out: list[dict[str, Any]] = []
    for event_id, (home, away) in teams.items():
        draws = draws_by_event.get(event_id)
        if draws is None:
            continue
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
                            "event_id": event_id, "home": home, "away": away,
                            "ladder": name, "line": line, "side": side,
                            "p_win": round(pr.p_win, 5), "p_push": round(pr.p_push, 5),
                            "fair_decimal": round(pr.fair_decimal, 4) if math.isfinite(pr.fair_decimal) else "",
                        }
                    )  # fmt: skip
    return out


def rows_to_csv(rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=list(fields if fields is not None else ROW_FIELDS), lineterminator="\n"
    )
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
    feed_receipt_loader: Callable[[], Mapping[str, Any] | None] = load_feed_receipt,
    ledger_db: Path | None = None,
    horizon_hours: float | None = DEFAULT_HORIZON_HOURS,
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
        st = compute_team_state(
            games_loader(),
            ctx.slate_date,
            params,
            params_source,
            feed_receipt=feed_receipt_loader(),
        )
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
        admitted = [
            row["rec"] for row in reader.admit(stamped, stamp_key="captured_at", tip_key="tip")
        ]
        cutoff = None if horizon_hours is None else ctx.as_of_utc + timedelta(hours=horizon_hours)
        state["records"] = [r for r in admitted if cutoff is None or r.tip <= cutoff]
        near = {r.event_id for r in state["records"]}
        state["events"] = [
            e
            for e in slate.events
            if str(e.get("eventId") or e.get("id") or "") in near or cutoff is None
        ]
        state["beyond_horizon"] = len(admitted) - len(state["records"])
        return receipt(
            "normalize",
            "OK" if state["records"] else "EMPTY",
            received=len(state["records"]),
            reason=(
                f"{state['beyond_horizon']} outcomes start beyond the {horizon_hours}h horizon"
                if state["beyond_horizon"]
                else None
            ),
        )

    def price(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        records = state["records"]
        state["shadow_rows"] = []  # the ledger stage reads this even when nothing was priced
        if not records:
            return receipt("price_team_markets", "EMPTY", reason="no team-market outcomes")
        cap = capture_mod.build_shadow_capture(records, captured_at=bundle.fetch_started)
        draws_by_event, neutral_by_event = simulate_events(
            state["team_state"], state["events"], ctx.slate_date, n_draws, 20260101, records
        )
        rows, stats = price_slate(
            state["team_state"],
            records,
            cap,
            captured_at=bundle.fetch_started,
            draws_by_event=draws_by_event,
            neutral_by_event=neutral_by_event,
            promotion_path=promotion_path,
        )
        state["shadow_rows"] = rows
        writer.stage_text("team_shadow.csv", rows_to_csv(rows))
        ladders = fair_ladders(event_teams(state["events"], records), draws_by_event)
        writer.stage_text(
            "team_fair_ladders.csv",
            rows_to_csv(ladders, list(ladders[0]) if ladders else ["event_id"]),
        )
        writer.stage_json("team_run_summary.json", {**stats, "run_id": ctx.run_id,
                          "n_draws": n_draws,
                          "horizon_hours": horizon_hours, "beyond_horizon": state["beyond_horizon"], "note": "SHADOW: units 0, model weight from evaluator"})  # fmt: skip
        quoted = {r.event_id for r in records}
        unmodelled = sorted(e for e in quoted if draws_by_event.get(e) is None)
        if not stats.get("model_priced"):
            return receipt(
                "price_team_markets",
                "FAILED",
                reason="no quoted outcome received a model probability",
                expected=len(rows),
                received=0,
                errors=[f"no simulation for event {e}" for e in unmodelled],
            )
        if unmodelled:
            return receipt(
                "price_team_markets",
                "INCOMPLETE",
                reason=f"{len(unmodelled)} of {len(quoted)} events have no model probabilities",
                expected=len(rows),
                received=stats["model_priced"],
                errors=[f"no simulation for event {e}" for e in unmodelled],
            )
        return receipt("price_team_markets", "OK", received=len(rows))

    def model_ledger(ctx: RunContext, writer: RunWriter, state: dict[str, Any]) -> StageReceipt:
        # Side effect, so it runs last and only when everything before it passed.
        prior = failing(state["receipts"])
        if prior:
            return receipt("model_ledger", "EMPTY", reason=f"skipped: {len(prior)} stage(s) not OK")
        if not ctx.publishes:
            return receipt(
                "model_ledger", "EMPTY", reason=f"skipped: {ctx.mode} run is bundle-only"
            )
        if not state.get("shadow_rows"):
            return receipt("model_ledger", "EMPTY", reason="no priced rows to record")
        stats = record_model_probs(ledger_db, state["shadow_rows"], ctx.run_id)  # type: ignore[arg-type]
        return receipt("model_ledger", "OK", received=stats["model_rows"])

    stages = [
        Stage("feeds", True, feeds),
        Stage("team_state", True, team_state),
        Stage("normalize", True, normalize),
        Stage("price_team_markets", True, price),
    ]
    if ledger_db is not None:
        stages.append(Stage("model_ledger", True, model_ledger))
    result = pipe.run(ctx, stages)
    logger.info("nba_team_shadow", run_id=result.run_id, status=result.status,
                published=result.published, withheld=result.withheld_reason)  # fmt: skip
    return result


def load_store_games(store: Path) -> list[GameRow]:
    if not store.exists():
        raise TeamStateError(f"games store not found at {store}; run `backfill --stage build`")
    return load_games(store)
