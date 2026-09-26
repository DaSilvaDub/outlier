"""Game script calibration and empirical hit-rate tiering for outlier_nfl.

Implements calibration heuristics learned from empirical postgame reconciliation:
1. Deficit-Risk Haircut on Road Underdog RB Rushing Lines (-15% volume adjustment).
2. Two-High Shell Target Divergence in Comeback Mode (+20% Slot/TE, -25% Deep Threat).
3. Empirical Hit Rate Priority (Tier-1 Anchor classification for 100% L5 / 80%+ L10).
4. Emit ``model_p`` from empirical hit rates (L10→L20→L5→season) — never from
   book ``implied_probability``. Volume haircuts stay tags; they are not mapped
   into probability without a distributional model.
"""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

from outlier_nfl.config import is_team_total
from outlier_nfl.models import NflGameLine, NflPlayerProp

logger = logging.getLogger("outlier_nfl.calibration")

# Known archetypes for pass-catchers
KNOWN_SLOT_INTERMEDIATE_TARGETS: tuple[str, ...] = (
    "Amon-Ra St. Brown",
    "A. St. Brown",
    "Khalil Shakir",
    "K. Shakir",
    "Cooper Kupp",
    "C. Kupp",
    "Puka Nacua",
    "P. Nacua",
    "Keenan Allen",
    "K. Allen",
    "Christian Kirk",
    "C. Kirk",
    "Wan'Dale Robinson",
    "W. Robinson",
    "Jayden Reed",
    "J. Reed",
    "Zay Flowers",
    "Z. Flowers",
    "Tank Dell",
    "T. Dell",
    "Josh Downs",
    "J. Downs",
)

KNOWN_VERTICAL_DEEP_THREATS: tuple[str, ...] = (
    "Jameson Williams",
    "J. Williams",
    "Rashid Shaheed",
    "R. Shaheed",
    "Alec Pierce",
    "A. Pierce",
    "Christian Watson",
    "C. Watson",
    "Darius Slayton",
    "D. Slayton",
    "Marquez Valdes-Scantling",
    "M. Valdes-Scantling",
    "Gabe Davis",
    "G. Davis",
    "Jalin Hyatt",
    "J. Hyatt",
)


MODEL_P_SOURCE_EMPIRICAL_HIT_RATE = "empirical_hit_rate"
MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE = "empirical_hit_rate_laplace"
MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_BETA = "empirical_hit_rate_beta"

# Assumed trial counts when Outlier only ships a rate (no explicit n).
# L10 is the preferred window for Tier-1; season uses a full-season proxy.
HIT_RATE_WINDOW_N: dict[str, int] = {
    "l10": 10,
    "l20": 20,
    "l5": 5,
    "season": 17,
}

# Principled default: add-2 smoothing. On 2026-09-20 holdout, α∈[1.5, 12]
# beat market Brier; α=4 minimized that one slate — do not treat the peak as
# locked without more Sundays. α=2 is pre-specified and still beats market.
DEFAULT_LAPLACE_ALPHA = 2.0


def _normalize_hit_rate(value: Any) -> float | None:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if not (p == p) or p < 0:
        return None
    if 0.0 <= p <= 1.0:
        return p
    if 1.0 < p <= 100.0:
        return p / 100.0
    return None


def select_empirical_hit_rate(
    *,
    l5_hit_rate: float | None = None,
    l10_hit_rate: float | None = None,
    l20_hit_rate: float | None = None,
    season_hit_rate: float | None = None,
) -> tuple[float, int, str] | None:
    """Return (p, assumed_n, window_name) using L10→L20→L5→season preference."""
    candidates = (
        ("l10", l10_hit_rate),
        ("l20", l20_hit_rate),
        ("l5", l5_hit_rate),
        ("season", season_hit_rate),
    )
    for window, raw in candidates:
        p = _normalize_hit_rate(raw)
        if p is None:
            continue
        return (p, HIT_RATE_WINDOW_N[window], window)
    return None


