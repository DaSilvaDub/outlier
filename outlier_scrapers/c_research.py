"""Gemini Prompt C runner for current injury and lineup research.

The runner sends the pack briefing plus the authoritative candidates ledger to
the existing Google-Search-grounded Gemini call path. Output is constrained to
machine-validated records whose market identifiers and quoted fields exactly
match candidates.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Sequence

from dateutil import parser as date_parser

from outlier_scrapers import gemini_research, pack, pack_index, paths, verdicts
from outlier_scrapers import runner_common as rc
from outlier_scrapers.verdict_gate import validate_envelope

logger = logging.getLogger(__name__)

MODEL = gemini_research.MODEL
GROUNDING = gemini_research.GROUNDING
OUT_NAME = "chatgpt_c.md"
PROMPT_FILE = "C.md"
NO_FINDINGS = "NO_SOURCED_FINDINGS"
RECORD_PREFIX = "FINDING | "
REQUIRED_FIELDS = {
    "market_id",
    "selection",
    "line",
    "price",
    "verdict",
    "claim",
    "source_name",
    "source_tier",
    "source_timestamp",
}


def call_gemini(prompt_text: str, role_block: list[str], research_input: str, client=None) -> str:
    """Reuse Prompt B's grounded Gemini call and custom 429 backoff."""
    return gemini_research.call_gemini(prompt_text, role_block, research_input, client=client)


def _market_index(
    raw_bytes: bytes,
    totals_bytes: bytes | None,
    team_totals_bytes: bytes | None = None,
) -> dict[str, dict[str, str]]:
    """Legacy market_id map. Kept for callers/tests that still pass a dict."""
    text = raw_bytes.decode("utf-8-sig")
    rows = csv.DictReader(StringIO(text))
    index = {row["market_id"]: row for row in rows if row.get("market_id")}
    for row in rc.parse_game_totals(totals_bytes):
        totals_id = str(row.get("totals_id") or "").strip()
        if totals_id:
            index[totals_id] = row
    for row in rc.parse_team_totals(team_totals_bytes):
        totals_id = str(row.get("totals_id") or "").strip()
        if totals_id:
            index[totals_id] = row
    return index


def _dummy_hash() -> str:
    return "0" * 64


def _index_from_candidate_map(
    candidates: dict[str, dict[str, str]],
    *,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
) -> pack_index.PackIndex:
    from outlier_scrapers.portfolio import PortfolioPolicy

    rows: dict[str, pack_index.IndexedRow] = {}
    for key, data in candidates.items():
        outcome_id = str(data.get("outcome_id") or key)
        market_id = str(data.get("market_id") or key)
        stream = str(data.get("stream") or "candidates")
        row_data = dict(data)
        row_data.setdefault("market_id", market_id)
        row_data.setdefault("outcome_id", outcome_id)
        if stream != "candidates":
            row_data.setdefault("totals_id", key)
        rows[outcome_id] = pack_index.IndexedRow(
            stream=stream, market_id=market_id, outcome_id=outcome_id, data=row_data
        )
    return pack_index.PackIndex(
        candidates_sha256=candidates_sha256,
        game_totals_sha256=game_totals_sha256,
        team_totals_sha256=team_totals_sha256,
        rows=rows,
        dropped={},
        unindexed_totals=(),
        players={},
        injuries={},
        locks={},
        policy=PortfolioPolicy(),
    )


def _lookup_indexed_row(index: pack_index.PackIndex, market_id: str):
    if market_id in index.rows:
        return index.rows[market_id]
    for row in index.rows.values():
        if row.market_id == market_id or str(row.data.get("totals_id") or "") == market_id:
            return row
    return None


def _finding_from_pipe_fields(
    fields: dict[str, str], index: pack_index.PackIndex
) -> dict[str, object]:
    market_id = fields["market_id"]
    row = _lookup_indexed_row(index, market_id)
    outcome_id = fields.get("outcome_id") or (row.outcome_id if row else market_id)
    stream = fields.get("stream") or (row.stream if row else "candidates")
    try:
        tier = int(fields["source_tier"])
    except ValueError as exc:
        raise rc.RunnerError("Prompt C output line has an invalid source tier") from exc
    return {
        "market_id": market_id,
        "outcome_id": outcome_id,
        "stream": stream,
        "selection": fields["selection"],
        "line": fields["line"],
        "price": fields["price"],
        "verdict": fields["verdict"],
        "claim": fields["claim"],
        "source_name": fields["source_name"],
        "source_tier": tier,
        "source_timestamp": fields["source_timestamp"],
        "evidence": [],
    }


def _pipe_to_envelope(
    output_text: str,
    pack_date_str: str,
    index: pack_index.PackIndex,
) -> str:
    findings = []
    for line_number, line in enumerate(output_text.strip().splitlines(), start=1):
        if not line.startswith(RECORD_PREFIX):
            raise rc.RunnerError(f"Prompt C output line {line_number} is not a FINDING record")
        fields: dict[str, str] = {}
        for part in line.split(" | ")[1:]:
            if "=" not in part:
                raise rc.RunnerError(f"Prompt C output line {line_number} has a malformed field")
            key, value = part.split("=", 1)
            if key in fields:
                raise rc.RunnerError(f"Prompt C output line {line_number} repeats field {key}")
            fields[key] = value
        if not REQUIRED_FIELDS.issubset(fields):
            raise rc.RunnerError(f"Prompt C output line {line_number} has invalid fields")
        try:
            date_parser.parse(fields["source_timestamp"])
        except (ValueError, OverflowError, TypeError) as exc:
            raise rc.RunnerError(
                f"Prompt C output line {line_number} has unparseable timestamp: "
                f"{fields['source_timestamp']}"
            ) from exc
        findings.append(_finding_from_pipe_fields(fields, index))
    payload = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pass": "C",
        "pack_date": pack_date_str,
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "findings": findings,
        "no_sourced_findings": not findings,
    }
    return json.dumps(payload)


