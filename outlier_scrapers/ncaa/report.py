"""Slate report rendering, Sections A through G (spec Part 11).

The renderer is also where spec Part 14 is enforced mechanically rather than
by good intentions. No wager may be described as guaranteed, safe, a lock, or
certain, so :func:`certainty_language_violations` scans the rendered text for
that vocabulary and :func:`render_slate_report` refuses to return a report that
trips it. A rule that only exists in a style guide is a rule that eventually
gets broken by a template change; this one fails loudly instead.

Output is Markdown so it drops into the same report folders as the rest of the
pipeline's artifacts.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from outlier_scrapers.ncaa.parlay import Parlay, ParlayFragility
from outlier_scrapers.ncaa.pipeline import GameAssessment, SlateAnalysis
from outlier_scrapers.ncaa.totals import PASS

__all__ = [
    "CERTAINTY_PATTERNS",
    "CertaintyLanguageError",
    "certainty_language_violations",
    "render_slate_report",
]

#: Vocabulary forbidden by spec Part 14. Word-boundary anchored so ordinary
#: football language ("lockdown corner", "safety") is unaffected.
CERTAINTY_PATTERNS: tuple[str, ...] = (
    r"\bguarantee(?:d|s)?\b",
    r"\block\b",
    r"\blocks\b",
    r"\bsafe bets?\b",
    r"\bsure thing\b",
    r"\bcan(?:no|')t lose\b",
    r"\bcannot lose\b",
    r"\brisk[- ]free\b",
    r"\briskless\b",
    r"\bno[- ]risk\b",
    r"\bcertainty\b",
    r"\bcertain (?:win|winner|thing)\b",
)

_COMPILED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in CERTAINTY_PATTERNS)


class CertaintyLanguageError(ValueError):
    """Raised when a rendered report describes a wager as certain."""


def certainty_language_violations(text: str) -> tuple[str, ...]:
    """Return every forbidden phrase found in ``text``."""
    found: list[str] = []
    for pattern in _COMPILED:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return tuple(dict.fromkeys(found))


def _pct(value: float | None, digits: int = 1) -> str:
    return "--" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def _signed(value: float | None, digits: int = 1) -> str:
    return "--" if value is None else f"{value:+.{digits}f}"


def _american(value: float | None) -> str:
    if value is None:
        return "--"
    return f"+{value:.0f}" if value > 0 else f"{value:.0f}"


def _location(assessment: GameAssessment) -> str:
    if assessment.features.neutral_site:
        return "N"
    return "H" if assessment.features.favorite_is_home else "A"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_slate_report(analysis: SlateAnalysis) -> str:
    """Render Sections A-G for a completed slate analysis."""
    lines: list[str] = []
    label = analysis.label or "NCAA Football Slate"
    lines.append(f"# {label}")
    lines.append("")
    lines.append(
        "Probabilistic decision support. Every figure below is an estimate carrying "
        "loss risk, including the highest-probability selections."
    )
    lines.append("")

    lines.extend(_section_a(analysis))
    lines.extend(_section_b(analysis))
    lines.extend(_section_c(analysis))
    lines.extend(_section_d(analysis))
    lines.extend(_section_e(analysis))
    lines.extend(_section_f(analysis))
    lines.extend(_section_g(analysis))

    text = "\n".join(lines).rstrip() + "\n"
    violations = certainty_language_violations(text)
    if violations:
        raise CertaintyLanguageError(
            "Report describes a wager in terms of certainty: " + ", ".join(violations)
        )
    return text


def _section_a(analysis: SlateAnalysis) -> list[str]:
    lines = ["## Section A - Slate Overview", ""]
    scored = [a for a in analysis.assessments if a.win.model_prob is not None]
    lines.append(f"- Games evaluated: **{analysis.games_evaluated}** ({len(scored)} scored)")
    lines.append(
        f"- Tier counts: CORE **{len(analysis.core)}**, "
        f"SUPPORTING **{len(analysis.supporting)}**, "
        f"held out **{len(analysis.avoid)}**"
    )
    lines.append(f"- Probability calibration in force: `{analysis.calibration_method}`")

    strongest = sorted(scored, key=lambda a: -(a.win.model_prob or 0.0))[:3]
    if strongest:
        lines.append(
            "- Strongest favorites: "
            + ", ".join(f"{a.win.favorite} ({_pct(a.win.model_prob)})" for a in strongest)
        )
    riskiest = sorted(
        (a for a in scored if (a.win.model_prob or 0.0) >= 0.75),
        key=lambda a: -a.win.upset_risk,
    )[:3]
    if riskiest:
        lines.append(
            "- Highest-risk favorites: "
            + ", ".join(f"{a.win.favorite} (upset risk {a.win.upset_risk:.0f})" for a in riskiest)
        )

    totals = analysis.actionable_totals[:3]
    if totals:
        lines.append(
            "- Most interesting totals: "
            + ", ".join(
                f"{t.away_team} @ {t.home_team} {t.recommendation} "
                f"({_signed(t.total_edge)})"
                for t in totals
            )
        )
    else:
        lines.append("- Most interesting totals: none cleared the edge threshold")

    considerations = _slate_considerations(analysis)
    if considerations:
        lines.append("- Injury / weather / news considerations:")
        lines.extend(f"  - {item}" for item in considerations)
    if analysis.flags:
        lines.append(f"- Pipeline flags: `{', '.join(analysis.flags)}`")
    lines.append("")
    return lines


def _slate_considerations(analysis: SlateAnalysis) -> list[str]:
    items: list[str] = []
    for assessment in analysis.assessments:
        win = assessment.win
        notable = [
            flag
            for flag in win.flags
            if flag
            in {
                "unresolved_injury_report",
                "stale_market",
                "incomplete_critical_inputs",
                "small_sample",
                "fundamental_unavailable",
            }
        ]
        weather = [k for k in assessment.total.adjustments if k in {"wind", "precipitation", "extreme_cold"}]
        if notable or weather:
            detail = ", ".join(notable + weather)
            items.append(f"{win.favorite} vs {win.underdog}: {detail}")
    return items[:8]


def _section_b(analysis: SlateAnalysis) -> list[str]:
    lines = [
        "## Section B - Moneyline Safety Board",
        "",
        "Ranked by model win probability. High probability is not the same as a good "
        "price; the edge column is what makes it a wager rather than a prediction.",
        "",
    ]
    eligible = [a for a in analysis.assessments if a.tier.parlay_eligible]
    eligible.sort(key=lambda a: (-(a.win.model_prob or 0.0), a.win.upset_risk))
    if not eligible:
        lines.append("_No favorite qualified for the board._")
        lines.append("")
        return lines

    rows = []
    for a in eligible:
        rows.append(
            [
                a.win.favorite,
                a.win.underdog,
                _location(a),
                _american(a.value.price_american),
                _num(a.features.market_spread_favorite, 1),
                _pct(a.win.model_prob),
                _pct(a.value.market_prob_fair),
                _signed((a.value.probability_edge or 0.0) * 100, 2) + "pp"
                if a.value.probability_edge is not None
                else "--",
                _num(a.mismatch.score, 1),
                f"{a.win.confidence:.0f}",
                f"{a.win.upset_risk:.0f}",
                a.tier.tier,
                a.tier.primary_reason,
                a.tier.primary_concern or "--",
            ]
        )
    lines.extend(
        _table(
            [
                "Team",
                "Opponent",
                "Loc",
                "ML",
                "Spread",
                "Model",
                "Market",
                "Edge",
                "Mismatch",
                "Conf",
                "Upset",
                "Tier",
                "Primary reason",
                "Primary concern",
            ],
            rows,
        )
    )
    lines.append("")
    return lines


def _section_c(analysis: SlateAnalysis) -> list[str]:
    lines = [
        "## Section C - Do-Not-Use Favorites",
        "",
        "Favorites that look usable by ranking, reputation, or price, and the specific "
        "reason each was held out of the parlay pool.",
        "",
    ]
    held = [a for a in analysis.avoid if (a.win.model_prob or 0.0) >= 0.70]
    held.sort(key=lambda a: -(a.win.model_prob or 0.0))
    if not held:
        lines.append("_No heavily favored team was held out this slate._")
        lines.append("")
        return lines
    rows = [
        [
            a.win.favorite,
            a.win.underdog,
            _pct(a.win.model_prob),
            f"{a.win.confidence:.0f}",
            f"{a.win.upset_risk:.0f}",
            ", ".join(a.tier.blocking) or (a.tier.primary_concern or "below thresholds"),
        ]
        for a in held
    ]
    lines.extend(_table(["Team", "Opponent", "Model", "Conf", "Upset", "Why not"], rows))
    lines.append("")
    return lines


def _section_d(analysis: SlateAnalysis) -> list[str]:
    lines = ["## Section D - Parlay Builder", ""]
    if not analysis.parlays:
        lines.append(
            "_No parlay met the construction rules. PASS is a valid outcome; forcing a "
            "ticket by relaxing the leg-quality rule would be the only way to produce one._"
        )
        lines.append("")
        return lines

    for size in sorted(analysis.parlays):
        parlay = analysis.parlays[size]
        marker = " *(best risk/reward)*" if parlay is analysis.best_parlay else ""
        lines.append(f"### {size}-leg parlay{marker}")
        lines.append("")
        lines.extend(_parlay_block(parlay))
        lines.append("")
    return lines


def _parlay_block(parlay: Parlay) -> list[str]:
    lines = []
    rows = [
        [
            leg.team,
            leg.opponent,
            _american(leg.american_price),
            _pct(leg.model_prob, 2),
            _pct(leg.market_prob_fair, 2),
            f"{leg.confidence:.0f}",
            f"{leg.upset_risk:.0f}",
            leg.tier,
        ]
        for leg in parlay.legs
    ]
    lines.extend(
        _table(
            ["Leg", "Opponent", "Price", "Model", "Market", "Conf", "Upset", "Tier"],
            rows,
        )
    )
    lines.append("")
    lines.append(f"- Estimated win probability: **{_pct(parlay.p_correlated, 2)}**")
    lines.append(
        f"- Independent-assumption estimate: {_pct(parlay.p_independent, 2)} "
        f"(shared exposure moves it by {_signed(parlay.correlation_lift * 100, 2)}pp)"
    )
    lines.append(
        f"- Under calibration stress: {_pct(parlay.p_stressed, 2)} "
        "(what it is worth if every leg is slightly overconfident)"
    )
    lines.append(
        f"- Price: {parlay.decimal_price:.3f} decimal "
        f"({_american(parlay.american_price)}), implying {_pct(parlay.sportsbook_implied_prob, 2)}"
    )
    if parlay.estimated_edge is not None:
        lines.append(f"- Estimated edge: {_signed(parlay.estimated_edge * 100, 2)}pp")
    lines.append(f"- Expected value: {_signed(parlay.ev_per_unit, 4)} per unit")
    lines.append(f"- Risk rating: {parlay.risk_score:.0f}/100")
    lines.append(f"- Weakest leg: {parlay.weakest_leg or '--'}")
    if parlay.second_weakest_leg:
        lines.append(f"- Second-weakest leg: {parlay.second_weakest_leg}")
    lines.append(f"- Average confidence: {parlay.average_confidence:.0f}")
    lines.append(
        f"- Correlated risks: {parlay.correlated_risk_count} pair(s)"
        + (f" via {', '.join(parlay.correlated_risk_tags)}" if parlay.correlated_risk_tags else "")
    )
    if parlay.construction_reason:
        lines.append(f"- Construction: {parlay.construction_reason}")
    if parlay.rejected_legs:
        lines.append("- Legs considered and rejected:")
        for rejection in parlay.rejected_legs[:6]:
            lines.append(f"  - **{rejection.team}** ({rejection.rule}): {rejection.detail}")
    return lines


def _section_e(analysis: SlateAnalysis) -> list[str]:
    lines = [
        "## Section E - Parlay Fragility Test",
        "",
        "If a parlay loses, which leg is most likely responsible?",
        "",
    ]
    if not analysis.fragilities:
        lines.append("_No parlays to test._")
        lines.append("")
        return lines
    for size in sorted(analysis.fragilities):
        fragility = analysis.fragilities[size]
        lines.append(f"### {size}-leg parlay")
        lines.append("")
        lines.append(fragility.explanation)
        lines.append("")
        lines.extend(_fragility_table(fragility))
        lines.append("")
    return lines


def _fragility_table(fragility: ParlayFragility) -> list[str]:
    rows = [
        [team, _pct(fragility.sole_failure_prob[team], 2), _pct(share, 1)]
        for team, share in sorted(fragility.blame_share.items(), key=lambda kv: -kv[1])
    ]
    rows.append(
        ["_multiple legs_", "--", _pct(fragility.multi_leg_failure_share, 1)]
    )
    return _table(["Leg", "P(only this leg fails)", "Share of losing outcomes"], rows)


def _section_f(analysis: SlateAnalysis) -> list[str]:
    lines = ["## Section F - Totals Board", ""]
    totals = [a.total for a in analysis.assessments if a.total.projected_total is not None]
    if not totals:
        lines.append("_No game had the inputs required to project a total._")
        lines.append("")
        return lines
    totals.sort(key=lambda t: -abs(t.total_edge or 0.0))
    rows = [
        [
            f"{t.away_team} @ {t.home_team}",
            _num(t.market_total, 1),
            _num(t.projected_total, 1),
            _signed(t.total_edge, 1),
            _pct(t.over_probability),
            _pct(t.under_probability),
            f"{t.confidence:.0f}",
            t.recommendation,
            "; ".join(t.primary_drivers[:2]) or "--",
        ]
        for t in totals
    ]
    lines.extend(
        _table(
            [
                "Game",
                "Market",
                "Projected",
                "Diff",
                "Over",
                "Under",
                "Conf",
                "Rec",
                "Primary drivers",
            ],
            rows,
        )
    )
    lines.append("")
    return lines


def _section_g(analysis: SlateAnalysis) -> list[str]:
    lines = ["## Section G - Best Totals", ""]
    actionable = analysis.actionable_totals
    if not actionable:
        lines.append("**PASS** - no total showed sufficient edge to recommend.")
        lines.append("")
    else:
        for index, total in enumerate(actionable[:3], start=1):
            lines.append(
                f"{index}. **{total.away_team} @ {total.home_team} {total.recommendation} "
                f"{_num(total.market_total, 1)}** - projected {_num(total.projected_total, 1)}, "
                f"edge {_signed(total.total_edge, 1)}, confidence {total.confidence:.0f}"
            )
            if total.primary_drivers:
                lines.append(f"   - {total.primary_drivers[0]}")
        lines.append("")

    passed = [
        a.total
        for a in analysis.assessments
        if a.total.recommendation == PASS and a.total.projected_total is not None
    ]
    if passed:
        lines.append(
            f"**PASS ({len(passed)} games)**: edge below the threshold, or the projection "
            "and the market agree closely enough that the price is not worth paying."
        )
        lines.append("")
    return lines
