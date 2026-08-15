"""Structured JSON envelope schemas, strict parser, and JSON Schema emitter for the
AI research desk's verdict/finding/reconciliation contract.

Three envelope kinds share an identity/evidence core but assert different things:
verdict envelopes (passes A, D, B) propose BET/PASS/STAND_DOWN with a stake; the
finding envelope (pass C) proposes CONFIRMS/CONTRADICTS/NEUTRAL research findings
with no stake; the reconciliation envelope (pass E) narrows what upstream passes
already validated, citing them by (publication_id, record_id).

This module owns shape validation only -- is the JSON well-formed and does it match
one of the three envelope kinds. It has no pack dependency and performs no
cross-checking against pack truth (unknown markets, tampered lines, stake caps,
etc.); that is outlier_scrapers.verdict_gate's job, which consults
outlier_scrapers.pack_index. See docs/plans/2026-08-12-structured-ai-verdicts.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0"

STREAMS = ("candidates", "game_totals", "team_totals")
VERDICT_VALUES = ("BET", "PASS", "STAND_DOWN")
FINDING_VALUES = ("CONFIRMS", "CONTRADICTS", "NEUTRAL")
EVIDENCE_KINDS = ("pack", "external")
SUBJECT_TYPES = ("player", "team", "event", "market", "environment")
CONTRADICTION_SEVERITIES = ("minor", "material")
ENVELOPE_KINDS = ("verdict", "finding", "reconciliation")

_ARRAY_KEY = {"verdict": "verdicts", "finding": "findings", "reconciliation": "reconciliations"}


class EnvelopeUnparseableError(ValueError):
    """No valid outermost JSON object could be extracted from the raw text."""


class SchemaInvalidError(ValueError):
    """JSON parsed, but does not match the envelope shape for its declared kind."""

    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    claim: str
    kind: str
    subject_type: str
    player_id: str | None = None
    team: str | None = None
    market_id: str | None = None
    outcome_id: str | None = None
    source: str | None = None
    tier: int | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class Contradiction:
    claim: str
    severity: str


@dataclass(frozen=True)
class KillTrigger:
    condition: str
    observable_before_lock: bool


@dataclass(frozen=True)
class Citation:
    pass_: str
    publication_id: str
    record_id: str


@dataclass(frozen=True)
class VerdictRecord:
    market_id: str
    outcome_id: str
    stream: str
    selection: str
    line: str
    price: str
    book: str
    verdict: str
    confidence: float
    recommended_units: float
    evidence: tuple[Evidence, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    kill_triggers: tuple[KillTrigger, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    record_id: str = ""


@dataclass(frozen=True)
class FindingRecord:
    market_id: str
    outcome_id: str
    stream: str
    selection: str
    line: str
    price: str
    verdict: str
    claim: str
    source_name: str
    source_tier: int
    source_timestamp: str
    evidence: tuple[Evidence, ...] = ()
    record_id: str = ""


@dataclass(frozen=True)
class ReconciliationRecord:
    market_id: str
    outcome_id: str
    stream: str
    selection: str
    line: str
    price: str
    book: str
    verdict: str
    recommended_units: float
    narrative: str
    cites: tuple[Citation, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    record_id: str = ""


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictEnvelope:
    schema_version: str
    pass_: str
    pack_date: str
    candidates_sha256: str
    game_totals_sha256: str
    team_totals_sha256: str
    verdicts: tuple[VerdictRecord, ...]
    slate_notes: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FindingEnvelope:
    schema_version: str
    pass_: str
    pack_date: str
    candidates_sha256: str
    game_totals_sha256: str
    team_totals_sha256: str
    findings: tuple[FindingRecord, ...]
    no_sourced_findings: bool = False


@dataclass(frozen=True)
class ReconciliationEnvelope:
    schema_version: str
    pass_: str
    pack_date: str
    candidates_sha256: str
    game_totals_sha256: str
    team_totals_sha256: str
    upstream_publication_ids: dict[str, str | None]
    reconciliations: tuple[ReconciliationRecord, ...]
    slate_notes: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseWarning:
    code: str
    outcome_id: str
    market_id: str
    detail: str
    severity: str = "warn"


@dataclass(frozen=True)
class ParsedEnvelope:
    envelope: VerdictEnvelope | FindingEnvelope | ReconciliationEnvelope
    warnings: tuple[ParseWarning, ...] = ()


# ---------------------------------------------------------------------------
# Defensive JSON extraction
# ---------------------------------------------------------------------------


def _extract_outermost_json(text: str) -> dict[str, Any]:
    """Locate and parse the outermost {...} object in raw text, tolerating
    surrounding prose. Raises EnvelopeUnparseableError if none is found."""
    start = text.find("{")
    if start == -1:
        raise EnvelopeUnparseableError("No JSON object found in model output.")
    end = text.rfind("}")
    if end == -1 or end < start:
        raise EnvelopeUnparseableError("No closing brace found for a JSON object.")
    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise EnvelopeUnparseableError(f"JSON did not parse: {exc}") from exc
    if not isinstance(parsed, dict):
        raise EnvelopeUnparseableError("Top-level JSON value is not an object.")
    return parsed


# ---------------------------------------------------------------------------
# record_id derivation
# ---------------------------------------------------------------------------

_IDENTITY_FIELDS_BY_KIND = {
    "verdict": (
        "market_id", "outcome_id", "stream", "selection", "line", "price",
        "book", "verdict", "recommended_units",
    ),
    "finding": (
        "market_id", "outcome_id", "stream", "selection", "line", "price",
        "verdict", "claim",
    ),
    "reconciliation": (
        "market_id", "outcome_id", "stream", "selection", "line", "price",
        "book", "verdict", "recommended_units", "narrative",
    ),
}


def _content_hash(kind: str, record: dict[str, Any]) -> str:
    identity = {k: record.get(k) for k in _IDENTITY_FIELDS_BY_KIND[kind]}
    canonical = json.dumps(identity, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _collapse_duplicates(
    kind: str, pass_: str, records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[ParseWarning]]:
    """Assign a content-derived record_id to each record, collapsing byte-
    identical (outcome_id, content_hash) groups to a single surviving record.
    Order-independent: driven entirely by content, never array position."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        outcome_id = str(record.get("outcome_id") or "")
        key = (outcome_id, _content_hash(kind, record))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    survivors: list[dict[str, Any]] = []
    warnings: list[ParseWarning] = []
    for outcome_id, content_hash in order:
        members = groups[(outcome_id, content_hash)]
        survivor = dict(members[0])
        survivor["record_id"] = f"{pass_}:{outcome_id}:{content_hash}"
        survivors.append(survivor)
        if len(members) > 1:
            warnings.append(
                ParseWarning(
                    code="duplicate_record_collapsed",
                    outcome_id=outcome_id,
                    market_id=str(survivor.get("market_id") or ""),
                    detail=f"{len(members)} identical records collapsed to one.",
                )
            )
    return survivors, warnings


