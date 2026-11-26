"""Markdown renderer for :class:`outlier_nba.slate_report.SlateResult` (sections A-I).

Commentary is derived from the result and nothing here is specific to one slate. The
caveats in section A are unconditional because they describe the data source and the model,
not the day; the one cited backtest figure there is a fixed reference, dated in the text.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from outlier_nba.slate_report import Candidate, GameProjection, SlateResult

ET = ZoneInfo("America/New_York")


def _pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:+.{nd}f}%"


def _pp(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


def _sel(c: Candidate) -> str:
    if c.market == "ML":
        return f"{c.team} ML"
    if c.market == "SPREAD":
        line = c.line if c.side == "HOME" else -(c.line or 0.0)
        return f"{c.team} {(line or 0.0) + 0.0:+g}"  # -0.0 + 0.0 is 0.0: no "-0" pick'em
    return f"{c.side.title()} {c.line:g}"


def _tip(p: GameProjection) -> str:
    return p.tip.astimezone(ET).strftime("%H:%M ET")


def _state(p: GameProjection) -> str:
    if p.status.endswith(("IN_PROGRESS", "HALFTIME", "END_PERIOD")):
        return "in progress"
    for suffix in ("FINAL", "POSTPONED", "CANCELED", "SUSPENDED", "DELAYED", "FORFEIT"):
        if p.status.endswith(suffix):
            return suffix.lower()
    return "tipped" if p.started else "pre-tip"


def _section_a(r: SlateResult, bets: list[Candidate]) -> list[str]:
    cfg, st = r.config, r.state_summary
    nz = st["noise"]
    prog = sum(_state(p) == "in progress" for p in r.projections)
    pre = [p for p in r.projections if not p.started and p.lines]
    tipped = sum(p.started for p in r.projections) - prog
    run = r.run_utc.astimezone(ET)
    return [
        f"# NBA Master Betting Pipeline V3.0 — {r.day}",
        f"*Run {run:%Y-%m-%d %H:%M ET} · {cfg.n_draws:,} simulations per game · seed {cfg.seed} "
        "· SHADOW/UNPROMOTED model*\n",
        "## A. Execution Summary\n",
        f"- **Slate:** {len(r.projections)} games on {r.day} (ET). At run time {prog} were in "
        f"progress, {tipped} more had tipped or were within {cfg.pretip_buffer_min:g} minutes of "
        f"tip, **{len(pre)} were still pre-tip** and bettable.",
        f"- **Model:** repo team model (`outlier_nba`), as-of fit on {st['games_in_store']:,} "
        f"stored games, {st['season_games_fitted']} games of {st['season']} through "
        f"{st['last_result_day']}. Params `{st['params_source']}`; fitted noise σ_margin "
        f"{nz['sigma_margin']:.1f}, σ_total {nz['sigma_total']:.1f}, σ_e {nz['sigma_e']:.1f}/100, "
        f"ρ {nz['rho']:.2f}.",
        "- **Simulation:** per game, draw possessions (log-normal) and correlated per-100 "
        "efficiencies, regulation score, overtime loop. It simulates possession *counts* and "
        "efficiency, not each possession; its variances are calibrated to realized NBA "
        "margins/totals. Spread, total and ML read the same draws.",
        f"- **Sizing:** quarter-Kelly, 1u = 1% bankroll, EV band {cfg.min_edge:.0%}–"
        f"{cfg.max_edge:.0%}; caps {cfg.cap_bet:g}u bet / {cfg.cap_game:g}u game / "
        f"{cfg.cap_day:g}u day.",
        f"- **Result:** {len(r.candidates)} outcomes priced, **{len(bets)} qualified**, "
        f"{sum(b.units for b in bets):.1f}u staked.\n",
        "**Read this first:**",
        "1. **Lines are a single book** (DraftKings via ESPN's public feed) **with no capture "
        "timestamp**: not a verified consensus. Re-check every price before betting. Outlier "
        "multi-book odds, alt ladders and props are not used by this command.",
        "2. **Player props are not modeled.** The repo has no player-level engine yet, so no "
        "prop is priced or recommended; team totals are not in the ESPN feed.",
        "3. **Injuries and lineups are not model inputs.** ESPN listings are shown and used "
        "only as a rejection filter (F10).",
        *(
            []
            if r.injuries_verified
            else [
                "   **The injury feed was empty or unavailable, so every candidate is rejected (F10).**"
            ]
        ),
        "4. **The model is unpromoted and has lost to the market out of sample** (per the "
        "repo's backtest dated 2026-10, `docs/nba/phase2-market-backtest.md`: worse log loss than "
        "the closing market, confidence interval excluding zero). Every edge is "
        f"model-only unless marked *anchored* (model at {cfg.blend_weight:.1%} weight against the "
        "devigged market, the weight the repo fitted on earlier seasons).",
        *([f"5. Not simulated: {'; '.join(r.skipped)}."] if r.skipped else []),
        "",
        f"**Gates:** F1 pre-tip (≥{cfg.pretip_buffer_min:g} min) · F2 results fresh ≤7d "
        "(enforced when the team state is built) · F3 teams resolved, venue known · "
        f"F4 −{abs(cfg.price_min)} ≤ price < +{cfg.price_max} · F5 probabilities available · "
        f"F6 EV ≥{cfg.min_edge:.0%} and quarter-Kelly ≥0.5u · F7 EV ≤{cfg.max_edge:.0%} (larger "
        f"= model error) · F8 |model − devigged market| ≤{cfg.max_gap * 100:.0f} pts · "
        f"F9 EV >0 after a {cfg.stress_pts:g}-pt adverse shift · F10 no Out/Doubtful/"
        f"Questionable listing dated ≤{cfg.fresh_injury_hours:g}h on either team, and a usable "
        "injury feed · "
        f"F11 overround 1.00–{cfg.max_overround:.2f} · F12 caps and one exposure per side per game.\n",
    ]


def _section_b(r: SlateResult) -> list[str]:
    out = [
        "## B. Complete Game Projection Board\n",
        "| Game | Tip | Status | Proj score (A–H) | Margin (H) | Total | P(home) | P(OT) "
        "| Book spread / total / ML (H/A) | Model − book |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in r.projections:
        ln = g.lines
        if ln:
            book = f"{ln.sp_home_line:+g} / {ln.tot_line:g} / {ln.ml_home}/{ln.ml_away}"
            diff = (
                f"spr {g.margin_mean + ln.sp_home_line:+.1f}, tot {g.total_mean - ln.tot_line:+.1f}"
            )
        else:
            book, diff = "no line", "—"
        out.append(
            f"| {g.away}@{g.home} | {_tip(g)} | {_state(g)} | {g.away_pts:.1f}–{g.home_pts:.1f} | "
            f"{g.margin_mean:+.1f} | {g.total_mean:.1f} (80%: {g.total_p10:.0f}–{g.total_p90:.0f}) | "
            f"{_pp(g.p_home)} | {_pp(g.p_ot)} | {book} | {diff} |"
        )
    out += [
        "\n*Model − book: spread = model home margin implied vs the book's home line (positive = "
        "model likes the home side more); total = model total minus book total.*\n",
        "**Matchup efficiency (as-of ratings, points per 100 possessions vs league average):**\n",
        "| Team | Off | Def (neg = good) | Net | Elo |",
        "|---|---|---|---|---|",
    ]
    for team, row in sorted(r.ratings.items(), key=lambda kv: -kv[1]["net_per100"]):
        out.append(
            f"| {team} | {row['off_per100']:+.1f} | {row['def_per100']:+.1f} | "
            f"{row['net_per100']:+.1f} | {row['elo']:.0f} |"
        )
    return [*out, ""]


def _section_c(r: SlateResult) -> list[str]:
    out = [
        "## C. Master Value Board (all priced outcomes, sorted by model EV)\n",
        "EV = model-only expected profit per unit, push-aware. **Stress** = EV after the adverse "
        "shift. **Anchored** = EV with the model blended into the devigged market. Games that "
        "had already tipped are priced for reference only.\n",
        "| Game | Bet | Odds | Model p | Mkt p | EV | Stress | Anchored | Qtr-Kelly | Gates |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(r.candidates, key=lambda c: -c.edge):
        out.append(
            f"| {c.game} | {_sel(c)} | {c.odds:+d} | {_pp(c.p_model)} | {_pp(c.p_market)} | "
            f"{_pct(c.edge)} | {_pct(c.edge_stress)} | {_pct(c.edge_blend)} | "
            f"{c.kelly_units:.1f}u | {c.codes} |"
        )
    return [*out, ""]


def _section_d(r: SlateResult, bets: list[Candidate]) -> list[str]:
    games = {p.event_id: p for p in r.projections}
    out = ["## D. Top Bet Analysis\n"]
    for b in bets:
        g = games[b.event_id]
        inj = [*g.injuries_home, *g.injuries_away]
        out += [
            f"### {b.game} — {_sel(b)} ({b.odds:+d}), {b.units:.1f}u\n",
            f"- **Model:** {g.away} {g.away_pts:.1f} – {g.home} {g.home_pts:.1f}; {g.home} "
            f"projected {g.margin_mean:+.1f} (σ {g.margin_sd:.1f}). Win {_pp(b.p_win)}, push "
            f"{_pp(b.p_push)}, fair price {b.fair_odds} vs book {b.odds:+d}.",
            f"- **Edge:** EV {_pct(b.edge)} model-only; {_pct(b.edge_stress)} under the adverse "
            f"shift; **{_pct(b.edge_blend)} market-anchored**. Model–market gap "
            f"{100 * (b.p_model - b.p_market):+.1f} pts.",
        ]
        if b.breakeven is not None:
            out.append(
                f"- **Kill line:** still +EV at this price down to a line of **{b.breakeven:+g}** "
                f"({'home handicap' if b.market == 'SPREAD' else 'total'}); worse than that, pass. "
                f"Pass too if the price is worse than the model-fair {b.fair_odds}."
            )
        out += [
            "- **Injuries listed:** "
            + (
                "; ".join(f"{n.player} ({n.status})" for n in inj)
                or ("none listed" if r.injuries_verified else "injury feed unavailable")
            )
            + ".",
            "- **Risks:** team-level model with no lineup/rest/injury inputs; single-book "
            "untimestamped price; the model has underperformed the closing market out of sample"
            + (
                "; the market-anchored EV is not positive, so the edge may be model noise."
                if b.edge_blend <= 0
                else "."
            ),
            "",
        ]
    if not bets:
        out.append("No outcome cleared every gate.\n")
    near = sorted(
        (c for c in r.candidates if c.units == 0 and c.edge >= r.config.min_edge
         and not any(f.startswith("F1 ") for f in c.fails)),
        key=lambda c: -c.edge,
    )[:4]  # fmt: skip
    big = [c for c in r.candidates if any(f.startswith(("F7 ", "F8 ")) for f in c.fails)]
    if near:
        out.append("**Largest rejected edges:**\n")
        for c in near:
            g = games[c.event_id]
            out.append(
                f"- **{c.game} {_sel(c)} ({c.odds:+d}):** model EV {_pct(c.edge)}, gates "
                f"{c.codes}. Model total {g.total_mean:.1f} / margin {g.margin_mean:+.1f}"
                + (
                    f" vs book {g.lines.tot_line:g} / {g.lines.sp_home_line:+g}."
                    if g.lines
                    else "."
                )
            )
        if big:
            out.append(
                f"\n{len(big)} outcome(s) failed F7/F8 (an implausibly large edge or a wide "
                "model-market gap). A gap like that against a sharper price usually means the "
                "model cannot see something the market prices (tonight's availability, rest, "
                "rotations), not that the market is wrong.\n"
            )
        else:
            out.append("")
    return out


def _section_e(r: SlateResult) -> list[str]:
    out = [
        "## E. Best Opportunity by NBA Market\n",
        "| Market | Best model-EV outcome (pre-tip) | EV | Anchored | Gates | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    pre = [c for c in r.candidates if not any(f.startswith("F1 ") for f in c.fails)]
    for mk, label in (("ML", "Moneyline"), ("SPREAD", "Spread"), ("TOTAL", "Game total")):
        best = sorted((c for c in pre if c.market == mk), key=lambda c: -c.edge)
        if best:
            c = best[0]
            out.append(
                f"| {label} | {c.game} {_sel(c)} ({c.odds:+d}) | {_pct(c.edge)} | "
                f"{_pct(c.edge_blend)} | {c.codes} | {'qualified' if c.units > 0 else 'rejected'} |"
            )
        else:
            out.append(f"| {label} | no pre-tip line | — | — | — | no data |")
    out += [
        "| Team totals | not quoted in the ESPN feed | — | — | — | no data |",
        "| Player props / alt ladders | **not modeled** | — | — | — | no data |\n",
    ]
    return out


def _section_f(r: SlateResult, bets: list[Candidate]) -> list[str]:
    reasons: Counter[str] = Counter()
    for c in r.candidates:
        if c.units == 0:
            reasons.update(c.fails)
    out = [
        "## F. Rejected Opportunity Board\n",
        f"{len(r.candidates) - len(bets)} of {len(r.candidates)} outcomes rejected. Failure "
        "counts (an outcome can fail several):\n",
        "| Gate | Outcomes failing |",
        "|---|---|",
    ]
    out += [f"| {f} | {n} |" for f, n in sorted(reasons.items(), key=lambda kv: -kv[1])]
    return [*out, "\nFull per-outcome gate codes are in Section C.\n"]


def _section_g(r: SlateResult, bets: list[Candidate]) -> list[str]:
    games = {p.event_id: p for p in r.projections}
    out = ["## G. Official NBA Daily Bet Card\n"]
    if not bets:
        return [*out, "**No plays.**\n"]
    out += [
        "| Bet | Odds (single book, untimestamped) | Units | Model EV | Anchored EV | Status |",
        "|---|---|---|---|---|---|",
    ]
    for b in bets:
        status = "model-qualified, **low confidence**" if b.edge_blend <= 0 else "qualified"
        out.append(
            f"| {b.game} **{_sel(b)}** | {b.odds:+d} | {b.units:.1f}u | {_pct(b.edge)} | "
            f"{_pct(b.edge_blend)} | {status} |"
        )
    weak = [b for b in bets if b.edge_blend <= 0]
    if weak:
        out.append(
            "\n**My read:** "
            + ("these qualify" if len(weak) > 1 else "this qualifies")
            + " only on the model's own numbers. Once the model is shrunk toward the market the "
            "way the repo's backtest says to, the expected value is not positive. Treat "
            + ("them as shadow leans" if len(weak) > 1 else "it as a shadow lean")
            + ", not recommended wagers."
        )
    soonest = min((games[b.event_id] for b in bets), key=lambda g: g.tip)
    out.append(f"Re-verify each line and price first (the earliest tips {_tip(soonest)}).\n")
    return out


def _section_h(r: SlateResult, bets: list[Candidate]) -> list[str]:
    p, cfg = r.portfolio, r.config
    out = ["## H. Daily Portfolio Analysis\n"]
    if not bets or p.p_profit is None or p.worst is None or p.p50 is None:
        return [*out, "No exposure.\n"]
    per_game: defaultdict[str, float] = defaultdict(float)
    for b in bets:
        per_game[b.game] += b.units
    out += [
        f"- Exposure: {p.units:.1f}u of the {cfg.cap_day:g}u daily cap across {len(per_game)} "
        f"game(s): "
        + ", ".join(f"{g} {u:.1f}u" for g, u in per_game.items())
        + f" (game cap {cfg.cap_game:g}u).",
        f"- Model-only expected profit: **{p.ev_units:+.2f}u**. Market-anchored: "
        f"**{p.ev_units_blend:+.2f}u**.",
        f"- Simulated outcome of the card: P(profit) {_pp(p.p_profit)}, worst "
        f"{p.worst:+.1f}u, median {p.p50:+.2f}u.",
        "- Correlation: games are independent in the simulation; ML and spread on the same side "
        "count as one exposure, and a total is separate.\n",
    ]
    return out


def _section_i(r: SlateResult, bets: list[Candidate]) -> list[str]:
    out = ["## I. Final NBA Slate Verdict\n"]
    if not bets:
        return [*out, "**PASS.** Nothing cleared the gates.\n"]
    weak = [b for b in bets if b.edge_blend <= 0]
    blind = any(f.startswith(("F7 ", "F8 ")) for c in r.candidates for f in c.fails)
    names = ", ".join(f"{b.game} {_sel(b)} ({b.units:.1f}u)" for b in bets)
    if len(weak) == len(bets):
        out.append(
            f"**Lean PASS.** {len(bets)} play(s) cleared the model's gates ({names}), but none "
            "is confirmed once the model is anchored to the market, and the lines are "
            "single-book."
            + (
                " Large model edges elsewhere were rejected as likely missing-information "
                "artifacts."
                if blind
                else ""
            )
            + " For a real card: multi-book odds, props, and an injury/lineup layer."
        )
    else:
        out.append(
            f"**{len(bets)} play(s) qualified** ({names}); {len(bets) - len(weak)} keep a "
            "positive expected value after market anchoring. Still single-book, untimestamped "
            "prices from an unpromoted model: size down and re-verify before betting."
        )
    return [*out, ""]


def render_markdown(r: SlateResult) -> str:
    bets = [c for c in r.candidates if c.units > 0]
    lines: list[str] = []
    for part in (
        _section_a(r, bets), _section_b(r), _section_c(r), _section_d(r, bets),
        _section_e(r), _section_f(r, bets), _section_g(r, bets), _section_h(r, bets),
        _section_i(r, bets),
    ):  # fmt: skip
        lines += part
    return "\n".join(lines) + "\n"


__all__ = ["render_markdown"]
