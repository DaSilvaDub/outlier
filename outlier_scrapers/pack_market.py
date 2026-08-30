"""Market/odds resolution and formatting helpers for the pack writer.

Pure functions that resolve a candidate's market identity, price, and display
text from the triage cards + EV records. No candidate-selection policy and no
row assembly lives here — see ``pack_selection.py`` for that.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from outlier_scrapers.registry import classify_foreign_market

_SLATE_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

NO_PUSH_MARKETS = {"MONEYLINE", "ML", "ML_3WAY", "MONEYLINE_3WAY"}

# House rule: HR prop markets are excluded from
# packs entirely (low hit-rate longshot markets). Covers both the normalized
# short codes (registry.py) and the raw proposition tokens.
EXCLUDED_MARKETS = {
    "HR",
    "HOME_RUNS",
    "WALKS_ALLOWED",
    "WALKSALLOWED",
    "PITCHER_WALKS",
    "PITCHING_WALKS",
    "WALKS ALLOWED",
}

# House rule: plus-money longshots (e.g. a Hits Over at +181) are hard-filtered
# from packs. Any candidate priced at +LONGSHOT_AMERICAN_PRICE or longer is dropped.
LONGSHOT_AMERICAN_PRICE = 150

# Propositions where the line is a signed margin (point spread / run line / puck
# line) rather than a magnitude. A positive value here means the side is getting
# a cushion, not that it's the favorite — the same "1.5" that's unambiguous on a
# TOTAL is easy to mis-sign on a SPREAD, and different readers guess differently.
SIGNED_MARGIN_PROPOSITIONS = {"SPREAD"}

_GAMELINE_SIDE_PROPOSITIONS = {"MONEYLINE", "SPREAD", "RUN_LINE", "PUCK_LINE"}

# A single player prop line above this is a data artifact, not a real market
# (no MLB/WNBA single-player line approaches it). Deliberately generous so a
# high-but-real line — e.g. a starter's ~130 pitches-thrown — never trips it.
PLAYER_PROP_LINE_CEILING = 300.0


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def american_to_decimal(american: float | int | str | None) -> float | None:
    if american is None or american == "":
        return None
    try:
        val = float(american)
    except (ValueError, TypeError):
        return None
    if val > 0:
        return (val / 100.0) + 1.0
    if val < 0:
        return (100.0 / abs(val)) + 1.0
    return 2.0


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _selected_ev_devig_decimal(
    record: dict[str, Any], ev_summary: dict[str, Any] | None
) -> tuple[float | None, bool]:
    """Return devig decimal from the same method that produced the record EV.

    Native EV records retain every calculated method in ``sport_context``.
    When that contract is present, do not fall back to the legacy top-level
    devig field: it may describe a different method.  The boolean indicates
    that the selected-method contract was present, so callers can validate EV
    coherence and fail closed.  Local/legacy records keep their existing
    top-level decimal fallback.
    """
    context = record.get("sport_context")
    if isinstance(context, dict):
        methods = context.get("calculated_ev_methods")
        if isinstance(methods, dict):
            method = str(
                context.get("selected_ev_method")
                or record.get("calculated_ev_method")
                or (ev_summary or {}).get("method")
                or ""
            ).strip()
            payload = methods.get(method) if method else None
            no_vig = payload.get("noVigOdds") if isinstance(payload, dict) else None
            decimal = _to_float(no_vig.get("decimal")) if isinstance(no_vig, dict) else None
            return (decimal if decimal is not None and decimal > 1.0 else None), True

    for source in (record, ev_summary or {}):
        decimal = _to_float(source.get("devig_decimal"))
        if decimal is not None and decimal > 1.0:
            return decimal, False
    return None, False


def index_ev_by_outcome(ev_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_outcome: dict[str, list[dict[str, Any]]] = {}
    for rec in ev_records:
        oid = rec.get("outcome_id")
        if oid:
            by_outcome.setdefault(str(oid), []).append(rec)
    return by_outcome


def match_ev_records(
    market_id: str | None,
    outcome_id: str | None,
    headline_side: str | None,
    line: float | None,
    ev_records: list[dict[str, Any]],
    by_outcome: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if outcome_id and str(outcome_id) in by_outcome:
        return by_outcome[str(outcome_id)]
    if market_id is None:
        return []
    return [
        r
        for r in ev_records
        if r.get("market_id") == market_id
        and r.get("side") == headline_side
        and r.get("current_line") == line
    ]


def is_excluded_market(market_token: str | None, market_type: str | None) -> bool:
    for tok in (market_token, market_type):
        if not tok:
            continue
        upper_tok = str(tok).strip().upper()
        norm_tok = upper_tok.replace(" ", "_").replace("-", "_")
        clean_tok = norm_tok.replace("_", "")
        if (
            upper_tok in EXCLUDED_MARKETS
            or norm_tok in EXCLUDED_MARKETS
            or clean_tok in EXCLUDED_MARKETS
        ):
            return True
    return False


def is_longshot_price(price: Any) -> bool:
    if price in (None, ""):
        return False
    try:
        val = float(str(price).replace("+", ""))
    except (ValueError, TypeError):
        return False
    return val >= LONGSHOT_AMERICAN_PRICE


def is_no_push_market(market_token: str | None, line: float | None) -> bool:
    token = (market_token or "").upper()
    if token in NO_PUSH_MARKETS:
        return True
    if line is None:
        return True
    try:
        if float(line) % 1 != 0:
            return True
    except (ValueError, TypeError):
        pass
    return False


def get_research_leverage(market_token: str | None, scope: str | None, sport: str) -> str:
    token = (market_token or "").upper()
    scope_l = (scope or "").lower()
    if sport.upper() == "MLB":
        if (
            token == "TOTAL"
            or scope_l in ("first_5_innings", "first_3_innings")
            or "nrfi" in scope_l
        ):
            return "HIGH"
        if token in ("SPREAD", "MONEYLINE", "RUN_LINE", "GAMELINE"):
            return "LOW"
    return "MED"


def format_source_timestamps(ts_dict: dict[str, str | None]) -> str:
    return json.dumps({k: v for k, v in ts_dict.items() if v})


def _slug(text: str | None) -> str:
    if not text:
        return "unknown"
    return "".join(ch if ch.isalnum() else "-" for ch in str(text).lower()).strip("-") or "unknown"


def _fmt_line(line: Any) -> str:
    if line in (None, ""):
        return ""
    try:
        f = float(line)
    except (ValueError, TypeError):
        return str(line)
    # NaN/inf can reach here from an upstream feed (market_validation_flags
    # already flags it non_numeric_line) — int(f) raises ValueError on either,
    # which would crash pack generation for the whole slate over one bad row.
    # Fall back to the raw repr, same as an unparseable string above.
    if not math.isfinite(f):
        return str(line)
    return str(int(f)) if f == int(f) else str(f)


def _fmt_signed_line(line: Any, proposition: Any) -> str:
    """``_fmt_line`` plus an explicit leading '+' for positive signed-margin lines.

    Negative lines already render with '-' via ``_fmt_line``; only the positive
    case is ambiguous (a bare "1.5" reads as a magnitude, not "+1.5"), so that's
    the only case rewritten. Non-spread markets (totals, props) are untouched.
    """
    fl = _fmt_line(line)
    if not fl or str(proposition or "").strip().upper() not in SIGNED_MARGIN_PROPOSITIONS:
        return fl
    val = _to_float(line)
    if val is not None and val > 0 and not fl.startswith(("+", "-")):
        return f"+{fl}"
    return fl


def _matchup_sides(matchup: Any) -> tuple[str, str] | None:
    """Split an ``AWAY @ HOME`` matchup into its original-case tokens."""

    matchup_s = str(matchup or "").strip()
    if " @ " not in matchup_s:
        return None
    away_token, home_token = (part.strip() for part in matchup_s.split(" @ ", 1))
    if not away_token or not home_token:
        return None
    return away_token, home_token


def _home_away(matchup: Any, team: Any) -> str:
    """Resolve whether ``team`` is HOME or AWAY within an ``AWAY @ HOME`` matchup.

    Returns "HOME"/"AWAY" when the team code matches one side, else "" (unknown).
    Comparison is code-level; matchup strings are built from canonical aliases in
    the normalizer, so an exact token match is reliable.
    """
    sides = _matchup_sides(matchup)
    team_s = str(team or "").strip().upper()
    if not sides or not team_s:
        return ""
    away_token, home_token = sides
    if team_s == home_token.upper():
        return "HOME"
    if team_s == away_token.upper():
        return "AWAY"
    return ""


def _opponent_from_matchup(matchup: Any, team: Any) -> str | None:
    sides = _matchup_sides(matchup)
    team_s = str(team or "").strip().upper()
    if not sides or not team_s:
        return None
    away_token, home_token = sides
    if team_s == away_token.upper():
        return home_token
    if team_s == home_token.upper():
        return away_token
    return None


def _align_gameline_team(
    *,
    matchup: Any,
    team: Any,
    opponent: Any,
    headline_side: Any,
    market_type: Any,
    proposition: Any,
) -> tuple[Any, Any]:
    """Align a HOME/AWAY gameline row to the selected matchup side."""

    side = str(headline_side or "").strip().upper()
    proposition_s = str(proposition or "").strip().upper()
    market_type_s = str(market_type or "").strip().upper()
    if side not in {"HOME", "AWAY"} or (
        market_type_s != "GAMELINE" and proposition_s not in _GAMELINE_SIDE_PROPOSITIONS
    ):
        return team, opponent
    sides = _matchup_sides(matchup)
    if not sides:
        return team, opponent
    away_token, home_token = sides
    if side == "HOME":
        return home_token, away_token
    return away_token, home_token


def _selection_side(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("best_side") or "").strip().upper()
    if explicit in {"OVER", "UNDER"}:
        return explicit
    selection = f" {str(row.get('selection') or '').strip().upper()} "
    if " UNDER " in selection:
        return "UNDER"
    if " OVER " in selection:
        return "OVER"
    return ""


def _priced_line_from_ev(ev_records: list[dict[str, Any]], record_id: Any) -> Any:
    """The line an EV alt-line fallback was actually priced at (``current_line``)."""
    if not record_id:
        return None
    for rec in ev_records:
        if rec.get("record_id") == record_id:
            return rec.get("current_line")
    return None


def market_validation_flags(
    sport: str,
    card: dict[str, Any],
    ref: dict[str, Any],
    market_token: Any,
    market_type: Any,
    player_id: Any,
    line: Any,
) -> list[str]:
    """Non-fatal data-quality flags for a candidate's market/line.

    Deterministic checks only (no hard drops): a cross-sport market artifact and
    a clearly-impossible line. Returns flag strings for ``data_quality_flags``.
    """
    flags: list[str] = []
    market_value = _coalesce(
        card.get("proposition"), ref.get("proposition"), card.get("market_raw"), market_token
    )
    foreign = classify_foreign_market(sport, market_value)
    if foreign:
        flags.append(f"cross_sport_market:{foreign}")
    if line not in (None, ""):
        line_val = _to_float(line)
        # ``line_val != line_val`` is an import-free NaN test: a NaN line parses
        # without error but compares False against everything, so it would slip
        # past the ceiling check unflagged.
        if line_val is None or line_val != line_val:
            flags.append("non_numeric_line")
        else:
            is_player_prop = str(market_type or "").upper() == "PLAYER_PROP" or bool(player_id)
            if is_player_prop and abs(line_val) > PLAYER_PROP_LINE_CEILING:
                flags.append("implausible_line")
    return flags


def _canonical_pack_date(target_date: str | None, out_dir: Path) -> str:
    """Return the slate date without leaking a feedback-staging directory name."""

    for candidate in (target_date, out_dir.name):
        text = str(candidate or "").strip()
        if not text:
            continue
        matches = [text, *_SLATE_DATE_RE.findall(text)]
        for match in matches:
            try:
                datetime.strptime(match, "%Y-%m-%d")
            except ValueError:
                continue
            return match
    return str(target_date or out_dir.name)


def build_selection(name: Any, label: Any, side: Any, line: Any, proposition: Any = None) -> str:
    name_s = str(name or "").strip()
    label_s = str(label or "").strip()
    side_s = str(side or "").strip()
    if name_s and label_s:
        ln = label_s.lower()
        nn = name_s.lower()
        if (
            ln.startswith(nn)
            or ln.startswith(nn + " ")
            or ln.startswith(nn + "-")
            or ln.startswith(nn + " -")
        ):
            core = label_s
        else:
            core = f"{name_s} {label_s}".strip()
    else:
        core = " ".join(p for p in (name_s, label_s) if p)
    # Append side only if not already present in core (prevents "Foo OVER OVER 1.5").
    # This keeps display dedup while preserving verbatim spirit for research quotes.
    parts = [core] if core else []
    if side_s:
        if side_s.lower() not in (core or "").lower():
            parts.append(side_s)
    fl = _fmt_signed_line(line, proposition)
    if fl:
        parts.append(fl)
    return " ".join(parts) if parts else (side_s if side_s else "")
