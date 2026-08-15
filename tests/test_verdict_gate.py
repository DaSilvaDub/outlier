"""Tests for outlier_scrapers.verdict_gate — one red path per violation code.

See docs/plans/2026-08-12-structured-ai-verdicts.md sections
"The deterministic gates", "Repair loop and failure policy", and
"Resolved market policy".
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

import pytest

from outlier_scrapers import pack, pack_index, verdict_gate, verdict_policy, verdicts
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER, TOTAL_KIND_GAME, TOTAL_KIND_TEAM

FUTURE = (datetime.now().astimezone() + timedelta(hours=6)).isoformat()
PAST = (datetime.now().astimezone() - timedelta(hours=1)).isoformat()
NOW = datetime.now().astimezone()


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def candidate_row(**overrides):
    row = {k: "" for k in pack.CANDIDATES_HEADER}
    row.update(
        {
            "sport": "MLB",
            "event_id": "evt1",
            "_event_starts_at": FUTURE,
            "market_id": "mkt1",
            "outcome_id": "out1",
            "market_type": "PLAYER_PROP",
            "player_id": "p1",
            "selection": "Player One Over 5.5",
            "line": "5.5",
            "price": "-110",
            "book": "FD",
            "board": "A",
            "actionable": "true",
            "market_label": "SO",
            "max_units": "2.0",
            "recommended_units_pre_news": "1.5",
            "team": "NYY",
            "team_name": "Yankees",
        }
    )
    row.update(overrides)
    return row


def totals_row(*, kind=TOTAL_KIND_GAME, **overrides):
    row = {k: "" for k in GAME_TOTALS_HEADER}
    row.update(
        {
            "totals_id": "mkt_t1:8.5:OVER",
            "sport": "MLB",
            "event_id": "evt2",
            "market_id": "mkt_t1",
            "outcome_id": "mkt_t1:8.5:OVER",
            "total_kind": kind,
            "team": "" if kind == TOTAL_KIND_GAME else "NYY",
            "selection": "Over 8.5",
            "line": "8.5",
            "price": "-110",
            "book": "FD",
        }
    )
    row.update(overrides)
    return row


def write_pack(pack_dir, *, candidates=None, game_totals=None, team_totals=None):
    pack_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(pack_dir / "candidates.csv", pack.CANDIDATES_HEADER, candidates or [candidate_row()])
    _write_csv(
        pack_dir / "game_totals.csv",
        GAME_TOTALS_HEADER,
        game_totals if game_totals is not None else [totals_row()],
    )
    _write_csv(
        pack_dir / "team_totals.csv",
        GAME_TOTALS_HEADER,
        team_totals if team_totals is not None else [
            totals_row(
                kind=TOTAL_KIND_TEAM,
                outcome_id="mkt_t2:8.5:OVER",
                totals_id="mkt_t2:8.5:OVER",
                market_id="mkt_t2",
            )
        ],
    )


def _policy_path(tmp_path):
    return tmp_path / "no_such_policy.json"


def build_index(tmp_path, **write_kw):
    pack_dir = tmp_path / "pack"
    write_pack(pack_dir, **write_kw)
    return pack_index.build_pack_index(pack_dir, now=NOW, policy_path=_policy_path(tmp_path))


def _verdict_dict(index, **record_overrides):
    record = {
        "market_id": "mkt1",
        "outcome_id": "out1",
        "stream": "candidates",
        "selection": "Player One Over 5.5",
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
    record.update(record_overrides)
    return {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "A",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [record],
        "slate_notes": [],
        "needs": [],
    }


def parse_verdict(index, **record_overrides):
    return verdicts.parse_envelope(json.dumps(_verdict_dict(index, **record_overrides)), "verdict")


def gate(envelope, index, now=NOW, **kwargs):
    return verdict_gate.validate_envelope(envelope, index, now, **kwargs)


def codes(result):
    return [v.code for v in result.violations]


def only_code(result, expected):
    assert codes(result) == [expected], codes(result)
    assert result.violations[0].severity == "reject"


# ---------------------------------------------------------------------------
# Green path
# ---------------------------------------------------------------------------


def test_green_path_compliant_bet_has_zero_violations(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index), index)
    assert result.violations == ()
    assert result.pass_fails is False


def test_validate_envelope_default_policy_does_not_load_file(tmp_path, monkeypatch):
    index = build_index(tmp_path)

    def unexpected_load(*args, **kwargs):
        raise AssertionError("validate_envelope must not load policy from disk")

    monkeypatch.setattr(verdict_gate, "load_verdict_policy", unexpected_load)
    result = gate(parse_verdict(index), index)
    assert result.violations == ()


def test_gate_reexports_canonical_policy_contract():
    assert verdict_gate.VerdictPolicy is verdict_policy.VerdictPolicy
    assert verdict_gate.load_verdict_policy is verdict_policy.load_verdict_policy


# ---------------------------------------------------------------------------
# Envelope-level
# ---------------------------------------------------------------------------


def test_envelope_unparseable_from_raw_text(tmp_path):
    index = build_index(tmp_path)
    result = gate("this is not json at all", index, kind="verdict")
    only_code(result, "envelope_unparseable")
    assert result.pass_fails is True


def test_schema_invalid_from_raw_text(tmp_path):
    index = build_index(tmp_path)
    raw = json.dumps({"schema_version": "1.0", "pass": "A"})
    result = gate(raw, index, kind="verdict")
    only_code(result, "schema_invalid")
    assert result.pass_fails is True


def test_pack_mismatch_on_candidates_hash_alone(tmp_path):
    index = build_index(tmp_path)
    parsed = parse_verdict(index)
    env = parsed.envelope
    assert isinstance(env, verdicts.VerdictEnvelope)
    stale = verdicts.VerdictEnvelope(
        schema_version=env.schema_version,
        pass_=env.pass_,
        pack_date=env.pack_date,
        candidates_sha256="0" * 64,
        game_totals_sha256=env.game_totals_sha256,
        team_totals_sha256=env.team_totals_sha256,
        verdicts=env.verdicts,
    )
    result = gate(stale, index)
    only_code(result, "pack_mismatch")
    assert result.pass_fails is True


def test_pack_mismatch_on_totals_hash_alone(tmp_path):
    index = build_index(tmp_path)
    parsed = parse_verdict(index)
    env = parsed.envelope
    assert isinstance(env, verdicts.VerdictEnvelope)
    stale = verdicts.VerdictEnvelope(
        schema_version=env.schema_version,
        pass_=env.pass_,
        pack_date=env.pack_date,
        candidates_sha256=env.candidates_sha256,
        game_totals_sha256=env.game_totals_sha256,
        team_totals_sha256="f" * 64,
        verdicts=env.verdicts,
    )
    result = gate(stale, index)
    only_code(result, "pack_mismatch")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_unknown_market_absent_outcome_id(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, outcome_id="nope"), index)
    only_code(result, "unknown_market")
    assert result.pass_fails is True


def test_unknown_market_empty_outcome_id_unindexed_total(tmp_path):
    index = build_index(
        tmp_path,
        game_totals=[totals_row(outcome_id="", totals_id="mkt_a", market_id="mkt_a")],
    )
    parsed = parse_verdict(index)
    env = parsed.envelope
    assert isinstance(env, verdicts.VerdictEnvelope)
    empty = verdicts.VerdictRecord(
        market_id="mkt_a",
        outcome_id="",
        stream="game_totals",
        selection="Over 8.5",
        line="8.5",
        price="-110",
        book="FD",
        verdict="BET",
        confidence=0.5,
        recommended_units=1.0,
    )
    result = gate(
        verdicts.VerdictEnvelope(
            schema_version=env.schema_version,
            pass_=env.pass_,
            pack_date=env.pack_date,
            candidates_sha256=env.candidates_sha256,
            game_totals_sha256=env.game_totals_sha256,
            team_totals_sha256=env.team_totals_sha256,
            verdicts=(empty,),
        ),
        index,
    )
    only_code(result, "unknown_market")


def test_market_id_mismatch(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, market_id="wrong"), index)
    only_code(result, "market_id_mismatch")


def test_totals_market_id_accepts_totals_id_compatibility_alias(tmp_path):
    index = build_index(tmp_path)
    result = gate(
        parse_verdict(
            index,
            market_id="mkt_t1:8.5:OVER",
            outcome_id="mkt_t1:8.5:OVER",
            stream="game_totals",
            selection="Over 8.5",
            line="8.5",
            book="FD",
        ),
        index,
    )
    assert "market_id_mismatch" not in codes(result)
    assert "unknown_market" not in codes(result)
    assert "locked_market" not in codes(result)
    assert result.violations == ()


def test_stream_mismatch(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, stream="game_totals"), index)
    only_code(result, "stream_mismatch")


# ---------------------------------------------------------------------------
# Tamper
# ---------------------------------------------------------------------------


def test_line_tampered_one_tick_off(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, line="6.5"), index)
    only_code(result, "line_tampered")


def test_price_tampered(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, price="+100"), index)
    only_code(result, "price_tampered")


def test_selection_tampered(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, selection="Someone Else Over 5.5"), index)
    only_code(result, "selection_tampered")


def test_book_tampered(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, book="DK"), index)
    only_code(result, "book_tampered")


def test_priced_line_unreconciled_when_priced_line_set(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(priced_line="6.5")])
    result = gate(parse_verdict(index, line="5.5"), index)
    only_code(result, "priced_line_unreconciled")


def test_priced_line_accepted_when_verdict_quotes_it(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(priced_line="6.5")])
    result = gate(parse_verdict(index, line="6.5"), index)
    assert "priced_line_unreconciled" not in codes(result)
    assert "line_tampered" not in codes(result)


def test_priced_line_unreconciled_from_ev_fallback_flag(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(data_quality_flags="ev_line_fallback:priced_at=6.5")],
    )
    result = gate(parse_verdict(index, line="5.5"), index)
    only_code(result, "priced_line_unreconciled")


def test_priced_line_unreconciled_from_semicolon_multi_flag(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[
            candidate_row(
                data_quality_flags="source_note;ev_line_fallback:priced_at=6.5",
            )
        ],
    )
    result = gate(parse_verdict(index, line="5.5"), index)
    only_code(result, "priced_line_unreconciled")


def test_quality_flag_tokenizer_supports_both_columns_and_delimiters():
    assert verdict_gate._quality_flag_tokens(
        {
            "data_quality_flags": "current_one;current_two",
            "quality_flags": "legacy_one,legacy_two",
        }
    ) == ("current_one", "current_two", "legacy_one", "legacy_two")


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------


def test_unknown_player(tmp_path):
    index = build_index(tmp_path)
    evidence = [
        {
            "claim": "Player is healthy.",
            "kind": "pack",
            "subject_type": "player",
            "player_id": "p999",
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    only_code(result, "unknown_player")


def test_unbound_player_claim(tmp_path):
    index = build_index(tmp_path)
    evidence = [
        {
            "claim": "A mystery player is heating up.",
            "kind": "pack",
            "subject_type": "player",
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    only_code(result, "unbound_player_claim")


def test_unbound_name_in_prose_is_warn_only(tmp_path):
    index = build_index(tmp_path)
    evidence = [
        {
            "claim": "Player One is heating up in this matchup.",
            "kind": "pack",
            "subject_type": "market",
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    assert codes(result) == ["unbound_name_in_prose"]
    assert result.violations[0].severity == "warn"
    assert result.pass_fails is False


# ---------------------------------------------------------------------------
# Injury
# ---------------------------------------------------------------------------


def test_unsupported_injury_claim_empty_flags(tmp_path):
    index = build_index(tmp_path)
    evidence = [
        {
            "claim": "Player One is out with an injury.",
            "kind": "pack",
            "subject_type": "player",
            "player_id": "p1",
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    only_code(result, "unsupported_injury_claim")


def test_injury_claim_supported_by_pack_flags_for_pass_a(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(injury_flags="IL:hamstring")])
    evidence = [
        {
            "claim": "Player One is out with an injury.",
            "kind": "pack",
            "subject_type": "player",
            "player_id": "p1",
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    assert "unsupported_injury_claim" not in codes(result)


def test_injury_claim_supported_by_grounded_source(tmp_path):
    index = build_index(tmp_path)
    evidence = [
        {
            "claim": "Player One is questionable with a scratch risk.",
            "kind": "external",
            "subject_type": "player",
            "player_id": "p1",
            "source": "Beat writer",
            "tier": 2,
            "timestamp": (NOW - timedelta(hours=2)).isoformat(),
        }
    ]
    result = gate(parse_verdict(index, evidence=evidence), index)
    assert "unsupported_injury_claim" not in codes(result)


# ---------------------------------------------------------------------------
# Stakes
# ---------------------------------------------------------------------------


def test_stake_above_row_cap(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, recommended_units=2.0), index)
    only_code(result, "stake_above_row_cap")


def test_stake_at_exactly_max_units_passes_when_row_cap_allows(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(max_units="2.0", recommended_units_pre_news="2.0")],
    )
    result = gate(parse_verdict(index, recommended_units=2.0), index)
    assert "stake_above_row_cap" not in codes(result)
    assert "stake_above_policy_cap" not in codes(result)


def test_stake_one_increment_above_max_units_fails(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(max_units="2.0", recommended_units_pre_news="3.0")],
    )
    result = gate(parse_verdict(index, recommended_units=2.5), index)
    only_code(result, "stake_above_row_cap")


def test_stake_above_policy_cap(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(max_units="5.0", recommended_units_pre_news="5.0")],
    )
    result = gate(parse_verdict(index, recommended_units=3.5), index)
    only_code(result, "stake_above_policy_cap")


def test_stake_off_increment(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, recommended_units=1.25), index)
    only_code(result, "stake_off_increment")


def test_negative_stake(tmp_path):
    index = build_index(tmp_path)
    result = gate(parse_verdict(index, recommended_units=-0.5), index)
    only_code(result, "negative_stake")


# ---------------------------------------------------------------------------
# Locked / integrity
# ---------------------------------------------------------------------------


def test_locked_market_already_in_drop_set(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(_event_starts_at=PAST)])
    result = gate(parse_verdict(index), index)
    only_code(result, "locked_market")


def test_locked_market_when_event_locks_between_index_and_gate(tmp_path):
    start = (NOW + timedelta(minutes=30)).isoformat()
    index = build_index(tmp_path, candidates=[candidate_row(_event_starts_at=start)])
    later = NOW + timedelta(hours=1)
    result = gate(parse_verdict(index), index, now=later)
    only_code(result, "locked_market")


def test_integrity_flag_disqualifying_dq(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(data_quality_flags="spread_sign_conflict")],
    )
    result = gate(parse_verdict(index), index)
    only_code(result, "integrity_flag")


def test_integrity_flag_disqualifying_dq_after_semicolon_fails_closed(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(data_quality_flags="source_note;spread_sign_conflict")],
    )
    result = gate(parse_verdict(index), index)
    only_code(result, "integrity_flag")


def test_integrity_flag_actionable_false(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(actionable="false")])
    result = gate(parse_verdict(index), index)
    only_code(result, "integrity_flag")


def test_integrity_flag_board_a_flagged(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(board="A_FLAGGED")])
    result = gate(parse_verdict(index), index)
    only_code(result, "integrity_flag")


# ---------------------------------------------------------------------------
# Variance / prohibited / longshot / side
# ---------------------------------------------------------------------------


def test_high_variance_market_3pm(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="3PM")])
    result = gate(parse_verdict(index), index)
    only_code(result, "high_variance_market")


def test_tb_is_not_high_variance(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="TB")])
    result = gate(parse_verdict(index), index)
    assert "high_variance_market" not in codes(result)
    assert result.violations == ()


def test_prohibited_market_hrr_any_scope(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="HRR")])
    result = gate(parse_verdict(index), index)
    only_code(result, "prohibited_market")


def test_prohibited_market_home_runs_alias(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="HOME_RUNS")])
    result = gate(parse_verdict(index), index)
    only_code(result, "prohibited_market")


def test_prohibited_market_player_bb(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(market_label="BB", market_type="PLAYER_PROP")],
    )
    result = gate(parse_verdict(index), index)
    only_code(result, "prohibited_market")


def test_team_bb_is_not_prohibited(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(market_label="BB", market_type="TEAM_PROP", player_id="")],
    )
    result = gate(parse_verdict(index), index)
    assert "prohibited_market" not in codes(result)
    assert result.violations == ()


def test_longshot_price_at_plus_150(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(price="+150")])
    result = gate(parse_verdict(index, price="+150"), index)
    only_code(result, "longshot_price")


def test_longshot_price_plus_149_passes(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(price="+149")])
    result = gate(parse_verdict(index, price="+149"), index)
    assert "longshot_price" not in codes(result)
    assert result.violations == ()


def test_side_restricted_2b_over(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(market_label="2B", selection="Player One Over 1.5", line="1.5")],
    )
    result = gate(
        parse_verdict(index, selection="Player One Over 1.5", line="1.5"),
        index,
    )
    only_code(result, "side_restricted")


def test_2b_under_is_not_side_restricted(tmp_path):
    index = build_index(
        tmp_path,
        candidates=[candidate_row(market_label="2B", selection="Player One Under 1.5", line="1.5")],
    )
    result = gate(
        parse_verdict(index, selection="Player One Under 1.5", line="1.5"),
        index,
    )
    assert "side_restricted" not in codes(result)
    assert result.violations == ()


# ---------------------------------------------------------------------------
# PASS / STAND_DOWN exemptions
# ---------------------------------------------------------------------------


def test_stand_down_skips_stake_and_variance_but_not_tamper(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="3PM")])
    result = gate(
        parse_verdict(index, verdict="STAND_DOWN", recommended_units=9.0, line="9.9"),
        index,
    )
    assert codes(result) == ["line_tampered"]
    assert result.pass_fails is True


def test_stand_down_high_variance_does_not_fail(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(market_label="3PM")])
    result = gate(parse_verdict(index, verdict="STAND_DOWN", recommended_units=0.0), index)
    assert result.violations == ()
    assert result.pass_fails is False


# ---------------------------------------------------------------------------
# Finding envelope (no stake)
# ---------------------------------------------------------------------------


def test_finding_envelope_has_no_stake_to_violate(tmp_path):
    index = build_index(tmp_path)
    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "C",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "findings": [
            {
                "market_id": "mkt1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
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
    parsed = verdicts.parse_envelope(json.dumps(raw), "finding")
    result = gate(parsed, index)
    assert result.violations == ()


# ---------------------------------------------------------------------------
# E synthesis gates
# ---------------------------------------------------------------------------


def test_unsourced_synthesis_when_no_upstream_verdict(tmp_path):
    index = build_index(tmp_path)
    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {"A": None, "D": None, "B": None, "C": "pub_c"},
        "reconciliations": [
            {
                "market_id": "mkt1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "recommended_units": 1.0,
                "narrative": "Going alone.",
                "cites": [{"pass": "C", "publication_id": "pub_c", "record_id": "only_c"}],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    parsed = verdicts.parse_envelope(json.dumps(raw), "reconciliation")
    pubs = {
        "C": verdict_gate.UpstreamPublication(
            pass_="C",
            publication_id="pub_c",
            record_ids=frozenset({"only_c"}),
            outcome_ids=frozenset(),
        )
    }
    result = gate(parsed, index, current_publications=pubs)
    only_code(result, "unsourced_synthesis")


def test_stale_citation_publication_id(tmp_path):
    index = build_index(tmp_path)
    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {"A": "pub_a_old", "D": None, "B": None, "C": None},
        "reconciliations": [
            {
                "market_id": "mkt1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "BET",
                "recommended_units": 1.0,
                "narrative": "A agrees.",
                "cites": [{"pass": "A", "publication_id": "pub_a_old", "record_id": "rec_a"}],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    parsed = verdicts.parse_envelope(json.dumps(raw), "reconciliation")
    pubs = {
        "A": verdict_gate.UpstreamPublication(
            pass_="A",
            publication_id="pub_a_new",
            record_ids=frozenset({"rec_a"}),
            outcome_ids=frozenset({"out1"}),
        )
    }
    result = gate(parsed, index, current_publications=pubs)
    assert codes(result) == ["stale_upstream_publications", "stale_citation"]


def test_e_injury_claim_without_citation_fails_even_with_pack_flags(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(injury_flags="IL:hamstring")])
    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {
            "A": "pub_a",
            "D": None,
            "B": "pub_b",
            "C": None,
        },
        "reconciliations": [
            {
                "market_id": "mkt1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "STAND_DOWN",
                "recommended_units": 0.0,
                "narrative": "The starter is out with an injury.",
                "cites": [{"pass": "A", "publication_id": "pub_a", "record_id": "rec_a"}],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    # Attach injury evidence by wrapping after parse is awkward; put it in narrative
    # and also inject via a parsed envelope mutation is not possible (frozen).
    # Build evidence through a verdict-shaped claim on a custom evidence-bearing path:
    # ReconciliationRecord has no evidence field. Injury on E is evaluated from
    # cites + narrative keywords. Here narrative asserts injury and cites only A.
    parsed = verdicts.parse_envelope(json.dumps(raw), "reconciliation")
    pubs = {
        "A": verdict_gate.UpstreamPublication(
            pass_="A",
            publication_id="pub_a",
            record_ids=frozenset({"rec_a"}),
            outcome_ids=frozenset({"out1"}),
        ),
        "B": verdict_gate.UpstreamPublication(
            pass_="B",
            publication_id="pub_b",
            record_ids=frozenset({"rec_b"}),
            outcome_ids=frozenset({"out1"}),
            injury_supported_record_ids=frozenset({"rec_b"}),
        ),
    }
    result = gate(parsed, index, current_publications=pubs)
    only_code(result, "unsupported_injury_claim")


def test_e_injury_claim_citing_validated_b_finding_passes(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(injury_flags="IL:hamstring")])
    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "E",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "upstream_publication_ids": {
            "A": "pub_a",
            "D": None,
            "B": "pub_b",
            "C": None,
        },
        "reconciliations": [
            {
                "market_id": "mkt1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": "STAND_DOWN",
                "recommended_units": 0.0,
                "narrative": "Player One is out with an injury.",
                "cites": [
                    {"pass": "A", "publication_id": "pub_a", "record_id": "rec_a"},
                    {"pass": "B", "publication_id": "pub_b", "record_id": "rec_b"},
                ],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }
    parsed = verdicts.parse_envelope(json.dumps(raw), "reconciliation")
    pubs = {
        "A": verdict_gate.UpstreamPublication(
            pass_="A",
            publication_id="pub_a",
            record_ids=frozenset({"rec_a"}),
            outcome_ids=frozenset({"out1"}),
        ),
        "B": verdict_gate.UpstreamPublication(
            pass_="B",
            publication_id="pub_b",
            record_ids=frozenset({"rec_b"}),
            outcome_ids=frozenset({"out1"}),
            injury_supported_record_ids=frozenset({"rec_b"}),
        ),
    }
    result = gate(parsed, index, current_publications=pubs)
    assert "unsupported_injury_claim" not in codes(result)


# ---------------------------------------------------------------------------
# Failure-class split
# ---------------------------------------------------------------------------


def test_all_voluntary_stand_downs_never_trip_ratio(tmp_path):
    index = build_index(tmp_path)
    parsed = parse_verdict(index, verdict="STAND_DOWN", recommended_units=0.0)
    result = gate(parsed, index)
    assert result.violations == ()
    assert result.pass_fails is False
    assert result.reject_fail_ratio is None


def test_identity_tamper_fails_pass_even_under_ratio(tmp_path):
    index = build_index(tmp_path)
    base = _verdict_dict(index)
    clean = dict(base["verdicts"][0])
    clean["outcome_id"] = "out1"
    dirty = dict(clean)
    dirty["line"] = "9.9"
    # two attempted BETs, only one identity-failing — ratio 0.5 would be at
    # the default threshold, but identity fails the pass regardless.
    env = dict(base)
    env["verdicts"] = [clean, dirty]
    # dirty shares outcome_id so parse will keep both (content differs).
    parsed = verdicts.parse_envelope(json.dumps(env), "verdict")
    result = gate(parsed, index)
    assert "line_tampered" in codes(result)
    assert result.pass_fails is True


def test_judgement_rejects_above_ratio_fail_the_pass(tmp_path):
    # Three attempted BETs: two prohibited (HRR, player BB) + one clean SO.
    # Judgement reject ratio = 2/3 > 0.5.
    rows = [
        candidate_row(outcome_id="out_so", market_id="mkt_so", market_label="SO"),
        candidate_row(outcome_id="out_hrr", market_id="mkt_hrr", market_label="HRR"),
        candidate_row(
            outcome_id="out_bb",
            market_id="mkt_bb",
            market_label="BB",
            market_type="PLAYER_PROP",
        ),
    ]
    index = build_index(tmp_path, candidates=rows)
    def rec(outcome_id, market_id):
        return {
            "market_id": market_id,
            "outcome_id": outcome_id,
            "stream": "candidates",
            "selection": "Player One Over 5.5",
            "line": "5.5",
            "price": "-110",
            "book": "FD",
            "verdict": "BET",
            "confidence": 0.7,
            "recommended_units": 1.0,
            "evidence": [],
            "contradictions": [],
            "kill_triggers": [],
            "rejection_reasons": [],
        }

    raw = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "A",
        "pack_date": "2026-08-12",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [
            rec("out_so", "mkt_so"),
            rec("out_hrr", "mkt_hrr"),
            rec("out_bb", "mkt_bb"),
        ],
        "slate_notes": [],
        "needs": [],
    }
    parsed = verdicts.parse_envelope(json.dumps(raw), "verdict")
    result = gate(parsed, index)
    assert result.pass_fails is True
    assert result.reject_fail_ratio == pytest.approx(2 / 3)


def test_priced_line_still_checks_book_tamper(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(priced_line="6.5")])
    result = gate(parse_verdict(index, line="6.5", book="DK"), index)
    only_code(result, "book_tampered")


def test_stand_down_on_locked_row_records_lock_but_does_not_fail_pass(tmp_path):
    index = build_index(tmp_path, candidates=[candidate_row(_event_starts_at=PAST)])
    result = gate(parse_verdict(index, verdict="STAND_DOWN", recommended_units=0.0), index)
    only_code(result, "locked_market")
    assert result.pass_fails is False


def test_injury_flag_on_teammate_does_not_support_other_player(tmp_path):
    rows = [
        candidate_row(injury_flags="IL:hamstring"),
        candidate_row(
            outcome_id="out2",
            market_id="mkt2",
            player_id="p2",
            selection="Player Two Over 5.5",
            injury_flags="",
        ),
    ]
    index = build_index(tmp_path, candidates=rows)
    evidence = [
        {
            "claim": "Player Two is out with an injury.",
            "kind": "pack",
            "subject_type": "player",
            "player_id": "p2",
        }
    ]
    result = gate(
        parse_verdict(
            index,
            outcome_id="out2",
            market_id="mkt2",
            selection="Player Two Over 5.5",
            evidence=evidence,
        ),
        index,
    )
    only_code(result, "unsupported_injury_claim")


def test_violation_class_split_is_hard_coded():
    assert verdict_gate.violation_class("line_tampered") == "identity_tamper"
    assert verdict_gate.violation_class("unknown_market") == "identity_tamper"
    assert verdict_gate.violation_class("prohibited_market") == "judgement"
    assert verdict_gate.violation_class("stake_above_row_cap") == "judgement"
    assert verdict_gate.violation_class("unbound_name_in_prose") == "warn"
    assert verdict_gate.violation_class("pack_mismatch") == "envelope"