def _raise_for_gate(result) -> None:
    if not result.violations:
        return
    for item in result.violations:
        if item.severity != "reject":
            continue
        if item.code == "unknown_market":
            raise rc.RunnerError(f"Prompt C output references unknown market_id {item.market_id}")
        if item.code == "line_tampered":
            raise rc.RunnerError(f"Prompt C output changed line for market_id {item.market_id}")
        if item.code == "selection_tampered":
            raise rc.RunnerError(
                f"Prompt C output changed selection for market_id {item.market_id}"
            )
        if item.code == "price_tampered":
            raise rc.RunnerError(f"Prompt C output changed price for market_id {item.market_id}")
        if item.code == "unparseable_timestamp":
            raise rc.RunnerError(f"Prompt C output line has unparseable timestamp: {item.detail}")
        raise rc.RunnerError(f"Prompt C output failed gate {item.code}: {item.detail}")
    if result.pass_fails:
        raise rc.RunnerError("Prompt C output failed structured validation")


def validate_output(
    output_text: str,
    candidates: dict[str, dict[str, str]] | pack_index.PackIndex,
    pack_date_str: str,
    *,
    now: datetime | None = None,
) -> None:
    """Reject output that is unstructured, changes an authoritative quote, or has out-of-bounds dates."""
    stripped = output_text.strip()
    if stripped == NO_FINDINGS:
        return
    if not stripped:
        raise rc.RunnerError("Received empty or whitespace-only response from API")

    if isinstance(candidates, pack_index.PackIndex):
        index = candidates
    else:
        index = _index_from_candidate_map(
            candidates,
            candidates_sha256=_dummy_hash(),
            game_totals_sha256=_dummy_hash(),
            team_totals_sha256=_dummy_hash(),
        )

    if stripped.lstrip().startswith("{"):
        raw = stripped
    else:
        raw = _pipe_to_envelope(stripped, pack_date_str, index)

    try:
        parsed = rc.parse_envelope(raw, "finding")
    except verdicts.EnvelopeUnparseableError as exc:
        raise rc.RunnerError("Prompt C output is not a FINDING record or JSON envelope") from exc
    except verdicts.SchemaInvalidError as exc:
        raise rc.RunnerError(f"Prompt C output failed schema validation: {exc}") from exc

    when = now or datetime.now().astimezone()
    _raise_for_gate(validate_envelope(parsed, index, when))


def run_c_research(
    pack_dir: Path, *, force: bool = False, refresh_if_stale: bool = False, client=None
) -> int:
    try:
        out_file = pack_dir / OUT_NAME
        if out_file.exists() and not force and not refresh_if_stale:
            logger.info("Output exists. Clean no-op.")
            return 0

        briefing_text = rc.read_required_text(pack_dir / "briefing.md", "Briefing")
        briefing_sha256 = rc.sha256_text(briefing_text)
        totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256 = (
            rc.load_all_totals(pack_dir)
        )
        candidates_bytes, candidates_sha256 = rc.validate_candidates(
            pack_dir,
            allow_empty=rc.has_actionable_any_totals(totals_bytes, team_totals_bytes),
        )
        index = pack_index.build_pack_index(
            pack_dir, policy_path=paths.PROJECT_ROOT / "missing-portfolio-policy.json"
        )
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "grounding": GROUNDING,
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "briefing_hash": briefing_sha256,
                "candidates_hash": candidates_sha256,
                "game_totals_hash": game_totals_sha256,
                "team_totals_hash": team_totals_sha256,
                **rc.structured_request_fields(
                    candidates_sha256=candidates_sha256,
                    game_totals_sha256=game_totals_sha256,
                    team_totals_sha256=team_totals_sha256,
                ),
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0

        research_input = rc.append_totals_block(
            "Pack briefing:\n"
            + briefing_text
            + "\n\n"
            + f"pack_date: {pack_dir.name}\n"
            + f"candidates_sha256: {candidates_sha256}\n"
            + f"game_totals_sha256: {game_totals_sha256}\n"
            + f"team_totals_sha256: {team_totals_sha256}\n"
            + "\nAuthoritative candidates.csv:\n"
            + candidates_bytes.decode("utf-8-sig"),
            totals_bytes,
            team_totals_bytes,
        )
        logger.info("Calling Gemini (Prompt C injury/lineup research)...")
        output_text = call_gemini(prompt_text, pack.ROLE_BLOCK, research_input, client=client)
        validate_output(output_text, index, pack_dir.name)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"grounding: {GROUNDING}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"briefing_sha256: {briefing_sha256}\n"
            f"candidates_sha256: {candidates_sha256}\n"
            f"request_sha256: {request_sha256}\n"
            "---\n\n"
        )
        rc.atomic_write(pack_dir, OUT_NAME, front_matter, output_text)
        logger.info("Successfully wrote %s", OUT_NAME)
        return 0
    except rc.RunnerError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error("Unexpected runner error: %s", type(e).__name__)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prompt C injury/lineup research runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_c_research(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
