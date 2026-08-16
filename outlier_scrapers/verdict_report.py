"""Deterministic Markdown renderer and no-E fallback reconciliation.

When pass E is missing or not authoritative, this module computes a
conservative A+D+B quorum and renders the A.md §14 report. See
docs/plans/2026-08-12-structured-ai-verdicts.md sections
'The no-E fallback reconciliation' and 'Deterministic Markdown'.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping, Sequence

from outlier_scrapers import pack_index, verdicts
from outlier_scrapers.pack_index import IndexedRow, PackIndex
from outlier_scrapers.verdicts import Citation, FindingRecord, ReconciliationRecord, VerdictRecord

REQUIRED_QUORUM = frozenset({"A", "D", "B"})
SYNTHESIS_CLAUDE_E = "claude_e"
SYNTHESIS_FALLBACK = "fallback_no_e"
SYNTHESIS_MARKER = "synthesis_source:"


class ShadowEnvelopeError(ValueError):
    """Authoritative rendering refused a shadow-mode envelope."""


@dataclass(frozen=True)
class FallbackRecord:
    outcome_id: str
    market_id: str
    stream: str
    selection: str
    line: str
    price: str
    book: str
    verdict: str
    recommended_units: float
    narrative: str
    rejection_reasons: tuple[str, ...]
    cites: tuple[Citation, ...]
    record_id: str
    contributors: tuple[str, ...]

    def to_reconciliation(self) -> ReconciliationRecord:
        return ReconciliationRecord(
            market_id=self.market_id,
            outcome_id=self.outcome_id,
            stream=self.stream,
            selection=self.selection,
            line=self.line,
            price=self.price,
            book=self.book,
            verdict=self.verdict,
            recommended_units=self.recommended_units,
            narrative=self.narrative,
            cites=self.cites,
            rejection_reasons=self.rejection_reasons,
            record_id=self.record_id,
        )


@dataclass(frozen=True)
class FallbackResult:
    synthesis_source: str
    publications: dict[str, str]
    records: tuple[FallbackRecord, ...]
    envelope: verdicts.ReconciliationEnvelope


def read_synthesis_source(text: str) -> str | None:
    """Return the synthesis_source marker from a rendered report, if any."""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(SYNTHESIS_MARKER):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _identity(
    outcome_id: str,
    by_pass: Mapping[str, VerdictRecord],
    index: PackIndex | None,
) -> dict[str, str]:
    if index is not None and outcome_id in index.rows:
        row = index.rows[outcome_id]
        data = row.data
        return {
            "market_id": row.market_id,
            "stream": row.stream,
            "selection": str(data.get("selection") or ""),
            "line": str(data.get("line") or ""),
            "price": str(data.get("price") or ""),
            "book": str(data.get("book") or ""),
        }
    rec = by_pass.get("A") or by_pass.get("D") or by_pass.get("B")
    if rec is None:
        return {
            "market_id": "",
            "stream": "candidates",
            "selection": "",
            "line": "",
            "price": "",
            "book": "",
        }
    return {
        "market_id": rec.market_id,
        "stream": rec.stream,
        "selection": rec.selection,
        "line": rec.line,
        "price": rec.price,
        "book": rec.book,
    }


def _pass_rationale(pass_name: str, record: VerdictRecord) -> str:
    claims = [item.claim for item in record.evidence if item.claim]
    body = "; ".join(claims) if claims else record.verdict
    return f"[{pass_name}] {body}"


def _record_id(outcome_id: str, payload: dict[str, object]) -> str:
    return f"fallback:{outcome_id}:{verdicts._content_hash('reconciliation', payload)}"


def reconcile_outcome(
    outcome_id: str,
    by_pass: Mapping[str, VerdictRecord],
    publications: Mapping[str, str],
    *,
    index: PackIndex | None = None,
) -> FallbackRecord:
    """Apply the four-branch quorum to one outcome_id. No model call."""
    contributors = frozenset(by_pass)
    missing = tuple(sorted(REQUIRED_QUORUM - contributors))
    identity = _identity(outcome_id, by_pass, index)
    cites = tuple(
        Citation(
            pass_=name,
            publication_id=publications.get(name, ""),
            record_id=record.record_id,
        )
        for name, record in sorted(by_pass.items())
    )
    rationale_lines = [_pass_rationale(name, by_pass[name]) for name in ("A", "D", "B") if name in by_pass]

    if contributors != REQUIRED_QUORUM:
        verdict = "STAND_DOWN"
        units = 0.0
        reasons: tuple[str, ...] = ("insufficient_quorum",)
        who = ", ".join(missing) if missing else "required passes"
        narrative = (
            f"insufficient_quorum: {who} never evaluated this row.\n"
            + "\n".join(rationale_lines)
        )
    elif any(record.verdict != "BET" for record in by_pass.values()):
        verdict = "STAND_DOWN"
        units = 0.0
        reasons = ("non_unanimous",)
        narrative = "\n".join(rationale_lines)
    else:
        verdict = "BET"
        units = min(record.recommended_units for record in by_pass.values())
        reasons = ()
        narrative = "\n".join(rationale_lines)

    payload = {
        "market_id": identity["market_id"],
        "outcome_id": outcome_id,
        "stream": identity["stream"],
        "selection": identity["selection"],
        "line": identity["line"],
        "price": identity["price"],
        "book": identity["book"],
        "verdict": verdict,
        "recommended_units": units,
        "narrative": narrative,
    }
    return FallbackRecord(
        outcome_id=outcome_id,
        market_id=identity["market_id"],
        stream=identity["stream"],
        selection=identity["selection"],
        line=identity["line"],
        price=identity["price"],
        book=identity["book"],
        verdict=verdict,
        recommended_units=units,
        narrative=narrative,
        rejection_reasons=reasons,
        cites=cites,
        record_id=_record_id(outcome_id, payload),
        contributors=tuple(sorted(contributors)),
    )


def reconcile_no_e(
    pass_records: Mapping[str, Sequence[VerdictRecord]],
    publications: Mapping[str, str],
    *,
    pack_date: str,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
    index: PackIndex | None = None,
) -> FallbackResult:
    """Deterministic A+D+B reconciliation. synthesis_source is always fallback_no_e."""
    by_outcome: dict[str, dict[str, VerdictRecord]] = {}
    for pass_name in ("A", "D", "B"):
        for record in pass_records.get(pass_name, ()):
            slot = by_outcome.setdefault(record.outcome_id, {})
            slot.setdefault(pass_name, record)

    records = tuple(
        reconcile_outcome(outcome_id, by_pass, publications, index=index)
        for outcome_id, by_pass in sorted(by_outcome.items())
    )
    envelope = verdicts.ReconciliationEnvelope(
        schema_version=verdicts.SCHEMA_VERSION,
        pass_="fallback",
        pack_date=pack_date,
        candidates_sha256=candidates_sha256,
        game_totals_sha256=game_totals_sha256,
        team_totals_sha256=team_totals_sha256,
        upstream_publication_ids=dict(publications),
        reconciliations=tuple(item.to_reconciliation() for item in records),
    )
    return FallbackResult(
        synthesis_source=SYNTHESIS_FALLBACK,
        publications=dict(publications),
        records=records,
        envelope=envelope,
    )


def load_current_verdict_envelopes(
    pack_dir: Path,
) -> dict[str, tuple[str, verdicts.VerdictEnvelope]]:
    """Read A/D/B current.json + verdicts.json. Missing passes are omitted."""
    loaded: dict[str, tuple[str, verdicts.VerdictEnvelope]] = {}
    for name in ("A", "D", "B"):
        pointer = pack_dir / "verdicts" / name / "current.json"
        if not pointer.exists():
            continue
        current = json.loads(pointer.read_text(encoding="utf-8"))
        pub_id = str(current.get("publication_id") or "")
        envelope_path = pack_dir / "verdicts" / name / pub_id / "verdicts.json"
        if not pub_id or not envelope_path.exists():
            continue
        parsed = verdicts.parse_envelope(envelope_path.read_text(encoding="utf-8"), "verdict")
        if isinstance(parsed.envelope, verdicts.VerdictEnvelope):
            loaded[name] = (pub_id, parsed.envelope)
    return loaded


def load_current_findings(pack_dir: Path) -> tuple[FindingRecord, ...]:
    pointer = pack_dir / "verdicts" / "C" / "current.json"
    if not pointer.exists():
        return ()
    current = json.loads(pointer.read_text(encoding="utf-8"))
    pub_id = str(current.get("publication_id") or "")
    envelope_path = pack_dir / "verdicts" / "C" / pub_id / "verdicts.json"
    if not pub_id or not envelope_path.exists():
        return ()
    parsed = verdicts.parse_envelope(envelope_path.read_text(encoding="utf-8"), "finding")
    if isinstance(parsed.envelope, verdicts.FindingEnvelope):
        return parsed.envelope.findings
    return ()


def _publication_mode(pack_dir: Path, pass_name: str, publication_id: str) -> str:
    status_path = pack_dir / "verdicts" / pass_name / publication_id / "status_fragment.json"
    if not status_path.exists():
        return "shadow"
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "shadow"
    return str(payload.get("mode") or "shadow")


def compute_no_e_fallback(
    pack_dir: Path,
    *,
    index: PackIndex | None = None,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
) -> FallbackResult:
    """Load current A/D/B publications and compute the fallback envelope."""
    if index is None:
        index = pack_index.build_pack_index(pack_dir, now=now, policy_path=policy_path)
    loaded = load_current_verdict_envelopes(pack_dir)
    publications = {name: pub_id for name, (pub_id, _env) in loaded.items()}
    pass_records = {
        name: list(env.verdicts) for name, (_pub_id, env) in loaded.items()
    }
    c_pointer = pack_dir / "verdicts" / "C" / "current.json"
    if c_pointer.exists():
        current = json.loads(c_pointer.read_text(encoding="utf-8"))
        c_id = str(current.get("publication_id") or "")
        if c_id:
            publications["C"] = c_id
    return reconcile_no_e(
        pass_records,
        publications,
        pack_date=pack_dir.name,
        candidates_sha256=index.candidates_sha256,
        game_totals_sha256=index.game_totals_sha256,
        team_totals_sha256=index.team_totals_sha256,
        index=index,
    )


def _index_row(index: PackIndex, outcome_id: str) -> IndexedRow | None:
    return index.rows.get(outcome_id)


def _cell(index: PackIndex, rec: FallbackRecord | ReconciliationRecord, key: str, fallback: str) -> str:
    row = _index_row(index, rec.outcome_id)
    if row is None:
        return fallback
    if key == "market_id":
        return row.market_id or fallback
    return str(row.data.get(key) or fallback)


def _player_name(index: PackIndex, outcome_id: str) -> str:
    row = _index_row(index, outcome_id)
    if row is None:
        return ""
    player_id = str(row.data.get("player_id") or "")
    if player_id and player_id in index.players:
        return index.players[player_id].name
    return str(row.data.get("selection") or "")


def render_report(
    *,
    records: Sequence[FallbackRecord | ReconciliationRecord],
    index: PackIndex,
    findings: Sequence[FindingRecord] = (),
    synthesis_source: str,
    slate_notes: Sequence[str] = (),
    needs: Sequence[str] = (),
    mode: str = "enforce",
    pack_date: str = "",
) -> str:
    """Render A.md §14. Raises if asked to treat a shadow envelope as authoritative."""
    if mode == "shadow":
        raise ShadowEnvelopeError(
            "verdict_report refuses to render an authoritative report from a shadow-mode envelope"
        )

    bets = [rec for rec in records if rec.verdict == "BET"]
    stood = [rec for rec in records if rec.verdict != "BET"]
    raw_units = sum(rec.recommended_units for rec in bets)
    strongest = ""
    if bets:
        top = max(bets, key=lambda rec: rec.recommended_units)
        strongest = f"{_cell(index, top, 'selection', top.selection)} ({top.recommended_units}u)"

    lines: list[str] = [
        f"# Betting Report — {pack_date or 'pack'}",
        "",
        f"{SYNTHESIS_MARKER} {synthesis_source}",
        "",
        "## A. Executive Verdict",
        "",
        f"- final recommended bets: {len(bets)}",
        f"- total raw units: {raw_units}",
        f"- total correlation-adjusted units: {raw_units}",
        f"- strongest overall play: {strongest or '(none)'}",
        "- most important slate-wide risk or caveat: deterministic no-E quorum"
        if synthesis_source == SYNTHESIS_FALLBACK
        else "- most important slate-wide risk or caveat: see stand-downs",
        "",
        "## B. Final Betting Card",
        "",
        "| Rank | Sport / Matchup | Market ID | Exact Selection | Line | Price | Book | Probability Source | Pack Edge | Pre-News Units | Final Units | Confidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not bets:
        lines.append("|  |  |  | (no surviving bets) |  |  |  |  |  |  |  |  |")
    for rank, rec in enumerate(bets, start=1):
        row = _index_row(index, rec.outcome_id)
        data = row.data if row is not None else {}
        selection = _cell(index, rec, "selection", rec.selection)
        player = _player_name(index, rec.outcome_id)
        matchup = str(data.get("matchup") or data.get("sport") or player or "")
        lines.append(
            "| {rank} | {matchup} | {mid} | {sel} | {line} | {price} | {book} | {psrc} | {edge} | {pre} | {final} |  |".format(
                rank=rank,
                matchup=matchup,
                mid=_cell(index, rec, "market_id", rec.market_id),
                sel=selection,
                line=_cell(index, rec, "line", rec.line),
                price=_cell(index, rec, "price", rec.price),
                book=_cell(index, rec, "book", rec.book),
                psrc=str(data.get("model_prob_source") or ""),
                edge=str(data.get("edge_pct") or ""),
                pre=str(data.get("recommended_units_pre_news") or ""),
                final=rec.recommended_units,
            )
        )
        narrative = getattr(rec, "narrative", "")
        if narrative:
            lines.append("")
            lines.append(narrative)
            lines.append("")

    lines += [
        "",
        "## C. Research Validation",
        "",
        "| Market ID | Claim | Source | Tier | Timestamp | Impact |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if findings:
        for finding in findings:
            lines.append(
                f"| {finding.market_id} | {finding.claim} | {finding.source_name} | "
                f"{finding.source_tier} | {finding.source_timestamp} | {finding.verdict} |"
            )
    else:
        lines.append("|  | (no validated C findings) |  |  |  |  |")

    event_bets: dict[str, list[str]] = {}
    for rec in bets:
        row = _index_row(index, rec.outcome_id)
        event_id = str(row.data.get("event_id") or "") if row is not None else ""
        if event_id:
            event_bets.setdefault(event_id, []).append(
                _cell(index, rec, "market_id", rec.market_id)
            )
    multi = {eid: mids for eid, mids in event_bets.items() if len(mids) > 1}
    lines += ["", "## D. Correlation and Portfolio Risk", ""]
    if multi:
        for event_id, mids in sorted(multi.items()):
            lines.append(f"- event_id={event_id} bets={', '.join(mids)}")
    else:
        lines.append("No same-event adjustment was required.")

    lines += [
        "",
        "## E. Stand-Down / Rejected Candidates",
        "",
        "| Market ID | Exact Selection | Line | Price | Reason for Rejection |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not stood:
        lines.append("|  | (none) |  |  |  |")
    for rec in stood:
        reasons = ", ".join(getattr(rec, "rejection_reasons", ()) or ())
        lines.append(
            "| {mid} | {sel} | {line} | {price} | {why} |".format(
                mid=_cell(index, rec, "market_id", rec.market_id),
                sel=_cell(index, rec, "selection", rec.selection),
                line=_cell(index, rec, "line", rec.line),
                price=_cell(index, rec, "price", rec.price),
                why=reasons or rec.verdict,
            )
        )

    lines += ["", "## F. Slate Integrity Notes", ""]
    for note in slate_notes:
        lines.append(f"- {note}")
    for need in needs:
        lines.append(f"- need: {need}")
    if index.unindexed_totals:
        lines.append(
            f"- unindexed totals rows (empty outcome_id): {len(index.unindexed_totals)}"
        )
    if not slate_notes and not needs and not index.unindexed_totals:
        lines.append("- no additional integrity notes")
    lines += ["", "`No qualifying SGP.`", ""]
    return "\n".join(lines)


def _load_e_envelope(
    pack_dir: Path,
) -> tuple[str, verdicts.ReconciliationEnvelope] | None:
    pointer = pack_dir / "verdicts" / "E" / "current.json"
    if not pointer.exists():
        return None
    current = json.loads(pointer.read_text(encoding="utf-8"))
    pub_id = str(current.get("publication_id") or "")
    envelope_path = pack_dir / "verdicts" / "E" / pub_id / "verdicts.json"
    if not pub_id or not envelope_path.exists():
        return None
    if _publication_mode(pack_dir, "E", pub_id) == "shadow":
        return None
    parsed = verdicts.parse_envelope(envelope_path.read_text(encoding="utf-8"), "reconciliation")
    if isinstance(parsed.envelope, verdicts.ReconciliationEnvelope):
        return pub_id, parsed.envelope
    return None


def try_render_from_envelopes(
    pack_dir: Path,
    *,
    now: datetime | None = None,
    policy_path: Path | str | None = None,
) -> tuple[str, str] | None:
    """Render from E (enforce) or the no-E fallback when A/D/B are published.

    Returns None when the validated envelope set is not ready, so callers can
    keep today's Markdown path.
    """
    loaded = load_current_verdict_envelopes(pack_dir)
    if not REQUIRED_QUORUM.issubset(loaded):
        return None
    index = pack_index.build_pack_index(pack_dir, now=now, policy_path=policy_path)
    findings = load_current_findings(pack_dir)
    e_loaded = _load_e_envelope(pack_dir)
    if e_loaded is not None:
        _pub_id, envelope = e_loaded
        text = render_report(
            records=envelope.reconciliations,
            index=index,
            findings=findings,
            synthesis_source=SYNTHESIS_CLAUDE_E,
            slate_notes=envelope.slate_notes,
            needs=envelope.needs,
            mode="enforce",
            pack_date=pack_dir.name,
        )
        return text, SYNTHESIS_CLAUDE_E
    result = compute_no_e_fallback(pack_dir, index=index)
    text = render_report(
        records=result.records,
        index=index,
        findings=findings,
        synthesis_source=result.synthesis_source,
        slate_notes=result.envelope.slate_notes,
        needs=result.envelope.needs,
        mode="enforce",
        pack_date=pack_dir.name,
    )
    return text, result.synthesis_source