# ---------------------------------------------------------------------------
# Shape validation + construction
# ---------------------------------------------------------------------------


def _err(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _require_str(obj: dict, key: str, path: str, errors: list[str]) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or not val:
        _err(errors, path, f"missing or non-string field '{key}'")
        return ""
    return val


def _require_number(obj: dict, key: str, path: str, errors: list[str]) -> float:
    val = obj.get(key)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        _err(errors, path, f"field '{key}' must be a number, got {val!r}")
        return 0.0
    return float(val)


def _require_int(obj: dict, key: str, path: str, errors: list[str]) -> int:
    val = obj.get(key)
    if isinstance(val, bool) or not isinstance(val, int):
        _err(errors, path, f"field '{key}' must be an integer, got {val!r}")
        return 0
    return int(val)


def _require_bool(obj: dict, key: str, path: str, errors: list[str]) -> bool:
    val = obj.get(key)
    if not isinstance(val, bool):
        _err(errors, path, f"field '{key}' must be a boolean, got {val!r}")
        return False
    return val


def _require_enum(obj: dict, key: str, values: tuple[str, ...], path: str, errors: list[str]) -> str:
    val = obj.get(key)
    if val not in values:
        _err(errors, path, f"field '{key}' must be one of {values}, got {val!r}")
        return values[0]
    return val


def _require_list(obj: dict, key: str, path: str, errors: list[str]) -> list:
    val = obj.get(key, [])
    if not isinstance(val, list):
        _err(errors, path, f"field '{key}' must be a list, got {type(val).__name__}")
        return []
    return val


def _parse_evidence_list(raw: list, path: str, errors: list[str]) -> tuple[Evidence, ...]:
    items = []
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        if not isinstance(item, dict):
            _err(errors, item_path, "evidence item must be an object")
            continue
        claim = _require_str(item, "claim", item_path, errors)
        kind = _require_enum(item, "kind", EVIDENCE_KINDS, item_path, errors)
        subject_type = _require_enum(item, "subject_type", SUBJECT_TYPES, item_path, errors)
        tier = item.get("tier")
        if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int)):
            _err(errors, item_path, "field 'tier' must be an integer or null")
            tier = None
        items.append(
            Evidence(
                claim=claim,
                kind=kind,
                subject_type=subject_type,
                player_id=item.get("player_id"),
                team=item.get("team"),
                market_id=item.get("market_id"),
                outcome_id=item.get("outcome_id"),
                source=item.get("source"),
                tier=tier,
                timestamp=item.get("timestamp"),
            )
        )
    return tuple(items)


