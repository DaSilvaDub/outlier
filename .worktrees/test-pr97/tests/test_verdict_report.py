"""No-E fallback quorum and deterministic report rendering (step 11)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

import pytest

from outlier_scrapers import pack, pack_index
from outlier_scrapers import runner_common as rc
from outlier_scrapers import verdict_report, verdicts, run_desk


def _vr(
    pass_name: str,
    verdict: str,
    units: float,
    *,
    outcome: str = "out1",
    market_id: str = "m1",
    selection: str = "Player One Over 5.5",
    line: str = "5.5",
    evidence_claim: str = "",
) -> verdicts.VerdictRecord:
    evidence = ()
    if evidence_claim:
        evidence = (
            verdicts.Evidence(
                claim=evidence_claim,
                kind="pack",
                subject_type="market",
                market_id=market_id,
                outcome_id=outcome,
            ),
        )
    return verdicts.VerdictRecord(
        market_id=market_id,
        outcome_id=outcome,
        stream="candidates",
        selection=selection,
        line=line,
        price="-110",
        book="FD",
        verdict=verdict,
        confidence=0.7,
        recommended_units=units,
        evidence=evidence,
        record_id=f"{pass_name}:{outcome}:deadbeef",
    )


def _reconcile(by_pass, publications=None, index=None):
    pubs = publications if publications is not None else {
        name: f"pub{name}" for name in by_pass
    }
    return verdict_report.reconcile_no_e(
        by_pass,
        pubs,
        pack_date="2026-08-14",
        candidates_sha256="c" * 64,
        game_totals_sha256="g" * 64,
        team_totals_sha256="t" * 64,
        index=index,
    )


def test_missing_b_is_insufficient_quorum():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.5, evidence_claim="A likes the number")],
            "D": [_vr("D", "BET", 1.0, evidence_claim="D agrees")],
            "B": [],
        },
        publications={"A": "pubA", "D": "pubD", "B": "pubB"},
    )
    assert result.synthesis_source == "fallback_no_e"
    assert result.publications == {"A": "pubA", "D": "pubD", "B": "pubB"}
    rec = result.records[0]
    assert rec.verdict == "STAND_DOWN"
    assert rec.recommended_units == 0.0
    assert rec.rejection_reasons == ("insufficient_quorum",)
    assert "B" in rec.narrative
    assert "never evaluated" in rec.narrative
    assert "[A]" in rec.narrative
    assert "[D]" in rec.narrative


def test_unanimous_bet_uses_min_stake():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.5, evidence_claim="A edge")],
            "D": [_vr("D", "BET", 0.5, evidence_claim="D smaller")],
            "B": [_vr("B", "BET", 1.0, evidence_claim="B mid")],
        }
    )
    rec = result.records[0]
    assert rec.verdict == "BET"
    assert rec.recommended_units == 0.5
    assert rec.rejection_reasons == ()
    assert result.synthesis_source == "fallback_no_e"
    assert set(c.pass_ for c in rec.cites) == {"A", "D", "B"}
    assert "[A]" in rec.narrative and "[D]" in rec.narrative and "[B]" in rec.narrative


def test_pass_among_quorum_is_stand_down():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.0)],
            "D": [_vr("D", "PASS", 0.0)],
            "B": [_vr("B", "BET", 1.0)],
        }
    )
    rec = result.records[0]
    assert rec.verdict == "STAND_DOWN"
    assert rec.recommended_units == 0.0
    assert "insufficient_quorum" not in rec.rejection_reasons
    assert rec.rejection_reasons == ("non_unanimous",)


def test_stand_down_among_quorum_is_stand_down():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.0)],
            "D": [_vr("D", "STAND_DOWN", 0.0)],
            "B": [_vr("B", "BET", 1.0)],
        }
    )
    rec = result.records[0]
    assert rec.verdict == "STAND_DOWN"
    assert rec.recommended_units == 0.0
    assert rec.rejection_reasons == ("non_unanimous",)


def test_fallback_envelope_pass_is_not_claude_e():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.0)],
            "D": [_vr("D", "BET", 1.0)],
            "B": [_vr("B", "BET", 1.0)],
        }
    )
    assert result.envelope.pass_ == "fallback"
    assert result.envelope.upstream_publication_ids["A"].startswith("pub")
    assert result.synthesis_source == "fallback_no_e"


def test_render_report_raises_on_shadow_mode():
    with pytest.raises(verdict_report.ShadowEnvelopeError):
        verdict_report.render_report(
            records=(),
            index=_empty_index(),
            synthesis_source="fallback_no_e",
            mode="shadow",
        )


def test_render_report_marks_fallback_source():
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.5)],
            "D": [_vr("D", "BET", 1.0)],
            "B": [_vr("B", "BET", 1.25)],
        }
    )
    text = verdict_report.render_report(
        records=result.records,
        index=_empty_index(),
        synthesis_source=result.synthesis_source,
        mode="enforce",
    )
    assert "synthesis_source: fallback_no_e" in text
    assert "## B. Final Betting Card" in text
    assert "m1" in text
    assert "1.0" in text
    assert verdict_report.read_synthesis_source(text) == "fallback_no_e"


def test_render_report_table_uses_index_not_envelope_identity(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, line="5.5", selection="Player One Over 5.5")
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    result = _reconcile(
        {
            "A": [_vr("A", "BET", 1.0, line="9.9", selection="TAMPERED")],
            "D": [_vr("D", "BET", 1.0, line="9.9", selection="TAMPERED")],
            "B": [_vr("B", "BET", 1.0, line="9.9", selection="TAMPERED")],
        },
        index=index,
    )
    text = verdict_report.render_report(
        records=result.records,
        index=index,
        synthesis_source="fallback_no_e",
        mode="enforce",
    )
    assert "Player One Over 5.5" in text
    assert "5.5" in text
    assert "TAMPERED" not in text
    assert "9.9" not in text


def _empty_index():
    from outlier_scrapers.portfolio import PortfolioPolicy

    return pack_index.PackIndex(
        candidates_sha256="c",
        game_totals_sha256="g",
        team_totals_sha256="t",
        rows={},
        dropped={},
        unindexed_totals=(),
        players={},
        injuries={},
        locks={},
        policy=PortfolioPolicy(mode="enforce"),
    )


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir, *, line="5.5", selection="Player One Over 5.5"):
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-08-14\n", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "_event_starts_at": _future(),
                "market_id": "m1",
                "outcome_id": "out1",
                "market_type": "PLAYER_PROP",
                "player_id": "p1",
                "selection": selection,
                "line": line,
                "price": "-110",
                "book": "FD",
                "board": "A",
                "actionable": "true",
                "market_label": "SO",
                "max_units": "2.0",
                "recommended_units_pre_news": "1.5",
                "edge_pct": "4.2",
                "model_prob_source": "blend",
            }
        )
        writer.writerow(row)


def _verdict_envelope(index, pass_name: str, units: float = 1.0, verdict: str = "BET"):
    return {
        "schema_version": "1.0",
        "pass": pass_name,
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [
            {
                "market_id": "m1",
                "outcome_id": "out1",
                "stream": "candidates",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "verdict": verdict,
                "confidence": 0.7,
                "recommended_units": units,
                "evidence": [],
                "contradictions": [],
                "kill_triggers": [],
                "rejection_reasons": [],
            }
        ],
        "slate_notes": [],
        "needs": [],
    }


def _publish(pack_dir, index, pass_name, units=1.0, verdict="BET"):
    return rc.publish_verdict_pass(
        pack_dir,
        json.dumps(_verdict_envelope(index, pass_name, units, verdict)),
        pass_=pass_name,
        request_sha256=pass_name * 16,
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        model="fake",
    )


def test_compute_no_e_fallback_from_published_passes(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    pub_a = _publish(pack_dir, index, "A", units=1.5)
    pub_d = _publish(pack_dir, index, "D", units=0.5)
    pub_b = _publish(pack_dir, index, "B", units=1.0)
    result = verdict_report.compute_no_e_fallback(
        pack_dir, index=index
    )
    assert result.synthesis_source == "fallback_no_e"
    assert result.publications == {
        "A": pub_a.publication_id,
        "D": pub_d.publication_id,
        "B": pub_b.publication_id,
    }
    rec = result.records[0]
    assert rec.verdict == "BET"
    assert rec.recommended_units == 0.5


def test_try_render_none_without_published_passes(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    assert verdict_report.try_render_from_envelopes(pack_dir) is None


def test_produce_manual_report_uses_fallback_when_adb_published(tmp_path, monkeypatch):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    _publish(pack_dir, index, "A", units=1.5)
    _publish(pack_dir, index, "D", units=0.5)
    _publish(pack_dir, index, "B", units=1.0)
    path = run_desk.produce_manual_betting_report(pack_dir)
    text = path.read_text(encoding="utf-8")
    assert "synthesis_source: fallback_no_e" in text
    assert "0.5" in text
    assert "## B. Final Betting Card" in text
    assert verdict_report.read_synthesis_source(text) == "fallback_no_e"


def test_produce_manual_report_stays_legacy_without_envelopes(tmp_path, monkeypatch):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir)
    path = run_desk.produce_manual_betting_report(pack_dir)
    text = path.read_text(encoding="utf-8")
    assert "Manual Betting Report" in text
    assert "synthesis_source: fallback_no_e" not in text
    assert verdict_report.read_synthesis_source(text) is None
