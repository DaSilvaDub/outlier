"""Deterministic verdict validator.

Pure function of (envelope, index, now). No I/O, no clock reads, no network.
See docs/plans/2026-08-12-structured-ai-verdicts.md 'The deterministic gates'
and 'Repair loop and failure policy'.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from outlier_scrapers import pack, verdicts
from outlier_scrapers.normalizer import (
    ALLOWED_MLB_PLAYER_PROPS,
    ALLOWED_MLB_TEAM_PROPS,
    _market_token,
)
from outlier_scrapers.pack_index import PackIndex
from outlier_scrapers.utils import _parse_start
from outlier_scrapers.verdicts import (
    EnvelopeUnparseableError,
    FindingEnvelope,
    FindingRecord,
    ParsedEnvelope,
    ReconciliationEnvelope,
    ReconciliationRecord,
    SchemaInvalidError,
    VerdictEnvelope,
    VerdictRecord,
)

IDENTITY_TAMPER_CODES = frozenset(
    {
        "unknown_market",
        "market_id_mismatch",
        "stream_mismatch",
        "line_tampered",
        "price_tampered",
        "selection_tampered",
        "book_tampered",
        "unknown_player",
        "unbound_player_claim",
        "priced_line_unreconciled",
    }
)
ENVELOPE_CODES = frozenset(
    {
        "envelope_unparseable",
        "schema_invalid",
        "pack_mismatch",
    }
)
WARN_CODES = frozenset({"unbound_name_in_prose"})

DEFAULT_VARIANCE_MARKETS = (
    "3PM",
    "THREES",
    "THREE_POINTERS_MADE",
    "HA",
    "HITS_ALLOWED",
    "TO",
    "TURNOVERS",
)
DEFAULT_DESK_PROHIBITED = (
    {"market": "HR", "scope": "any"},
    {"market": "HOME_RUNS", "scope": "any"},
    {"market": "WALKS_ALLOWED", "scope": "any"},
    {"market": "HRR", "scope": "any"},
    {"market": "BB", "scope": "PLAYER_PROP"},
)

_INJURY_RE = re.compile(
    r"\b(injur(?:y|ed)|scratch(?:ed)?|lineup|availability|questionable|"
    r"probable|doubtful|out\b|il\b|inactive)\b",
    re.IGNORECASE,
)
_SIDE_OVER_RE = re.compile(r"\bover\b", re.IGNORECASE)
_SIDE_UNDER_RE = re.compile(r"\bunder\b", re.IGNORECASE)

_VERDICT_POLICY_KEYS = {
    "mode",
    "repair_attempts",
    "reject_fail_ratio",
    "prohibited_variance_markets",
    "desk_prohibited_markets",
    "enforce_mlb_whitelist",
    "external_evidence_max_age_h",
}


@dataclass(frozen=True)
class Violation:
    code: str
    outcome_id: str
    market_id: str
    detail: str
    severity: str  # "reject" | "warn"


@dataclass(frozen=True)
class VerdictPolicy:
    mode: str = "shadow"
    repair_attempts: int = 1
    reject_fail_ratio: float = 0.5
    prohibited_variance_markets: tuple[str, ...] = DEFAULT_VARIANCE_MARKETS
    desk_prohibited_markets: tuple[dict[str, str], ...] = DEFAULT_DESK_PROHIBITED
    enforce_mlb_whitelist: bool = True
    external_evidence_max_age_h: float = 24.0


@dataclass(frozen=True)
class UpstreamPublication:
    pass_: str
    publication_id: str
    record_ids: frozenset[str]
    outcome_ids: frozenset[str] = field(default_factory=frozenset)
    injury_supported_record_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GateResult:
    violations: tuple[Violation, ...]
    pass_fails: bool
    reject_fail_ratio: float | None
    fail_reasons: tuple[str, ...] = ()


def violation_class(code: str) -> str:
    if code in ENVELOPE_CODES:
        return "envelope"
    if code in IDENTITY_TAMPER_CODES:
        return "identity_tamper"
    if code in WARN_CODES:
        return "warn"
    return "judgement"


def load_verdict_policy(path: Path | str | None = None) -> VerdictPolicy:
    if path is None:
        return VerdictPolicy()
    path = Path(path)
    if not path.exists():
        return VerdictPolicy()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    extra = set(data) - _VERDICT_POLICY_KEYS
    if extra:
        raise ValueError(f"Unknown keys in verdict policy: {extra}")
    return VerdictPolicy(
        mode=data.get("mode", "shadow"),
        repair_attempts=int(data.get("repair_attempts", 1)),
        reject_fail_ratio=float(data.get("reject_fail_ratio", 0.5)),
        prohibited_variance_markets=tuple(
            data.get("prohibited_variance_markets", DEFAULT_VARIANCE_MARKETS)
        ),
        desk_prohibited_markets=tuple(
            data.get("desk_prohibited_markets", DEFAULT_DESK_PROHIBITED)
        ),
        enforce_mlb_whitelist=bool(data.get("enforce_mlb_whitelist", True)),
        external_evidence_max_age_h=float(data.get("external_evidence_max_age_h", 24.0)),
    )


def validate_envelope(
    envelope: ParsedEnvelope | VerdictEnvelope | FindingEnvelope | ReconciliationEnvelope | str,
    index: PackIndex,
    now: datetime,
    *,
    kind: str | None = None,
    policy: VerdictPolicy | None = None,
    current_publications: Mapping[str, UpstreamPublication] | None = None,
) -> GateResult:
    """Validate a parsed (or raw) envelope against the pack index.

    `now` is the only clock: callers pass it in. This function never reads
    the system clock and never touches the filesystem.
    """
    policy = policy or VerdictPolicy()
    parsed_or_fail = _coerce_envelope(envelope, kind)
    if isinstance(parsed_or_fail, GateResult):
        return parsed_or_fail
    env = parsed_or_fail

    if (
        env.candidates_sha256 != index.candidates_sha256
        or env.game_totals_sha256 != index.game_totals_sha256
        or env.team_totals_sha256 != index.team_totals_sha256
    ):
        return _envelope_result(
            Violation(
                code="pack_mismatch",
                outcome_id="",
                market_id="",
                detail="Envelope pack hashes do not match the authoritative index.",
                severity="reject",
            )
        )

    records = _records_of(env)
    violations: list[Violation] = []
    for record in records:
        violations.extend(
            _validate_record(
                record,
                index,
                now,
                policy,
                current_publications or {},
            )
        )

    return _classify_result(records, tuple(violations), policy)


def _coerce_envelope(
    envelope: ParsedEnvelope | VerdictEnvelope | FindingEnvelope | ReconciliationEnvelope | str,
    kind: str | None,
) -> VerdictEnvelope | FindingEnvelope | ReconciliationEnvelope | GateResult:
    if isinstance(envelope, ParsedEnvelope):
        return envelope.envelope
    if isinstance(envelope, (VerdictEnvelope, FindingEnvelope, ReconciliationEnvelope)):
        return envelope
    if not isinstance(envelope, str):
        raise TypeError(f"Unsupported envelope type: {type(envelope)!r}")
    if kind is None:
        raise ValueError("kind is required when validating raw text")
    try:
        return verdicts.parse_envelope(envelope, kind).envelope
    except EnvelopeUnparseableError as exc:
        return _envelope_result(
            Violation(
                code="envelope_unparseable",
                outcome_id="",
                market_id="",
                detail=str(exc),
                severity="reject",
            )
        )
    except SchemaInvalidError as exc:
        return _envelope_result(
            Violation(
                code="schema_invalid",
                outcome_id="",
                market_id="",
                detail=str(exc),
                severity="reject",
            )
        )


def _envelope_result(violation: Violation) -> GateResult:
    return GateResult(
        violations=(violation,),
        pass_fails=True,
        reject_fail_ratio=None,
        fail_reasons=(violation.code,),
    )


def _records_of(
    env: VerdictEnvelope | FindingEnvelope | ReconciliationEnvelope,
) -> tuple[VerdictRecord | FindingRecord | ReconciliationRecord, ...]:
    if isinstance(env, VerdictEnvelope):
        return env.verdicts
    if isinstance(env, FindingEnvelope):
        return env.findings
    return env.reconciliations


def _is_attempted_bet(record: Any) -> bool:
    return getattr(record, "verdict", None) == "BET"


def _is_stake_exempt(record: Any) -> bool:
    if isinstance(record, FindingRecord):
        return True
    return getattr(record, "verdict", None) in {"PASS", "STAND_DOWN"}


def _validate_record(
    record: VerdictRecord | FindingRecord | ReconciliationRecord,
    index: PackIndex,
    now: datetime,
    policy: VerdictPolicy,
    publications: Mapping[str, UpstreamPublication],
) -> list[Violation]:
    found: list[Violation] = []
    outcome_id = record.outcome_id
    market_id = record.market_id

    def add(code: str, detail: str, severity: str = "reject") -> None:
        found.append(
            Violation(
                code=code,
                outcome_id=outcome_id,
                market_id=market_id,
                detail=detail,
                severity=severity,
            )
        )

    row = index.rows.get(outcome_id)
    dropped = index.dropped.get(outcome_id)

    if row is None:
        if not outcome_id or any(u.totals_id == market_id for u in index.unindexed_totals):
            add("unknown_market", f"outcome_id {outcome_id!r} is not in the pack index.")
            return found
        if dropped is None:
            add("unknown_market", f"outcome_id {outcome_id!r} is not in the pack index.")
            return found
        # Dropped rows are still identifiable for tamper checks, then lock.
        row = dropped

    if record.stream != row.stream:
        add(
            "stream_mismatch",
            f"stream {record.stream!r} does not match indexed stream {row.stream!r}.",
        )

    if not _market_id_matches(record.market_id, row):
        add(
            "market_id_mismatch",
            f"market_id {record.market_id!r} matches neither CSV market_id "
            f"{row.market_id!r} nor the totals compatibility alias.",
        )

    _check_tamper(record, row, add)
    _check_players(record, index, add)

    if isinstance(record, ReconciliationRecord):
        _check_synthesis(record, publications, add)
    _check_injury(record, index, now, policy, publications, add)

    if _is_stake_exempt(record):
        return found

    _check_lock(outcome_id, row, index, now, add)
    _check_integrity(row, add)
    _check_markets(record, row, policy, add)
    _check_stakes(record, row, index, add)
    return found


def _market_id_matches(emitted: str, row: Any) -> bool:
    if emitted == row.market_id:
        return True
    totals_id = str(row.data.get("totals_id") or "")
    if row.stream in {"game_totals", "team_totals"} and totals_id and emitted == totals_id:
        return True
    return False


def _numeric_equal(left: str, right: str) -> bool:
    try:
        return math.isclose(float(str(left).replace("+", "")), float(str(right).replace("+", "")), abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _priced_line_required(row_data: Mapping[str, str]) -> str | None:
    priced = str(row_data.get("priced_line") or "").strip()
    if priced:
        return priced
    flags = str(row_data.get("data_quality_flags") or "")
    for part in flags.split(","):
        part = part.strip()
        if part.startswith("ev_line_fallback:priced_at="):
            return part.split("=", 1)[1]
    return None


def _check_tamper(record: Any, row: Any, add) -> None:
    data = row.data
    required_priced = _priced_line_required(data)
    if required_priced is not None:
        if not _numeric_equal(record.line, required_priced):
            add(
                "priced_line_unreconciled",
                f"priced_line is {required_priced!r}; verdict line is {record.line!r}.",
            )
        return
    if not _numeric_equal(record.line, str(data.get("line") or "")):
        add("line_tampered", f"line {record.line!r} != pack line {data.get('line')!r}.")
    if not _numeric_equal(record.price, str(data.get("price") or "")):
        add("price_tampered", f"price {record.price!r} != pack price {data.get('price')!r}.")
    if str(record.selection).strip() != str(data.get("selection") or "").strip():
        add(
            "selection_tampered",
            f"selection {record.selection!r} != pack selection {data.get('selection')!r}.",
        )
    book = getattr(record, "book", None)
    if book is not None and str(book).strip() != str(data.get("book") or "").strip():
        add("book_tampered", f"book {book!r} != pack book {data.get('book')!r}.")


def _evidence_items(record: Any) -> tuple[verdicts.Evidence, ...]:
    return getattr(record, "evidence", ()) or ()


def _player_display_name(raw: str) -> str:
    """PackIndex currently stores selection text as PlayerInfo.name."""
    cleaned = re.split(r"\b(?:over|under)\b", raw or "", flags=re.IGNORECASE)[0].strip(" -:")
    return cleaned or (raw or "")


def _prose_blobs(record: Any) -> list[str]:
    blobs: list[str] = []
    for item in _evidence_items(record):
        blobs.append(item.claim)
    for attr in ("claim", "narrative"):
        value = getattr(record, attr, None)
        if isinstance(value, str) and value:
            blobs.append(value)
    return blobs


def _check_players(record: Any, index: PackIndex, add) -> None:
    bound_ids: set[str] = set()
    for item in _evidence_items(record):
        if item.subject_type != "player":
            continue
        if not item.player_id:
            add("unbound_player_claim", "player evidence item is missing player_id.")
            continue
        if item.player_id not in index.players:
            add("unknown_player", f"player_id {item.player_id!r} is not in the pack roster.")
            continue
        bound_ids.add(item.player_id)

    roster_names = [
        (pid, _player_display_name(info.name)) for pid, info in index.players.items() if info.name
    ]
    for blob in _prose_blobs(record):
        for pid, name in roster_names:
            if name and len(name) >= 3 and name in blob and pid not in bound_ids:
                add(
                    "unbound_name_in_prose",
                    f"prose names roster player {name!r} without bound player_id.",
                    severity="warn",
                )
                break


def _check_synthesis(
    record: ReconciliationRecord,
    publications: Mapping[str, UpstreamPublication],
    add,
) -> None:
    cites = record.cites
    stale = False
    for cite in cites:
        pub = publications.get(cite.pass_)
        if pub is None:
            continue
        if cite.publication_id != pub.publication_id:
            add(
                "stale_citation",
                f"cites {cite.pass_} publication {cite.publication_id!r} "
                f"but current is {pub.publication_id!r}.",
            )
            stale = True
        elif cite.record_id not in pub.record_ids:
            add(
                "unsourced_synthesis",
                f"cites unknown record_id {cite.record_id!r} on pass {cite.pass_}.",
            )
    if stale:
        return
    verdict_backing = False
    for cite in cites:
        if cite.pass_ in {"A", "D", "B"}:
            pub = publications.get(cite.pass_)
            if pub and record.outcome_id in pub.outcome_ids:
                verdict_backing = True
                break
    if record.verdict == "BET" and not verdict_backing:
        add(
            "unsourced_synthesis",
            "E BET is not sourced by a current A/D/B verdict on this outcome_id.",
        )


def _is_injury_text(text: str) -> bool:
    return bool(_INJURY_RE.search(text or ""))


def _timestamp_in_window(raw: str | None, now: datetime, max_age_h: float) -> bool:
    if not raw:
        return False
    parsed = _parse_start(raw)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
    if parsed.tzinfo is None:
        if now.tzinfo is not None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        else:
            return False
    age = now - parsed
    return timedelta(0) <= age <= timedelta(hours=max_age_h)


def _injury_supported_by_pack(player_id: str | None, index: PackIndex) -> bool:
    if not player_id:
        return False
    info = index.players.get(player_id)
    if info is None:
        return False
    flags = index.injuries.get(info.event_id) or ""
    if flags:
        return True
    for row in (*index.rows.values(), *index.dropped.values()):
        if str(row.data.get("player_id") or "") == player_id and str(
            row.data.get("injury_flags") or ""
        ):
            return True
    return False


def _injury_supported_grounded(item: verdicts.Evidence, now: datetime, policy: VerdictPolicy) -> bool:
    return (
        bool(item.source)
        and item.tier is not None
        and _timestamp_in_window(item.timestamp, now, policy.external_evidence_max_age_h)
    )


def _check_injury(
    record: Any,
    index: PackIndex,
    now: datetime,
    policy: VerdictPolicy,
    publications: Mapping[str, UpstreamPublication],
    add,
) -> None:
    evidence_claims = [item for item in _evidence_items(record) if _is_injury_text(item.claim)]
    narrative_injury = False
    if isinstance(record, ReconciliationRecord) and _is_injury_text(record.narrative):
        narrative_injury = True
    if not evidence_claims and not narrative_injury:
        return

    if isinstance(record, ReconciliationRecord):
        cited_ok = False
        for cite in record.cites:
            if cite.pass_ not in {"B", "C"}:
                continue
            pub = publications.get(cite.pass_)
            if pub and cite.record_id in pub.injury_supported_record_ids:
                if not pub.publication_id or cite.publication_id == pub.publication_id:
                    cited_ok = True
                    break
        if not cited_ok:
            add(
                "unsupported_injury_claim",
                "E injury claim does not cite a validated B/C finding.",
            )
        return

    for item in evidence_claims:
        pack_ok = _injury_supported_by_pack(item.player_id, index)
        grounded_ok = _injury_supported_grounded(item, now, policy)
        if item.player_id and (pack_ok or grounded_ok):
            continue
        add("unsupported_injury_claim", "injury claim is not bound to pack flags or grounded source.")


def _check_lock(outcome_id: str, row: Any, index: PackIndex, now: datetime, add) -> None:
    if outcome_id in index.dropped:
        add("locked_market", f"outcome_id {outcome_id!r} is in the lock drop set.")
        return
    start_raw = str(row.data.get("_event_starts_at") or "")
    start = _parse_start(start_raw)
    if start is None or start <= now:
        add("locked_market", f"event start {start_raw!r} is unparseable or already locked at validation time.")


def _check_integrity(row: Any, add) -> None:
    flags_raw = str(row.data.get("data_quality_flags") or "")
    flags = {part.strip() for part in flags_raw.split(",") if part.strip()}
    # ev_line_fallback:priced_at=… is a priced-line signal, not a standalone DQ here.
    exact = {f for f in flags if not f.startswith("ev_line_fallback:")}
    if not pack.DISQUALIFYING_DQ_FLAGS.isdisjoint(exact) or any(
        f.startswith(pack.CROSS_SPORT_DQ_PREFIX) for f in flags
    ):
        add("integrity_flag", f"row carries disqualifying data_quality_flags {sorted(flags)}.")
        return
    if str(row.data.get("actionable") or "").lower() != "true":
        add("integrity_flag", "row actionable is not true.")
        return
    if str(row.data.get("board") or "") == "A_FLAGGED":
        add("integrity_flag", "row board is A_FLAGGED.")


def _row_market_token(row: Any) -> str:
    return _market_token(row.data.get("market_label") or row.data.get("market") or "")


def _selection_side(selection: str) -> str:
    if _SIDE_OVER_RE.search(selection or ""):
        return "OVER"
    if _SIDE_UNDER_RE.search(selection or ""):
        return "UNDER"
    return ""


def _check_markets(record: Any, row: Any, policy: VerdictPolicy, add) -> None:
    token = _row_market_token(row)
    variance = {_market_token(v) for v in policy.prohibited_variance_markets}
    if token and token in variance:
        add("high_variance_market", f"market {token} is in the high-variance never-recommend set.")
        return

    market_type = str(row.data.get("market_type") or "")
    sport = str(row.data.get("sport") or "").upper()
    for rule in policy.desk_prohibited_markets:
        rule_market = _market_token(rule.get("market", ""))
        scope = str(rule.get("scope") or "any")
        if token != rule_market:
            continue
        if scope == "any" or scope == market_type:
            add(
                "prohibited_market",
                f"market {token} is desk-prohibited at scope {scope}.",
            )
            return

    if policy.enforce_mlb_whitelist and sport == "MLB" and token:
        if market_type == "PLAYER_PROP" and token not in ALLOWED_MLB_PLAYER_PROPS:
            add("prohibited_market", f"MLB player prop {token} is not on the generation whitelist.")
            return
        if market_type == "TEAM_PROP" and token not in ALLOWED_MLB_TEAM_PROPS:
            add("prohibited_market", f"MLB team prop {token} is not on the generation whitelist.")
            return

    if token == "2B" and _selection_side(record.selection) == "OVER":
        add("side_restricted", "Doubles (2B) are UNDER-only.")
        return

    if pack.is_longshot_price(record.price):
        add("longshot_price", f"price {record.price} is at or beyond +{pack.LONGSHOT_AMERICAN_PRICE}.")


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _on_increment(units: float, increment: float) -> bool:
    if increment <= 0:
        return True
    steps = units / increment
    return math.isclose(steps, round(steps), abs_tol=1e-9)


def _check_stakes(record: Any, row: Any, index: PackIndex, add) -> None:
    units = getattr(record, "recommended_units", None)
    if units is None:
        return
    units = float(units)
    if units < 0:
        add("negative_stake", f"recommended_units {units} is negative.")
        return
    increment = index.policy.stake_increment
    if not _on_increment(units, increment):
        add(
            "stake_off_increment",
            f"recommended_units {units} is not on the {increment} increment grid.",
        )
        return

    row_caps = [
        _finite_float(row.data.get("recommended_units_pre_news")),
        _finite_float(row.data.get("max_units")),
    ]
    present = [c for c in row_caps if c is not None]
    if present and units > min(present) + 1e-9:
        add(
            "stake_above_row_cap",
            f"recommended_units {units} exceeds row cap {min(present)}.",
        )
        return
    if units > index.policy.max_wager_units + 1e-9:
        add(
            "stake_above_policy_cap",
            f"recommended_units {units} exceeds policy.max_wager_units "
            f"{index.policy.max_wager_units}.",
        )


def _classify_result(
    records: Sequence[Any],
    violations: tuple[Violation, ...],
    policy: VerdictPolicy,
) -> GateResult:
    fail_reasons: list[str] = []
    if any(violation_class(v.code) == "envelope" for v in violations):
        fail_reasons.extend(
            v.code for v in violations if violation_class(v.code) == "envelope"
        )

    if any(violation_class(v.code) == "identity_tamper" for v in violations):
        fail_reasons.append("identity_tamper")

    attempted = [r for r in records if _is_attempted_bet(r)]
    ratio: float | None = None
    if attempted:
        rejected = 0
        for record in attempted:
            rec_violations = [
                v
                for v in violations
                if v.outcome_id == record.outcome_id and v.severity == "reject"
            ]
            if rec_violations:
                rejected += 1
        ratio = rejected / len(attempted)
        if ratio > policy.reject_fail_ratio + 1e-12:
            fail_reasons.append("reject_fail_ratio")

    return GateResult(
        violations=violations,
        pass_fails=bool(fail_reasons),
        reject_fail_ratio=ratio,
        fail_reasons=tuple(fail_reasons),
    )
