"""Tests for outlier_scrapers.verdicts: envelope schemas, strict parser, JSON Schema emitter.

Covers the three envelope kinds (verdict/finding/reconciliation), content-hash record_id
derivation and duplicate collapsing, and the two parser failure modes
(EnvelopeUnparseableError vs SchemaInvalidError) per
docs/plans/2026-08-12-structured-ai-verdicts.md.
"""

from __future__ import annotations

import copy
import json

import pytest

from outlier_scrapers import verdicts


# ---------------------------------------------------------------------------
# Fixtures: minimal valid envelopes per kind
# ---------------------------------------------------------------------------


def _base_hashes():
    return {
        "candidates_sha256": "a" * 64,
        "game_totals_sha256": "b" * 64,
        "team_totals_sha256": "c" * 64,
    }


def verdict_envelope_dict(**overrides):
    env = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "A",
        "pack_date": "2026-08-12",
        **_base_hashes(),
        "verdicts": [
            {
                "market_id": "mkt_123",
                "outcome_id": "out_123",
                "stream": "candidates",
                "selection": "Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "confidence": 0.72,
                "recommended_units": 1.0,
                "evidence": [],
                "contradictions": [],
                "kill_triggers": [],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    env.update(overrides)
    return env


def finding_envelope_dict(**overrides):
    env = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "C",
        "pack_date": "2026-08-12",
        **_base_hashes(),
        "findings": [
            {
                "market_id": "mkt_123",
                "outcome_id": "out_123",
                "stream": "candidates",
                "selection": "Over 5.5",
                "line": "5.5",
                "price": "-110",
                "verdict": "CONFIRMS",
                "claim": "Starter confirmed healthy.",
                "source_name": "Team beat writer",
                "source_tier": 1,
                "source_timestamp": "2026-08-12T10:00:00Z",
                "evidence": [],
            }
        ],
        "no_sourced_findings": False,
    }
    env.update(overrides)
    return env


def reconciliation_envelope_dict(**overrides):
    env = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": "2026-08-12",
        **_base_hashes(),
        "upstream_publication_ids": {"A": "pub_a", "D": "pub_d", "B": "pub_b"},
        "reconciliations": [
            {
                "market_id": "mkt_123",
                "outcome_id": "out_123",
                "stream": "candidates",
                "selection": "Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "recommended_units": 1.0,
                "narrative": "A and D agree.",
                "cites": [
                    {"pass": "A", "publication_id": "pub_a", "record_id": "x"},
                ],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------


def test_schema_version_constant():
    assert verdicts.SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# Green path: each envelope kind parses into a typed envelope
# ---------------------------------------------------------------------------


def test_parse_verdict_envelope_green_path():
    raw = json.dumps(verdict_envelope_dict())
    parsed = verdicts.parse_envelope(raw, "verdict")
    env = parsed.envelope
    assert isinstance(env, verdicts.VerdictEnvelope)
    assert env.pass_ == "A"
    assert env.candidates_sha256 == "a" * 64
    assert len(env.verdicts) == 1
    record = env.verdicts[0]
    assert record.market_id == "mkt_123"
    assert record.outcome_id == "out_123"
    assert record.verdict == "BET"
    assert record.confidence == 0.72
    assert record.recommended_units == 1.0
    assert parsed.warnings == ()


def test_parse_finding_envelope_green_path():
    raw = json.dumps(finding_envelope_dict())
    parsed = verdicts.parse_envelope(raw, "finding")
    env = parsed.envelope
    assert isinstance(env, verdicts.FindingEnvelope)
    assert env.pass_ == "C"
    assert len(env.findings) == 1
    record = env.findings[0]
    assert record.verdict == "CONFIRMS"
    assert record.claim == "Starter confirmed healthy."
    assert record.source_tier == 1
    assert parsed.warnings == ()


def test_parse_reconciliation_envelope_green_path():
    raw = json.dumps(reconciliation_envelope_dict())
    parsed = verdicts.parse_envelope(raw, "reconciliation")
    env = parsed.envelope
    assert isinstance(env, verdicts.ReconciliationEnvelope)
    assert env.pass_ == "E"
    assert env.upstream_publication_ids == {"A": "pub_a", "D": "pub_d", "B": "pub_b"}
    assert len(env.reconciliations) == 1
    record = env.reconciliations[0]
    assert record.verdict == "BET"
    assert record.narrative == "A and D agree."
    assert len(record.cites) == 1
    assert record.cites[0].pass_ == "A"
    assert record.cites[0].publication_id == "pub_a"


def test_finding_record_has_no_book_field():
    # The finding envelope's own JSON example omits `book` (a finding about a
    # claim has no book/stake concept); FindingRecord must not require it.
    parsed = verdicts.parse_envelope(json.dumps(finding_envelope_dict()), "finding")
    record = parsed.envelope.findings[0]
    assert not hasattr(record, "book")


# ---------------------------------------------------------------------------
# record_id: content-derived, position-independent, full 64-hex digest
# ---------------------------------------------------------------------------


def test_record_id_is_pass_outcome_id_and_full_64_hex_digest():
    parsed = verdicts.parse_envelope(json.dumps(verdict_envelope_dict()), "verdict")
    record = parsed.envelope.verdicts[0]
    prefix = "A:out_123:"
    assert record.record_id.startswith(prefix)
    hash_part = record.record_id[len(prefix):]
    assert len(hash_part) == 64
    assert all(c in "0123456789abcdef" for c in hash_part)


def test_record_id_stable_across_array_reorder():
    # Two records on distinct outcome_ids; parse once in order [X, Y], once
    # reversed [Y, X]. Each logical record's record_id must be identical
    # regardless of its position in the array (simulates a repair-round
    # reorder that never touched either record's content).
    a = finding_envelope_dict()
    a["findings"] = [
        {**finding_envelope_dict()["findings"][0], "outcome_id": "out_X", "claim": "X claim"},
        {**finding_envelope_dict()["findings"][0], "outcome_id": "out_Y", "claim": "Y claim"},
    ]
    b = copy.deepcopy(a)
    b["findings"] = list(reversed(a["findings"]))

    parsed_a = verdicts.parse_envelope(json.dumps(a), "finding")
    parsed_b = verdicts.parse_envelope(json.dumps(b), "finding")

    ids_a = {r.outcome_id: r.record_id for r in parsed_a.envelope.findings}
    ids_b = {r.outcome_id: r.record_id for r in parsed_b.envelope.findings}
    assert ids_a == ids_b
    assert ids_a["out_X"] != ids_a["out_Y"]


def test_record_id_differs_when_any_field_differs():
    env1 = finding_envelope_dict()
    env2 = finding_envelope_dict()
    env2["findings"][0]["claim"] = "A completely different claim."

    r1 = verdicts.parse_envelope(json.dumps(env1), "finding").envelope.findings[0]
    r2 = verdicts.parse_envelope(json.dumps(env2), "finding").envelope.findings[0]
    assert r1.record_id != r2.record_id


# ---------------------------------------------------------------------------
# Duplicate collapsing: byte-identical records merge, distinct ones never do
# ---------------------------------------------------------------------------


def test_two_byte_identical_findings_collapse_with_warning():
    env = finding_envelope_dict()
    env["findings"] = [env["findings"][0], copy.deepcopy(env["findings"][0])]
    parsed = verdicts.parse_envelope(json.dumps(env), "finding")

    assert len(parsed.envelope.findings) == 1
    assert len(parsed.warnings) == 1
    warning = parsed.warnings[0]
    assert warning.code == "duplicate_record_collapsed"
    assert warning.severity == "warn"
    assert warning.outcome_id == "out_123"
    assert "2" in warning.detail


def test_three_byte_identical_findings_collapse_with_count_in_detail():
    env = finding_envelope_dict()
    one = env["findings"][0]
    env["findings"] = [one, copy.deepcopy(one), copy.deepcopy(one)]
    parsed = verdicts.parse_envelope(json.dumps(env), "finding")

    assert len(parsed.envelope.findings) == 1
    assert len(parsed.warnings) == 1
    assert "3" in parsed.warnings[0].detail


def test_duplicate_collapsing_is_order_independent():
    env = finding_envelope_dict()
    one = env["findings"][0]
    distinct = {**one, "claim": "A distinct second finding."}
    env["findings"] = [one, copy.deepcopy(one), distinct]
    reversed_env = copy.deepcopy(env)
    reversed_env["findings"] = list(reversed(env["findings"]))

    parsed = verdicts.parse_envelope(json.dumps(env), "finding")
    parsed_rev = verdicts.parse_envelope(json.dumps(reversed_env), "finding")

    ids = {r.claim: r.record_id for r in parsed.envelope.findings}
    ids_rev = {r.claim: r.record_id for r in parsed_rev.envelope.findings}
    assert len(parsed.envelope.findings) == 2
    assert len(parsed_rev.envelope.findings) == 2
    assert ids == ids_rev


def test_records_differing_by_one_field_never_collapse():
    env = finding_envelope_dict()
    distinct = copy.deepcopy(env["findings"][0])
    distinct["claim"] = "A different claim entirely."
    env["findings"] = [env["findings"][0], distinct]
    parsed = verdicts.parse_envelope(json.dumps(env), "finding")

    assert len(parsed.envelope.findings) == 2
    assert parsed.warnings == ()
    ids = {r.record_id for r in parsed.envelope.findings}
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# Parser failure modes
# ---------------------------------------------------------------------------


def test_malformed_json_raises_envelope_unparseable():
    with pytest.raises(verdicts.EnvelopeUnparseableError):
        verdicts.parse_envelope("{not valid json at all", "verdict")


def test_empty_text_raises_envelope_unparseable():
    with pytest.raises(verdicts.EnvelopeUnparseableError):
        verdicts.parse_envelope("", "verdict")


def test_json_wrapped_in_prose_parses_successfully():
    raw = (
        "Sure, here is my analysis:\n\n"
        + json.dumps(verdict_envelope_dict())
        + "\n\nLet me know if you need anything else!"
    )
    parsed = verdicts.parse_envelope(raw, "verdict")
    assert isinstance(parsed.envelope, verdicts.VerdictEnvelope)
    assert len(parsed.envelope.verdicts) == 1


def test_missing_required_field_raises_schema_invalid():
    env = verdict_envelope_dict()
    del env["candidates_sha256"]
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    assert any("candidates_sha256" in e for e in exc_info.value.errors)


def test_missing_record_field_raises_schema_invalid():
    env = verdict_envelope_dict()
    del env["verdicts"][0]["outcome_id"]
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    assert any("outcome_id" in e for e in exc_info.value.errors)


def test_invalid_verdict_enum_raises_schema_invalid():
    env = verdict_envelope_dict()
    env["verdicts"][0]["verdict"] = "MAYBE"
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    assert any("verdict" in e for e in exc_info.value.errors)


def test_invalid_finding_enum_raises_schema_invalid():
    env = finding_envelope_dict()
    env["findings"][0]["verdict"] = "BET"  # verdict values, not finding values
    with pytest.raises(verdicts.SchemaInvalidError):
        verdicts.parse_envelope(json.dumps(env), "finding")


def test_wrong_type_raises_schema_invalid():
    env = verdict_envelope_dict()
    env["verdicts"][0]["confidence"] = "high"
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    assert any("confidence" in e for e in exc_info.value.errors)


def test_non_dict_top_level_with_no_braces_raises_envelope_unparseable():
    # A bracket-less top-level list has no {...} span to extract at all --
    # indistinguishable from "no JSON object present," so this is the
    # extraction failure, not a shape failure on an extracted object.
    with pytest.raises(verdicts.EnvelopeUnparseableError):
        verdicts.parse_envelope(json.dumps(["not", "a", "dict"]), "verdict")


def test_stale_schema_version_raises_schema_invalid():
    env = verdict_envelope_dict()
    env["schema_version"] = "0.9"
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    assert any("schema_version" in e for e in exc_info.value.errors)


def test_wrong_kind_array_key_raises_schema_invalid():
    # A finding-shaped payload handed to the verdict parser is missing the
    # `verdicts` key entirely.
    with pytest.raises(verdicts.SchemaInvalidError):
        verdicts.parse_envelope(json.dumps(finding_envelope_dict()), "verdict")


def test_invalid_stream_raises_schema_invalid():
    env = verdict_envelope_dict()
    env["verdicts"][0]["stream"] = "not_a_real_stream"
    with pytest.raises(verdicts.SchemaInvalidError):
        verdicts.parse_envelope(json.dumps(env), "verdict")


def test_all_errors_collected_not_just_first():
    env = verdict_envelope_dict()
    del env["verdicts"][0]["outcome_id"]
    del env["verdicts"][0]["market_id"]
    with pytest.raises(verdicts.SchemaInvalidError) as exc_info:
        verdicts.parse_envelope(json.dumps(env), "verdict")
    joined = " ".join(exc_info.value.errors)
    assert "outcome_id" in joined
    assert "market_id" in joined


# ---------------------------------------------------------------------------
# Evidence / Contradiction / KillTrigger sub-records
# ---------------------------------------------------------------------------


def test_evidence_item_parses_with_all_fields():
    env = verdict_envelope_dict()
    env["verdicts"][0]["evidence"] = [
        {
            "claim": "Starter is confirmed.",
            "kind": "pack",
            "subject_type": "player",
            "player_id": "p1",
            "team": None,
            "market_id": "mkt_123",
            "outcome_id": "out_123",
            "source": None,
            "tier": None,
            "timestamp": None,
        }
    ]
    parsed = verdicts.parse_envelope(json.dumps(env), "verdict")
    ev = parsed.envelope.verdicts[0].evidence[0]
    assert ev.subject_type == "player"
    assert ev.player_id == "p1"


def test_evidence_invalid_subject_type_raises_schema_invalid():
    env = verdict_envelope_dict()
    env["verdicts"][0]["evidence"] = [
        {
            "claim": "x",
            "kind": "pack",
            "subject_type": "coach",  # not a valid subject_type
            "player_id": None,
            "team": None,
            "market_id": None,
            "outcome_id": None,
            "source": None,
            "tier": None,
            "timestamp": None,
        }
    ]
    with pytest.raises(verdicts.SchemaInvalidError):
        verdicts.parse_envelope(json.dumps(env), "verdict")


def test_contradiction_and_kill_trigger_parse():
    env = verdict_envelope_dict()
    env["verdicts"][0]["contradictions"] = [{"claim": "News says otherwise.", "severity": "material"}]
    env["verdicts"][0]["kill_triggers"] = [
        {"condition": "Starter scratched.", "observable_before_lock": True}
    ]
    parsed = verdicts.parse_envelope(json.dumps(env), "verdict")
    record = parsed.envelope.verdicts[0]
    assert record.contradictions[0].severity == "material"
    assert record.kill_triggers[0].observable_before_lock is True


# ---------------------------------------------------------------------------
# JSON Schema emitter
# ---------------------------------------------------------------------------


def _assert_strict_mode_compatible(schema: dict) -> None:
    """Recursively assert additionalProperties: False and every property required,
    per OpenAI strict-mode structured-output requirements."""
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        props = schema.get("properties", {})
        required = schema.get("required", [])
        assert set(required) == set(props.keys())
        for sub in props.values():
            _assert_strict_mode_compatible(sub)
    elif schema.get("type") == "array":
        _assert_strict_mode_compatible(schema.get("items", {}))


@pytest.mark.parametrize("kind", ["verdict", "finding", "reconciliation"])
def test_json_schema_is_strict_mode_compatible(kind):
    schema = verdicts.json_schema_for(kind)
    assert schema["type"] == "object"
    _assert_strict_mode_compatible(schema)


@pytest.mark.parametrize(
    "kind,array_key",
    [("verdict", "verdicts"), ("finding", "findings"), ("reconciliation", "reconciliations")],
)
def test_json_schema_has_expected_array_key(kind, array_key):
    schema = verdicts.json_schema_for(kind)
    assert array_key in schema["properties"]
    assert schema["properties"][array_key]["type"] == "array"


def test_json_schema_unknown_kind_raises():
    with pytest.raises(ValueError):
        verdicts.json_schema_for("not_a_kind")