def _parse_contradictions(raw: list, path: str, errors: list[str]) -> tuple[Contradiction, ...]:
    items = []
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        if not isinstance(item, dict):
            _err(errors, item_path, "contradiction item must be an object")
            continue
        claim = _require_str(item, "claim", item_path, errors)
        severity = _require_enum(item, "severity", CONTRADICTION_SEVERITIES, item_path, errors)
        items.append(Contradiction(claim=claim, severity=severity))
    return tuple(items)


def _parse_kill_triggers(raw: list, path: str, errors: list[str]) -> tuple[KillTrigger, ...]:
    items = []
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        if not isinstance(item, dict):
            _err(errors, item_path, "kill_trigger item must be an object")
            continue
        condition = _require_str(item, "condition", item_path, errors)
        observable = _require_bool(item, "observable_before_lock", item_path, errors)
        items.append(KillTrigger(condition=condition, observable_before_lock=observable))
    return tuple(items)


def _parse_string_list(raw: list, path: str, errors: list[str]) -> tuple[str, ...]:
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            _err(errors, f"{path}[{i}]", "must be a string")
            continue
        out.append(item)
    return tuple(out)


def _parse_citations(raw: list, path: str, errors: list[str]) -> tuple[Citation, ...]:
    items = []
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        if not isinstance(item, dict):
            _err(errors, item_path, "cites item must be an object")
            continue
        pass_ = _require_str(item, "pass", item_path, errors)
        publication_id = _require_str(item, "publication_id", item_path, errors)
        record_id = _require_str(item, "record_id", item_path, errors)
        items.append(Citation(pass_=pass_, publication_id=publication_id, record_id=record_id))
    return tuple(items)