def shrink_hit_rate(
    p: float,
    n: int,
    *,
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
) -> float:
    """Beta/Laplace posterior mean: (p*n + α) / (n + α + β).

    When ``beta`` is None, uses β=α (symmetric Laplace / add-α smoothing).
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if alpha < 0:
        raise ValueError("alpha must be >= 0")
    b = alpha if beta is None else float(beta)
    if b < 0:
        raise ValueError("beta must be >= 0")
    hits = float(p) * float(n)
    return (hits + float(alpha)) / (float(n) + float(alpha) + b)


def compute_empirical_model_p(
    *,
    l5_hit_rate: float | None = None,
    l10_hit_rate: float | None = None,
    l20_hit_rate: float | None = None,
    season_hit_rate: float | None = None,
) -> float | None:
    """Selected-side empirical P(hit) in [0, 1], or None if no usable hit rate.

    Preference order favors the longer / more stable window first. Tier-1 rows
    often have ``l5_hit_rate == 1.0`` by construction, so L10 is preferred when
    present. Accepts 0–1 fractions or 0–100 percentages. Does **not** use book
    implied probability.
    """
    selected = select_empirical_hit_rate(
        l5_hit_rate=l5_hit_rate,
        l10_hit_rate=l10_hit_rate,
        l20_hit_rate=l20_hit_rate,
        season_hit_rate=season_hit_rate,
    )
    if selected is None:
        return None
    return round(selected[0], 6)


def compute_shrunk_empirical_model_p(
    *,
    l5_hit_rate: float | None = None,
    l10_hit_rate: float | None = None,
    l20_hit_rate: float | None = None,
    season_hit_rate: float | None = None,
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
    method: str = "laplace",
) -> tuple[float, str] | None:
    """Shrunk empirical P(hit) and source stamp, or None if no usable rate.

    method:
      - laplace: β=α (ignores ``beta`` arg except when method=beta)
      - beta: uses provided β (default β=α when None)
    Never uses book implied probability.
    """
    selected = select_empirical_hit_rate(
        l5_hit_rate=l5_hit_rate,
        l10_hit_rate=l10_hit_rate,
        l20_hit_rate=l20_hit_rate,
        season_hit_rate=season_hit_rate,
    )
    if selected is None:
        return None
    p, n, _window = selected
    if method == "laplace":
        shrunk = shrink_hit_rate(p, n, alpha=alpha, beta=None)
        source = MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_LAPLACE
    elif method == "beta":
        shrunk = shrink_hit_rate(p, n, alpha=alpha, beta=beta)
        source = MODEL_P_SOURCE_EMPIRICAL_HIT_RATE_BETA
    else:
        raise ValueError(f"Unsupported shrink method: {method}")
    return (round(shrunk, 6), source)


def attach_empirical_model_p(
    prop: NflPlayerProp,
    *,
    overwrite: bool = False,
    method: str = "raw",
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
) -> NflPlayerProp:
    """Return prop with ``model_p`` from empirical hit rates when missing.

    method: ``raw`` | ``laplace`` | ``beta``. Preserves an already-set
    ``model_p`` unless ``overwrite`` is True. Never copies ``implied_probability``.
    """
    if prop.model_p is not None and not overwrite:
        return prop
    if method == "raw":
        model_p = compute_empirical_model_p(
            l5_hit_rate=prop.l5_hit_rate,
            l10_hit_rate=prop.l10_hit_rate,
            l20_hit_rate=prop.l20_hit_rate,
            season_hit_rate=prop.season_hit_rate,
        )
        source = MODEL_P_SOURCE_EMPIRICAL_HIT_RATE
    else:
        shrunk = compute_shrunk_empirical_model_p(
            l5_hit_rate=prop.l5_hit_rate,
            l10_hit_rate=prop.l10_hit_rate,
            l20_hit_rate=prop.l20_hit_rate,
            season_hit_rate=prop.season_hit_rate,
            alpha=alpha,
            beta=beta,
            method=method,
        )
        if shrunk is None:
            model_p, source = None, None
        else:
            model_p, source = shrunk
    if model_p is None:
        if prop.model_p is None and prop.model_p_source is None:
            return prop
        return replace(prop, model_p=None, model_p_source=None)
    return replace(prop, model_p=model_p, model_p_source=source)


def attach_empirical_model_p_record(
    record: dict[str, Any],
    *,
    overwrite: bool = False,
    method: str = "raw",
    alpha: float = DEFAULT_LAPLACE_ALPHA,
    beta: float | None = None,
) -> dict[str, Any]:
    """Mutate/return a prop dict with empirical ``model_p`` when missing.

    Same contract as :func:`attach_empirical_model_p` for JSON artifacts
    (historical packs / enrich_close). Never copies implied_probability.
    """
    if record.get("model_p") is None and record.get("p_model") is not None:
        record["model_p"] = record.get("p_model")
    if record.get("model_p") is not None and not overwrite:
        record.setdefault("model_p_source", record.get("model_p_source"))
        return record
    if method == "raw":
        model_p = compute_empirical_model_p(
            l5_hit_rate=record.get("l5_hit_rate"),
            l10_hit_rate=record.get("l10_hit_rate"),
            l20_hit_rate=record.get("l20_hit_rate"),
            season_hit_rate=record.get("season_hit_rate"),
        )
        source: str | None = MODEL_P_SOURCE_EMPIRICAL_HIT_RATE
    else:
        shrunk = compute_shrunk_empirical_model_p(
            l5_hit_rate=record.get("l5_hit_rate"),
            l10_hit_rate=record.get("l10_hit_rate"),
            l20_hit_rate=record.get("l20_hit_rate"),
            season_hit_rate=record.get("season_hit_rate"),
            alpha=alpha,
            beta=beta,
            method=method,
        )
        if shrunk is None:
            model_p, source = None, None
        else:
            model_p, source = shrunk
    if model_p is None:
        record.setdefault("model_p", None)
        record.setdefault("model_p_source", None)
        return record
    record["model_p"] = model_p
    record["model_p_source"] = source
    return record


def extract_game_script_context(game_lines: list[NflGameLine]) -> dict[str, dict[str, Any]]:
    """Extract deficit risk and pace context per event and team.

    Returns a dict keyed by event_id containing team-specific game script contexts.
    """
    contexts: dict[str, dict[str, Any]] = {}

    # Group game lines by event_id
    by_event: dict[str, list[NflGameLine]] = {}
    for g in game_lines:
        if g.scope == "full_game":
            by_event.setdefault(g.event_id, []).append(g)

    for event_id, lines in by_event.items():
        home_team = lines[0].home_team if lines else ""
        away_team = lines[0].away_team if lines else ""

        # Extract Spreads
        spreads = [ln for ln in lines if ln.market == "SPREAD"]
        away_spread_val = 0.0
        for s in spreads:
            if s.team == away_team and float(s.line or 0) > 0:
                away_spread_val = max(away_spread_val, float(s.line or 0))

        # Extract Team Totals. `proposition` carries the raw feed string, so
        # compare it through the canonical is_team_total() predicate rather than
        # against literal spellings: "TEAM_TOTAL_POINTS" / "Team Total Points"
        # are real team totals, and dropping them pins home_tt at the 27.0
        # default, which sits below the 28.0 deficit-risk threshold and so
        # silently disables the haircut entirely.
        team_totals = [
            ln for ln in lines
            if ln.market_type == "TEAM_PROP" and is_team_total(ln.proposition)
        ]
        # Seeding the accumulator with the default made the default a floor, so a
        # team total genuinely below it was reported as the default instead.
        home_tt_quoted: float | None = None
        away_tt_quoted: float | None = None
        for tt in team_totals:
            if tt.line is None:
                continue
            tt_line = float(tt.line)
            if tt.team == home_team:
                home_tt_quoted = tt_line if home_tt_quoted is None else max(home_tt_quoted, tt_line)
            elif tt.team == away_team:
                away_tt_quoted = tt_line if away_tt_quoted is None else max(away_tt_quoted, tt_line)

        home_tt = home_tt_quoted if home_tt_quoted is not None else 27.0
        away_tt = away_tt_quoted if away_tt_quoted is not None else 21.0

        # Road underdog deficit risk trigger: away underdog >= +4.5 and home total >= 28.0
        away_deficit_risk = (away_spread_val >= 4.5) and (home_tt >= 28.0)

        contexts[event_id] = {
            "home_team": home_team,
            "away_team": away_team,
            "away_spread": away_spread_val,
            "home_team_total": home_tt,
            "away_team_total": away_tt,
            "away_deficit_risk": away_deficit_risk,
            "home_deficit_risk": False,
        }

    return contexts


def apply_game_script_calibration(
    game_lines: list[NflGameLine],
    props: list[NflPlayerProp],
) -> list[NflPlayerProp]:
    """Apply empirical game script calibration, volume haircuts, and tier ranking.

    Returns a new list of calibrated NflPlayerProp instances.
    """
    event_contexts = extract_game_script_context(game_lines)
    calibrated_props: list[NflPlayerProp] = []

    for prop in props:
        tags: list[str] = list(prop.calibration_tags)
        vol_adj: float | None = prop.calibrated_volume_adjustment
        tier: str = "STANDARD"

        ctx = event_contexts.get(prop.event_id, {})
        team = prop.team or ""
        is_underdog_in_deficit_risk = (
            (team == ctx.get("away_team") and ctx.get("away_deficit_risk"))
            or (team == ctx.get("home_team") and ctx.get("home_deficit_risk"))
        )

        # 1. Deficit-Risk Adjustment on Underdog Running Backs
        if is_underdog_in_deficit_risk:
            if prop.market in ("RUSH_YDS", "RUSH_ATT") and prop.position == "OVER":
                tags.append("DEFICIT_VOLUME_RISK")
                vol_adj = -0.15  # 15% downward volume haircut
            elif prop.market in ("ANYTIME_TD", "REC_YDS", "REC", "RUSH_REC_YDS"):
                tags.append("RESILIENT_GAME_SCRIPT_TARGET")

        # 2. Defensive Shell Target Divergence for Pass Catchers
        if is_underdog_in_deficit_risk:
            # Check slot/intermediate targets
            if any(name in prop.player_name for name in KNOWN_SLOT_INTERMEDIATE_TARGETS):
                tags.append("SHELL_COVERAGE_TARGET_UPGRADE")
                if prop.market in ("REC_YDS", "REC"):
                    vol_adj = (vol_adj or 0.0) + 0.20
            # Check tight end safety valves
            elif prop.market in ("REC_YDS", "REC") and any(te in prop.player_name for te in ("LaPorta", "Kincaid", "Kelce", "McBride", "Kittle", "Ferguson", "Hockenson", "Njoku", "Goedert")):
                tags.append("SHELL_COVERAGE_TARGET_UPGRADE")
                vol_adj = (vol_adj or 0.0) + 0.15
            # Check vertical deep threats
            elif any(name in prop.player_name for name in KNOWN_VERTICAL_DEEP_THREATS):
                tags.append("SHELL_COVERAGE_DEEP_HAIRCUT")
                if prop.market in ("REC_YDS", "REC"):
                    vol_adj = (vol_adj or 0.0) - 0.25

        # 3. Empirical Hit Rate Priority (Tier 1 / Tier 2)
        l5 = prop.l5_hit_rate
        l10 = prop.l10_hit_rate
        book_count = len(prop.books)

        if l5 is not None and l5 == 1.0 and l10 is not None and l10 >= 0.80 and book_count >= 3:
            tier = "TIER_1_ANCHOR"
            tags.append("HIGH_HIT_RATE_ANCHOR")
        elif l5 is not None and l5 >= 0.80 and l10 is not None and l10 >= 0.70:
            tier = "TIER_2_STRONG"
            tags.append("CONSISTENT_HIT_RATE")

        updated = replace(
            prop,
            confidence_tier=tier,
            calibration_tags=tuple(tags),
            calibrated_volume_adjustment=round(vol_adj, 2) if vol_adj is not None else None,
        )
        calibrated_props.append(attach_empirical_model_p(updated, method="laplace"))

    return calibrated_props
