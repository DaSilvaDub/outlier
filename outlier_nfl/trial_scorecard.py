"""Settle best-bets card picks and score the Upgrade B live trial.

Nothing else settles the traced best-bets card: ``outlier_nfl.settle`` and
``enrich_close`` read Tier-1 / matchup ``records``, not card ``picks``. This module
converts picks into the same ``PredictionSnap`` rows, so grading (box scores) and
CLV (verified book closes, PR #375) are exactly the shadow-settle logic.

Report, per arm:

* ``VALIDATED``: the trial itself: W-L, flat-stake units, ROI, drawdown, verified CLV.
* ``blocked_only_by:<gate>``: PROVISIONAL picks that one Upgrade B gate alone held
  back. Live "all gates but this one" evidence for each gate (approximate; the
  exact counterfactual comes from ``outlier_nfl.gate_backtest``).
* ``PROVISIONAL`` and ``REJECTED``, for context.

Plus the pre-registered stop-criteria verdict (``config/nfl_upgrade_b_trial.json``)
and ``base_p`` reliability buckets for checking the market-prior anchoring (kappa).

Usage (run on the machine that holds data/NFL; writes only where you point it)::

    python -m outlier_nfl.trial_scorecard \\
        --cards "data/NFL/normalized/nfl_best_bets_2026-11-2*.json" \\
        --provider nflverse --season 2026 --weeks 12 \\
        --close-feed data/NFL/close/odds_api_close_2026-11-29.json \\
        --out-json reports/NFL/upgrade_b_trial_wk12.json --out-md reports/NFL/upgrade_b_trial_wk12.md
"""

from __future__ import annotations

import argparse
import glob
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from outlier_nfl.best_bets import PROVISIONAL, REJECTED, VALIDATED, american_to_decimal
from outlier_nfl.boxscore import NflBoxScoreEvent
from outlier_nfl.enrich_close import attach_close_fields, load_book_close_feed
from outlier_nfl.settle import (
    PredictionSnap,
    _optional_float,
    _optional_int,
    _slate_date_from_prediction,
    _verified_book_close,
    load_boxscores,
    settle_predictions,
)
from outlier_nfl.tape_nflverse import _name_key
from outlier_nfl.upgrade_b_trial import StopCriteria, load_trial_config

EXIT_OUTPUT_EXISTS = 2


# ---------------------------------------------------------------------------
# Cards -> picks
# ---------------------------------------------------------------------------


def pick_identity(pick: Mapping[str, Any]) -> tuple[str, str, str, str, float | None]:
    """One bet: event, player, market, side, line."""
    return (
        str(pick.get("event_id") or ""),
        _name_key(pick.get("player_name")),
        str(pick.get("market") or "").upper(),
        str(pick.get("position") or "").upper(),
        _optional_float(pick.get("line")),
    )


def load_cards(patterns: Iterable[str]) -> list[tuple[Path, dict[str, Any]]]:
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern)) or ([pattern] if Path(pattern).is_file() else [])
        paths.extend(Path(m) for m in matched)
    cards = []
    for path in dict.fromkeys(paths):  # de-duplicate, keep order
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("picks"), list):
            cards.append((path, payload))
    return cards