def _validate_record_shape(kind: str, record: dict, path: str, errors: list[str]) -> None:
    if not isinstance(record, dict):
        _err(errors, path, "record must be an object")
        return
    _require_str(record, "market_id", path, errors)
    _require_str(record, "outcome_id", path, errors)
    _require_enum(record, "stream", STREAMS, path, errors)
    _require_str(record, "selection", path, errors)
    _require_str(record, "line", path, errors)
    _require_str(record, "price", path, errors)
    if kind in ("verdict", "reconciliation"):
        _require_str(record, "book", path, errors)
        _require_enum(record, "verdict", VERDICT_VALUES, path, errors)
        _require_number(record, "recommended_units", path, errors)
    else:
        _require_enum(record, "verdict", FINDING_VALUES, path, errors)

    if kind == "verdict":
        _require_number(record, "confidence", path, errors)
        _parse_evidence_list(_require_list(record, "evidence", path, errors), f"{path}.evidence", errors)
        _parse_contradictions(
            _require_list(record, "contradictions", path, errors), f"{path}.contradictions", errors
        )
        _parse_kill_triggers(
            _require_list(record, "kill_triggers", path, errors), f"{path}.kill_triggers", errors
        )
        _parse_string_list(
            _require_list(record, "rejection_reasons", path, errors), f"{path}.rejection_reasons", errors
        )
    elif kind == "finding":
        _require_str(record, "claim", path, errors)
        _require_str(record, "source_name", path, errors)
        _require_int(record, "source_tier", path, errors)
        _require_str(record, "source_timestamp", path, errors)
        _parse_evidence_list(_require_list(record, "evidence", path, errors), f"{path}.evidence", errors)
    else:  # reconciliation
        _require_str(record, "narrative", path, errors)
        _parse_citations(_require_list(record, "cites", path, errors), f"{path}.cites", errors)
        _parse_string_list(
            _require_list(record, "rejection_reasons", path, errors), f"{path}.rejection_reasons", errors
        )


def _build_record(kind: str, record: dict) -> Any:
    if kind == "verdict":
        return VerdictRecord(
            market_id=record["market_id"],
            outcome_id=record["outcome_id"],
            stream=record["stream"],
            selection=record["selection"],
            line=record["line"],
            price=record["price"],
            book=record["book"],
            verdict=record["verdict"],
            confidence=float(record["confidence"]),
            recommended_units=float(record["recommended_units"]),
            evidence=_parse_evidence_list(record.get("evidence", []), "", []),
            contradictions=_parse_contradictions(record.get("contradictions", []), "", []),
            kill_triggers=_parse_kill_triggers(record.get("kill_triggers", []), "", []),
            rejection_reasons=_parse_string_list(record.get("rejection_reasons", []), "", []),
            record_id=record.get("record_id", ""),
        )
    if kind == "finding":
        return FindingRecord(
            market_id=record["market_id"],
            outcome_id=record["outcome_id"],
            stream=record["stream"],
            selection=record["selection"],
            line=record["line"],
            price=record["price"],
            verdict=record["verdict"],
            claim=record["claim"],
            source_name=record["source_name"],
            source_tier=int(record["source_tier"]),
            source_timestamp=record["source_timestamp"],
            evidence=_parse_evidence_list(record.get("evidence", []), "", []),
            record_id=record.get("record_id", ""),
        )
    return ReconciliationRecord(
        market_id=record["market_id"],
        outcome_id=record["outcome_id"],
        stream=record["stream"],
        selection=record["selection"],
        line=record["line"],
        price=record["price"],
        book=record["book"],
        verdict=record["verdict"],
        recommended_units=float(record["recommended_units"]),
        narrative=record["narrative"],
        cites=_parse_citations(record.get("cites", []), "", []),
        rejection_reasons=_parse_string_list(record.get("rejection_reasons", []), "", []),
        record_id=record.get("record_id", ""),
    )


def _build_envelope(kind: str, data: dict, records: list[Any]) -> Any:
    common = dict(
        schema_version=data["schema_version"],
        pass_=data["pass"],
        pack_date=data["pack_date"],
        candidates_sha256=data["candidates_sha256"],
        game_totals_sha256=data["game_totals_sha256"],
        team_totals_sha256=data["team_totals_sha256"],
    )
    if kind == "verdict":
        return VerdictEnvelope(
            **common,
            verdicts=tuple(records),
            slate_notes=_parse_string_list(data.get("slate_notes", []), "", []),
            needs=_parse_string_list(data.get("needs", []), "", []),
        )
    if kind == "finding":
        return FindingEnvelope(
            **common,
            findings=tuple(records),
            no_sourced_findings=bool(data.get("no_sourced_findings", False)),
        )
    return ReconciliationEnvelope(
        **common,
        upstream_publication_ids=dict(data.get("upstream_publication_ids") or {}),
        reconciliations=tuple(records),
        slate_notes=_parse_string_list(data.get("slate_notes", []), "", []),
        needs=_parse_string_list(data.get("needs", []), "", []),
    )


