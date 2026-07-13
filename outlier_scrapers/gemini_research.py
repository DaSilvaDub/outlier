"""Gemini Prompt B runner — wide-scan research pass (input: briefing.md).

Targets Gemini 3.1 Pro Preview via the google-genai SDK with Google Search
grounding (Prompt B is web-allowed). This automates a single grounded pass; it
is NOT the full Gemini Deep Research UI product (see plan caveats). Output is
``gemini_b.md`` with the same hash-based idempotency as the other runners.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import time

from google import genai
from google.genai import types

from outlier_scrapers import paths, pack
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import GEMINI_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = GEMINI_MODEL
MAX_TOKENS = 32_000
GROUNDING = "google_search"
OUT_NAME = "gemini_b.md"
PROMPT_FILE = "B.md"


def call_gemini(prompt_text: str, role_block: list[str], briefing_text: str, client=None) -> str:
    if client is None:
        load_environment()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise rc.RunnerError("GEMINI_API_KEY is not set.")
        client = genai.Client(api_key=api_key)

    full_prompt = prompt_text + "\n\nBriefing:\n" + briefing_text
    config = types.GenerateContentConfig(
        system_instruction="\n".join(role_block),
        tools=[types.Tool(google_search=types.GoogleSearch())],
        max_output_tokens=MAX_TOKENS,
    )
    max_retries = 10
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=full_prompt, config=config
            )
            text = getattr(response, "text", None) or ""
            if not text.strip():
                raise rc.RunnerError("Received empty or whitespace-only response from API")
            return text
        except Exception as e:
            if "429" in str(e) or "Too Many Requests" in str(e):
                if attempt < max_retries - 1:
                    logger.warning(f"Gemini API rate limited, retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                    continue
            raise rc.RunnerError(f"API call failed: type={type(e).__name__} {str(e)}")

    raise rc.RunnerError("Failed after maximum retries")



def run_gemini_b(
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

        briefing_text = rc.read_required_text(pack_dir / "briefing.md", "Briefing")
        totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256 = (
            rc.load_all_totals(pack_dir)
        )
        briefing_sha256 = rc.sha256_text(briefing_text)
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
                "game_totals_hash": game_totals_sha256,
                "team_totals_hash": team_totals_sha256,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Gemini (Prompt B)...")
        briefing_input = rc.append_totals_block(
            briefing_text, totals_bytes, team_totals_bytes
        )
        output_text = call_gemini(prompt_text, pack.ROLE_BLOCK, briefing_input, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"grounding: {GROUNDING}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"briefing_sha256: {briefing_sha256}\n"
            f"game_totals_sha256: {game_totals_sha256}\n"
            f"team_totals_sha256: {team_totals_sha256}\n"
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
    parser = argparse.ArgumentParser(description="Gemini Prompt B research runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_gemini_b(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