def collect_picks(cards: Sequence[tuple[Path, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """The last pre-kickoff verdict for each bet across all cards.

    A pick only counts from a card written before its game started (a card refreshed
    after kickoff is not a bet anyone could place). When several pre-kickoff cards
    carry the same bet, the latest card's verdict is the one that was acted on.
    """
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    written_at: dict[tuple[Any, ...], datetime] = {}
    for path, card in cards:
        written = _utc(card.get("updated_at"))
        stake_policy = (card.get("settings") or {}).get("stake_policy") or {"mode": "kelly"}
        for pick in card.get("picks") or []:
            starts = _utc(pick.get("event_starts_at"))
            # Timestamps mix UTC ("...Z") and Eastern offsets, so compare instants, not strings.
            if written is not None and starts is not None and written >= starts:
                continue
            row = dict(pick)
            row["card_path"] = str(path)
            row["card_updated_at"] = card.get("updated_at")
            row["stake_mode"] = stake_policy.get("mode") or "kelly"
            key = pick_identity(pick)
            stamp = written or _EPOCH
            if key not in best or stamp >= written_at[key]:
                best[key] = row
                written_at[key] = stamp
    return sorted(best.values(), key=lambda p: (_utc(p.get("event_starts_at")) or _EPOCH, pick_identity(p)))


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _utc(value: Any) -> datetime | None:
    """Parse an ISO timestamp to an aware UTC datetime (naive values are taken as UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))  # 3.11+ accepts a trailing "Z"
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def pick_to_record(pick: Mapping[str, Any]) -> dict[str, Any]:
    """A settle/enrich-compatible record for one card pick."""
    price = ((pick.get("pillars") or {}).get("price") or {}).get("evidence") or {}
    implied = price.get("implied")
    if implied is None and pick.get("best_odds") is not None:
        implied = 1.0 / american_to_decimal(int(pick["best_odds"]))
    return {
        "event_id": pick.get("event_id"),
        "event_starts_at": pick.get("event_starts_at"),
        "matchup": pick.get("matchup") or "",
        "team": pick.get("team"),
        "opponent": pick.get("opponent"),
        "player_name": pick.get("player_name"),
        "player_id": pick.get("player_id"),
        "market": pick.get("market"),
        "position": pick.get("position"),
        "line": pick.get("line"),
        "implied_probability": implied,
        "best_odds": pick.get("best_odds"),
        "confidence_tier": pick.get("confidence_tier"),
        "model_p": pick.get("final_p"),
        "scope": "full_game",
    }


def _snap(record: Mapping[str, Any]) -> PredictionSnap:
    return PredictionSnap(
        source="best_bets",
        event_id=str(record.get("event_id") or ""),
        event_starts_at=str(record["event_starts_at"]) if record.get("event_starts_at") else None,
        slate_date=_slate_date_from_prediction(record, None),
        matchup=str(record.get("matchup") or ""),
        team=str(record["team"]) if record.get("team") else None,
        opponent=str(record["opponent"]) if record.get("opponent") else None,
        player_name=str(record.get("player_name") or ""),
        player_id=str(record["player_id"]) if record.get("player_id") else None,
        market=str(record.get("market") or ""),
        position=str(record.get("position") or "").upper(),
        line=float(record["line"]),
        implied_probability=_optional_float(record.get("implied_probability")),
        confidence_tier=str(record["confidence_tier"]) if record.get("confidence_tier") else None,
        calibration_tags=(),
        best_odds=_optional_int(record.get("best_odds")),
        close_line=_optional_float(record.get("close_line")),
        close_odds=_optional_int(record.get("close_odds")),
        close_implied=_optional_float(record.get("close_implied")),
        close_source=str(record["close_source"]) if record.get("close_source") else None,
        close_status=str(record["close_status"]) if record.get("close_status") else None,
        model_p=_optional_float(record.get("model_p")),
        scope="full_game",
    )


def settle_picks(
    picks: Sequence[Mapping[str, Any]],
    events: Sequence[NflBoxScoreEvent],
    *,
    close_index: Mapping[tuple[Any, ...], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Grade each pick (W/L/P) and attach CLV; returns one outcome per pick, same order."""
    snaps: list[PredictionSnap | None] = []
    for pick in picks:
        if _optional_float(pick.get("line")) is None:
            snaps.append(None)
            continue
        record = pick_to_record(pick)
        if close_index is not None:
            attach_close_fields(record, mode="book_close", book_close_index=close_index)
        snaps.append(_snap(record))
    valid = [s for s in snaps if s is not None]
    rows = iter(settle_predictions(valid, events).rows) if valid else iter(())
    outcomes: list[dict[str, Any]] = []
    for snap in snaps:
        if snap is None:
            outcomes.append({"status": "skipped", "skip_reason": "no_line", "result": None})
            continue
        row = next(rows)  # settle_predictions keeps prediction order, one row each
        outcomes.append({
            "status": row.get("status"),
            "skip_reason": row.get("skip_reason"),
            "result": row.get("result"),
            "actual": row.get("actual"),
            "clv_implied_pts": row.get("clv_implied_pts"),
            "clv_verified": row.get("clv_implied_pts") is not None and _verified_book_close(snap),
            "close_line_move": row.get("close_line_move"),
        })
    return outcomes


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def flat_profit_units(result: str | None, american: Any) -> float | None:
    """Profit of a 1-unit stake at the pick's price; None when ungraded."""
    if result == "P":
        return 0.0
    if result not in ("W", "L") or american is None:
        return None
    return american_to_decimal(int(american)) - 1.0 if result == "W" else -1.0


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(centre - half, 4), round(centre + half, 4)


def arm_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """W-L, flat units, ROI, drawdown and verified CLV for one group of graded picks."""
    graded = [r for r in rows if r.get("result") in ("W", "L", "P")]
    wins = sum(1 for r in graded if r["result"] == "W")
    losses = sum(1 for r in graded if r["result"] == "L")
    pushes = sum(1 for r in graded if r["result"] == "P")
    decided = wins + losses
    profits = [flat_profit_units(r["result"], r.get("best_odds")) for r in graded]
    units = [u for u in profits if u is not None]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for u in units:  # rows are in kickoff order
        equity += u
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    kelly_bankroll = sum(
        float(r.get("kelly_stake_fraction") or 0.0) * u for r, u in zip(graded, profits) if u is not None
    )
    clv = [float(r["clv_implied_pts"]) for r in rows if r.get("clv_verified")]
    lo, hi = wilson_interval(wins, decided)
    lo_one_sided, _ = wilson_interval(wins, decided, z=1.645)
    return {
        "picks": len(rows),
        "graded": len(graded),
        "ungraded": len(rows) - len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / decided, 4) if decided else None,
        "win_rate_ci95": [lo, hi],
        "win_rate_lower_95_one_sided": lo_one_sided,
        "flat_units": round(sum(units), 3),
        "flat_roi": round(sum(units) / len(units), 4) if units else None,
        "max_drawdown_units": round(max_dd, 3),
        "kelly_bankroll_change": round(kelly_bankroll, 5),
        "clv_n": len(clv),
        "clv_mean_pts": round(sum(clv) / len(clv), 3) if clv else None,
        "clv_beat_close_rate": round(sum(1 for c in clv if c > 0) / len(clv), 4) if clv else None,
    }


def evaluate_stop_criteria(m: Mapping[str, Any], c: StopCriteria) -> dict[str, Any]:
    """Apply the pre-registered rules to the VALIDATED arm.

    Each rule is ``waiting`` (too few picks to judge), ``ok`` or ``TRIGGERED``.
    Overall: ``REVIEW_GATES`` if any rule triggered; ``EARNED_TRUST`` once the
    success rule holds and CLV is not negative; otherwise ``CONTINUE``.
    """
    decided = int(m.get("wins") or 0) + int(m.get("losses") or 0)
    rules: dict[str, dict[str, Any]] = {}

    if decided < c.win_rate_min_picks:
        rules["win_rate"] = {"status": "waiting", "detail": f"{decided}/{c.win_rate_min_picks} decided picks"}
    else:
        bad = (m.get("win_rate") or 0.0) < c.win_rate_floor
        rules["win_rate"] = {"status": "TRIGGERED" if bad else "ok",
                             "detail": f"win rate {m.get('win_rate')} vs floor {c.win_rate_floor}"}

    clv_n = int(m.get("clv_n") or 0)
    if clv_n < c.clv_min_picks:
        rules["clv"] = {"status": "waiting", "detail": f"{clv_n}/{c.clv_min_picks} picks with verified close"}
    else:
        bad = (m.get("clv_mean_pts") or 0.0) < c.clv_mean_floor_pts
        rules["clv"] = {"status": "TRIGGERED" if bad else "ok",
                        "detail": f"mean CLV {m.get('clv_mean_pts')} pts vs floor {c.clv_mean_floor_pts}"}

    dd = float(m.get("max_drawdown_units") or 0.0)
    rules["drawdown"] = {"status": "TRIGGERED" if dd >= c.max_drawdown_units else "ok",
                         "detail": f"max drawdown {dd}u vs stop {c.max_drawdown_units}u"}

    # One-sided: the question is only "is it above breakeven?". An observed 58% clears
    # 52.4% this way at about 210-250 decided picks.
    lower = m.get("win_rate_lower_95_one_sided")
    if decided < c.success_min_picks:
        rules["success"] = {"status": "waiting", "detail": f"{decided}/{c.success_min_picks} decided picks"}
    else:
        earned = lower is not None and lower > c.breakeven_win_rate
        rules["success"] = {"status": "ok" if earned else "not_met",
                            "detail": f"win-rate one-sided 95% lower bound {lower} vs breakeven "
                                      f"{c.breakeven_win_rate}"}

    if any(r["status"] == "TRIGGERED" for r in rules.values()):
        verdict = "REVIEW_GATES"
    elif rules["success"]["status"] == "ok" and rules["clv"]["status"] == "ok":
        verdict = "EARNED_TRUST"
    else:
        verdict = "CONTINUE"
    return {"verdict": verdict, "rules": rules}


def _bucket(value: float, width: float = 0.05) -> str:
    lo = math.floor(value / width) * width
    return f"{lo:.2f}-{lo + width:.2f}"


def reliability(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Predicted vs actual hit rate by bucket, over every graded pick (all verdicts).

    ``by_base_p``: is base_p calibrated? ``by_empirical``: grouped by the player's
    blended hit rate. If high-hit-rate buckets beat their mean base_p, the market
    prior (kappa) is underweighting real signal; if they fall short, it is not
    shrinking enough. Tune kappa only on held-out weeks (``gate_backtest``).
    """
    def table(key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in rows:
            v = _optional_float(r.get(key))
            if v is None or r.get("result") not in ("W", "L"):
                continue
            groups[_bucket(v)].append(r)
        out = []
        for name in sorted(groups):
            g = groups[name]
            n = len(g)
            hit = sum(1 for r in g if r["result"] == "W") / n
            mean_base = sum(float(r["base_p"]) for r in g) / n
            se = math.sqrt(max(mean_base * (1 - mean_base), 1e-9) / n)
            out.append({
                "bucket": name, "n": n,
                "mean_key": round(sum(float(r[key]) for r in g) / n, 4),
                "mean_base_p": round(mean_base, 4),
                "actual_hit_rate": round(hit, 4),
                "gap_pts": round((hit - mean_base) * 100, 2),
                "z": round((hit - mean_base) / se, 2),
            })
        return out

    return {"by_base_p": table("base_p"), "by_empirical": table("empirical_blend_p")}


def _arm_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    arms: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        arms[str(r.get("verdict"))].append(r)
        gate = (r.get("upgrade_b") or {}).get("blocked_only_by")
        if gate:
            arms[f"blocked_only_by:{gate}"].append(r)
    return arms


def build_scorecard(
    cards: Sequence[tuple[Path, Mapping[str, Any]]],
    events: Sequence[NflBoxScoreEvent],
    *,
    criteria: StopCriteria | None = None,
    close_index: Mapping[tuple[Any, ...], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    criteria = criteria or StopCriteria()
    picks = collect_picks(cards)
    outcomes = settle_picks(picks, events, close_index=close_index)
    rows: list[dict[str, Any]] = []
    for pick, outcome in zip(picks, outcomes):
        hist = ((pick.get("pillars") or {}).get("historical") or {}).get("evidence") or {}
        rows.append({
            "event_starts_at": pick.get("event_starts_at"),
            "player_name": pick.get("player_name"),
            "market": pick.get("market"),
            "position": pick.get("position"),
            "line": pick.get("line"),
            "best_odds": pick.get("best_odds"),
            "best_book": pick.get("best_book"),
            "verdict": pick.get("verdict"),
            "stake_mode": pick.get("stake_mode"),
            "stake_fraction": pick.get("stake_fraction"),
            "kelly_stake_fraction": pick.get("kelly_stake_fraction"),
            "base_p": pick.get("base_p"),
            "final_p": pick.get("final_p"),
            "empirical_blend_p": hist.get("empirical_blend_p"),
            "upgrade_b": pick.get("upgrade_b"),
            "card_path": pick.get("card_path"),
            **outcome,
        })
    arms = _arm_rows(rows)
    order = [VALIDATED, PROVISIONAL, REJECTED] + sorted(a for a in arms if a.startswith("blocked_only_by:"))
    arm_table = {name: arm_metrics(arms.get(name, [])) for name in order}
    skip_reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("status") == "skipped":
            skip_reasons[str(r.get("skip_reason"))] += 1
    return {
        "cards": [str(p) for p, _ in cards],
        "picks": len(rows),
        "close_feed_attached": close_index is not None,
        "arms": arm_table,
        "stop_criteria": {
            "criteria": {k: getattr(criteria, k) for k in StopCriteria.__dataclass_fields__},
            **evaluate_stop_criteria(arm_table[VALIDATED], criteria),
        },
        "reliability": reliability(rows),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "validated_rows": [r for r in rows if r.get("verdict") == VALIDATED],
    }


def _fmt(value: Any, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def render_markdown(sc: Mapping[str, Any], *, title: str = "Upgrade B live trial scorecard") -> str:
    stop = sc["stop_criteria"]
    header = (f"Cards: {len(sc['cards'])} | unique pre-kickoff picks: {sc['picks']} | "
              f"close feed attached: {sc['close_feed_attached']}")
    lines = [f"# {title}", "", header, "",
             f"## Stop criteria: **{stop['verdict']}**", "", "| Rule | Status | Detail |", "|---|---|---|"]
    for name, rule in stop["rules"].items():
        lines.append(f"| {name} | {rule['status']} | {rule['detail']} |")
    lines += ["", "## Arms (flat 1u per pick)", "",
              "| Arm | Picks | Graded | W-L-P | Win % | 95% CI | Units | ROI | Max DD | CLV n | CLV mean | Beat close |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, m in sc["arms"].items():
        ci = m["win_rate_ci95"]
        ci_s = "-" if ci[0] is None else f"{ci[0]:.3f}-{ci[1]:.3f}"
        lines.append(
            f"| {name} | {m['picks']} | {m['graded']} | {m['wins']}-{m['losses']}-{m['pushes']} | "
            f"{_fmt(m['win_rate'], '.1%')} | {ci_s} | {m['flat_units']:+.2f} | "
            f"{_fmt(m['flat_roi'], '+.1%')} | {m['max_drawdown_units']:.2f} | "
            f"{m['clv_n']} | {_fmt(m['clv_mean_pts'], '+.2f')} | {_fmt(m['clv_beat_close_rate'], '.0%')} |"
        )
    arm_note = ("`blocked_only_by:<gate>` = PROVISIONAL picks one Upgrade B gate alone held back. "
                "If such an arm wins and beats the close like VALIDATED does, that gate is costing picks.")
    lines += ["", arm_note, ""]
    for key, label in (("by_base_p", "base_p"), ("by_empirical", "blended empirical hit rate")):
        lines += [f"## Reliability by {label} (all graded picks)", "",
                  "| Bucket | n | Mean | Mean base_p | Actual | Gap (pts) | z |", "|---|---|---|---|---|---|---|"]
        for b in sc["reliability"][key]:
            lines.append(f"| {b['bucket']} | {b['n']} | {b['mean_key']:.3f} | {b['mean_base_p']:.3f} | "
                         f"{b['actual_hit_rate']:.3f} | {b['gap_pts']:+.1f} | {b['z']:+.2f} |")
        lines.append("")
    if sc["skip_reasons"]:
        lines += ["## Ungraded picks", ""] + [f"- {k}: {v}" for k, v in sc["skip_reasons"].items()] + [""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_events(
    *, boxscores: Sequence[Path] = (), season: int | None = None, weeks: Sequence[int] = (),
    nflverse_cache: Path | None = None,
) -> list[NflBoxScoreEvent]:
    events: list[NflBoxScoreEvent] = []
    for path in boxscores:
        events.extend(load_boxscores(path))
    if weeks:
        if season is None:
            raise SystemExit("--weeks needs --season")
        from outlier_nfl.boxscore_nflverse import load_nflverse_events

        for week in weeks:
            events.extend(load_nflverse_events(season=season, week=week, cache_dir=nflverse_cache))
    if not events:
        raise SystemExit("no box scores: pass --boxscores and/or --provider nflverse --season --weeks")
    return events


def add_event_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--boxscores", type=Path, action="append", default=[],
                        help="Simplified box-score JSON (repeatable).")
    parser.add_argument("--provider", choices=("nflverse",), help="Grade from nflverse weekly stats.")
    parser.add_argument("--season", type=int)
    parser.add_argument("--weeks", type=int, nargs="+", default=[])
    parser.add_argument("--nflverse-cache", type=Path)


def events_from_args(args: argparse.Namespace) -> list[NflBoxScoreEvent]:
    weeks = args.weeks if args.provider == "nflverse" else []
    if args.weeks and args.provider != "nflverse":
        raise SystemExit("--weeks needs --provider nflverse")
    return load_events(boxscores=args.boxscores, season=args.season, weeks=weeks,
                       nflverse_cache=args.nflverse_cache)


def write_outputs(pairs: Sequence[tuple[Path | None, str]], *, overwrite: bool) -> int:
    for path, _ in pairs:
        if path is not None and path.exists() and not overwrite:
            print(f"refusing to overwrite {path} (pass --overwrite)")
            return EXIT_OUTPUT_EXISTS
    for path, text in pairs:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the Upgrade B live trial from best-bets cards.")
    parser.add_argument("--cards", nargs="+", required=True, help="Card JSON paths or globs.")
    add_event_args(parser)
    parser.add_argument("--close-feed", type=Path, help="Book-close feed (fetch_odds_close output) for CLV.")
    parser.add_argument("--trial-config", type=Path, help="Defaults to config/nfl_upgrade_b_trial.json.")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    cards = load_cards(args.cards)
    if not cards:
        raise SystemExit("no best-bets cards matched --cards")
    trial = load_trial_config(args.trial_config)
    close_index = load_book_close_feed(args.close_feed) if args.close_feed else None
    sc = build_scorecard(cards, events_from_args(args), criteria=trial.stop_criteria, close_index=close_index)
    md = render_markdown(sc)
    code = write_outputs(
        [(args.out_json, json.dumps(sc, indent=2, default=str)), (args.out_md, md)],
        overwrite=args.overwrite,
    )
    if code == 0 and args.out_md is None:
        print(md)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