def parse_envelope(raw_text: str, kind: str) -> ParsedEnvelope:
    """Parse raw model output into a typed envelope of the given kind.

    Raises EnvelopeUnparseableError if no valid JSON object could be extracted
    from the text at all, or SchemaInvalidError (carrying every collected
    shape error, not just the first) if the JSON parses but does not match
    the envelope shape for `kind`. Never validates against pack truth -- see
    module docstring.
    """
    if kind not in ENVELOPE_KINDS:
        raise ValueError(f"Unknown envelope kind: {kind!r}")

    data = _extract_outermost_json(raw_text)

    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        _err(errors, "$", f"schema_version must be {SCHEMA_VERSION!r}, got {data.get('schema_version')!r}")
    for key in (
        "pass", "pack_date", "candidates_sha256", "game_totals_sha256", "team_totals_sha256",
    ):
        if not isinstance(data.get(key), str) or not data.get(key):
            _err(errors, "$", f"missing or non-string top-level field '{key}'")

    array_key = _ARRAY_KEY[kind]
    raw_records = data.get(array_key)
    if not isinstance(raw_records, list):
        _err(errors, "$", f"missing or non-list field '{array_key}'")
        raw_records = []

    if kind == "reconciliation":
        upstream = data.get("upstream_publication_ids")
        if not isinstance(upstream, dict):
            _err(errors, "$", "missing or non-object field 'upstream_publication_ids'")

    for i, record in enumerate(raw_records):
        _validate_record_shape(kind, record, f"{array_key}[{i}]", errors)

    if errors:
        raise SchemaInvalidError(f"Envelope failed schema validation for kind={kind!r}", errors)

    pass_ = data["pass"]
    collapsed, warnings = _collapse_duplicates(kind, pass_, raw_records)
    records = [_build_record(kind, r) for r in collapsed]
    envelope = _build_envelope(kind, data, records)
    return ParsedEnvelope(envelope=envelope, warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# JSON Schema emitter (OpenAI strict-mode compatible: additionalProperties
# false and every property required throughout, optional semantics modelled
# as nullable rather than omitted)
# ---------------------------------------------------------------------------


def _obj(properties: dict, *, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties.keys()),
        "additionalProperties": False,
    }


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _nullable(schema: dict) -> dict:
    base_type = schema.get("type")
    if isinstance(base_type, list):
        types = base_type
    else:
        types = [base_type]
    return {**schema, "type": [*types, "null"]}


def _str_schema() -> dict:
    return {"type": "string"}


def _num_schema() -> dict:
    return {"type": "number"}


def _int_schema() -> dict:
    return {"type": "integer"}


def _bool_schema() -> dict:
    return {"type": "boolean"}


def _enum_schema(values: tuple[str, ...]) -> dict:
    return {"type": "string", "enum": list(values)}


def _evidence_schema() -> dict:
    return _obj(
        {
            "claim": _str_schema(),
            "kind": _enum_schema(EVIDENCE_KINDS),
            "subject_type": _enum_schema(SUBJECT_TYPES),
            "player_id": _nullable(_str_schema()),
            "team": _nullable(_str_schema()),
            "market_id": _nullable(_str_schema()),
            "outcome_id": _nullable(_str_schema()),
            "source": _nullable(_str_schema()),
            "tier": _nullable(_int_schema()),
            "timestamp": _nullable(_str_schema()),
        }
    )


def _identity_fields_schema(*, with_book: bool) -> dict:
    fields = {
        "market_id": _str_schema(),
        "outcome_id": _str_schema(),
        "stream": _enum_schema(STREAMS),
        "selection": _str_schema(),
        "line": _str_schema(),
        "price": _str_schema(),
    }
    if with_book:
        fields["book"] = _str_schema()
    return fields


