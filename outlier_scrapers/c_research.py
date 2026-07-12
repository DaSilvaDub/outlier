"""Gemini Prompt C runner for current injury and lineup research.

The runner sends the pack briefing plus the authoritative candidates ledger to
the existing Google-Search-grounded Gemini call path. Output is constrained to
machine-validated records whose market identifiers and quoted fields exactly
match candidates.csv.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Sequence

from outlier_scrapers import gemini_research, pack, paths
from outlier_scrapers import runner_common as rc

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
    raw_bytes: bytes, totals_bytes: bytes | None
) -> dict[str, dict[str, str]]:
    text = raw_bytes.decode("utf-8-sig")
    rows = csv.DictReader(StringIO(text))
    index = {row["market_id"]: row for row in rows if row.get("market_id")}
    for row in rc.parse_game_totals(totals_bytes):
        totals_id = str(row.get("totals_id") or "").strip()
        if totals_id:
            index[totals_id] = row
    return index


def validate_output(output_text: str, candidates: dict[str, dict[str, str]], pack_date_str: str) -> None:
    """Reject output that is unstructured, changes an authoritative quote, or has out-of-bounds dates.
    
    Assumes the model never emits " | " inside a field value (this is prompt-enforced).
    """
    stripped = output_text.strip()
    if stripped == NO_FINDINGS:
        return
    if not stripped:
        raise rc.RunnerError("Received empty or whitespace-only response from API")

    for line_number, line in enumerate(stripped.splitlines(), start=1):
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

        if set(fields) != REQUIRED_FIELDS:
            raise rc.RunnerError(f"Prompt C output line {line_number} has invalid fields")

        market_id = fields["market_id"]
        candidate = candidates.get(market_id)
        if candidate is None:
            raise rc.RunnerError(f"Prompt C output references unknown market_id {market_id}")
        for field in ("selection", "line", "price"):
            if fields[field] != candidate[field]:
                raise rc.RunnerError(
                    f"Prompt C output changed {field} for market_id {market_id}"
                )
        if fields["verdict"] not in {"CONFIRMS", "CONTRADICTS", "NEUTRAL"}:
            raise rc.RunnerError(f"Prompt C output line {line_number} has an invalid verdict")
        if fields["source_tier"] not in {"1", "2", "3"}:
            raise rc.RunnerError(f"Prompt C output line {line_number} has an invalid source tier")
        for field in ("claim", "source_name", "source_timestamp"):
            if not fields[field].strip():
                raise rc.RunnerError(f"Prompt C output line {line_number} has an empty {field}")
        
        try:
            from datetime import datetime, timedelta
            pack_date = datetime.strptime(pack_date_str, "%Y-%m-%d").date()
            from dateutil import parser as date_parser
            ts = date_parser.parse(fields["source_timestamp"]).date()
            if not (pack_date - timedelta(days=2) <= ts <= pack_date + timedelta(days=1)):
                raise rc.RunnerError(
                    f"Prompt C output line {line_number} timestamp '{fields['source_timestamp']}' "
                    f"is outside the valid window for pack date {pack_date_str}"
                )
        except ValueError:
            pass # pack_date_str wasn't YYYY-MM-DD
        except Exception as e:
            if isinstance(e, rc.RunnerError):
                raise
            raise rc.RunnerError(f"Prompt C output line {line_number} has unparseable timestamp: {fields['source_timestamp']}")


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
        totals_bytes, game_totals_sha256 = rc.load_game_totals(pack_dir)
        candidates_bytes, candidates_sha256 = rc.validate_candidates(
            pack_dir, allow_empty=rc.has_actionable_game_totals(totals_bytes)
        )
        candidates = _market_index(candidates_bytes, totals_bytes)
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
            + "\n\nAuthoritative candidates.csv:\n"
            + candidates_bytes.decode("utf-8-sig"),
            totals_bytes,
        )
        logger.info("Calling Gemini (Prompt C injury/lineup research)...")
        output_text = call_gemini(prompt_text, pack.ROLE_BLOCK, research_input, client=client)
        validate_output(output_text, candidates, pack_dir.name)

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
