"""Claude Prompt D runner — pack-only reasoning pass (input: candidates.csv).

Mirrors ``reasoning.py`` (Prompt A) but targets Claude Opus 4.8 via the
Anthropic SDK with adaptive thinking. Pack-only: no web tools. Output is
``claude_d.md`` with hash-based idempotency so the daily job can re-invoke it
without re-billing an unchanged request.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import anthropic

from outlier_scrapers import paths, pack
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import CLAUDE_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = CLAUDE_MODEL
EFFORT = "high"
MAX_TOKENS = 32_000
OUT_NAME = "claude_d.md"
PROMPT_FILE = "D.md"


def call_claude(prompt_text: str, role_block: list[str], raw_csv_bytes: bytes, client=None) -> str:
    if client is None:
        load_environment()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(timeout=600.0, max_retries=1)

    full_prompt = prompt_text + "\n\nData:\n" + raw_csv_bytes.decode("utf-8")
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            system="\n".join(role_block),
            messages=[{"role": "user", "content": full_prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise rc.RunnerError(
            f"API call failed: type={type(e).__name__} "
            f"status={getattr(e, 'status_code', None)} request_id={getattr(e, 'request_id', None)}"
        )
    except Exception as e:
        raise rc.RunnerError(f"API call failed: type={type(e).__name__}")

    if getattr(message, "stop_reason", None) == "refusal":
        raise rc.RunnerError("Claude refused the request (stop_reason=refusal).")
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise rc.RunnerError("Received empty or whitespace-only response from API")
    return text


def run_claude_d(
    pack_dir: Path, *, force: bool = False, refresh_if_stale: bool = False, client=None
) -> int:
    try:
        out_file = pack_dir / OUT_NAME
        if out_file.exists():
            if force:
                out_file.unlink()
            elif not refresh_if_stale:
                logger.info("Output exists. Clean no-op.")
                return 0

        raw_bytes, candidates_sha256 = rc.validate_candidates(pack_dir)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "effort": EFFORT,
                "thinking": "adaptive",
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "candidates_hash": candidates_sha256,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Claude (Prompt D)...")
        output_text = call_claude(prompt_text, pack.ROLE_BLOCK, raw_bytes, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"effort: {EFFORT}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
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
    parser = argparse.ArgumentParser(description="Claude Prompt D reasoning runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_claude_d(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