def _verdict_record_schema() -> dict:
    fields = _identity_fields_schema(with_book=True)
    fields.update(
        {
            "verdict": _enum_schema(VERDICT_VALUES),
            "confidence": _num_schema(),
            "recommended_units": _num_schema(),
            "evidence": _arr(_evidence_schema()),
            "contradictions": _arr(
                _obj({"claim": _str_schema(), "severity": _enum_schema(CONTRADICTION_SEVERITIES)})
            ),
            "kill_triggers": _arr(
                _obj({"condition": _str_schema(), "observable_before_lock": _bool_schema()})
            ),
            "rejection_reasons": _arr(_str_schema()),
        }
    )
    return _obj(fields)


def _finding_record_schema() -> dict:
    fields = _identity_fields_schema(with_book=False)
    fields.update(
        {
            "verdict": _enum_schema(FINDING_VALUES),
            "claim": _str_schema(),
            "source_name": _str_schema(),
            "source_tier": _int_schema(),
            "source_timestamp": _str_schema(),
            "evidence": _arr(_evidence_schema()),
        }
    )
    return _obj(fields)


def _reconciliation_record_schema() -> dict:
    fields = _identity_fields_schema(with_book=True)
    fields.update(
        {
            "verdict": _enum_schema(VERDICT_VALUES),
            "recommended_units": _num_schema(),
            "narrative": _str_schema(),
            "cites": _arr(
                _obj(
                    {
                        "pass": _str_schema(),
                        "publication_id": _str_schema(),
                        "record_id": _str_schema(),
                    }
                )
            ),
            "rejection_reasons": _arr(_str_schema()),
        }
    )
    return _obj(fields)


def _envelope_common_fields() -> dict:
    return {
        "schema_version": _str_schema(),
        "pass": _str_schema(),
        "pack_date": _str_schema(),
        "candidates_sha256": _str_schema(),
        "game_totals_sha256": _str_schema(),
        "team_totals_sha256": _str_schema(),
    }


def _verdict_envelope_schema() -> dict:
    fields = _envelope_common_fields()
    fields.update(
        {
            "verdicts": _arr(_verdict_record_schema()),
            "slate_notes": _arr(_str_schema()),
            "needs": _arr(_str_schema()),
        }
    )
    return _obj(fields)


def _finding_envelope_schema() -> dict:
    fields = _envelope_common_fields()
    fields.update(
        {
            "findings": _arr(_finding_record_schema()),
            "no_sourced_findings": _bool_schema(),
        }
    )
    return _obj(fields)


def _upstream_publication_ids_schema() -> dict:
    # A fixed key set (A/D/B required upstream, C optional), not an open
    # dictionary: OpenAI strict mode requires additionalProperties: False
    # throughout, which is incompatible with dynamic-keyed maps. C is
    # nullable since E is not required to have cited a C finding.
    return _obj(
        {
            "A": _str_schema(),
            "D": _str_schema(),
            "B": _str_schema(),
            "C": _nullable(_str_schema()),
        }
    )


def _reconciliation_envelope_schema() -> dict:
    fields = _envelope_common_fields()
    fields.update(
        {
            "upstream_publication_ids": _upstream_publication_ids_schema(),
            "reconciliations": _arr(_reconciliation_record_schema()),
            "slate_notes": _arr(_str_schema()),
            "needs": _arr(_str_schema()),
        }
    )
    return _obj(fields)


_ENVELOPE_SCHEMA_BUILDERS = {
    "verdict": _verdict_envelope_schema,
    "finding": _finding_envelope_schema,
    "reconciliation": _reconciliation_envelope_schema,
}


def json_schema_for(kind: str) -> dict:
    """Return the JSON Schema dict for the given envelope kind, suitable for
    a provider's native structured-output config (e.g. OpenAI's
    `text.format.json_schema`)."""
    builder = _ENVELOPE_SCHEMA_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown envelope kind: {kind!r}")
    return builder()
