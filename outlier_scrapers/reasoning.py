import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import time

import openai

from outlier_scrapers import paths, pack
from outlier_scrapers import runner_common as rc
from outlier_scrapers.environment import load_environment

logger = logging.getLogger(__name__)

MODEL = "gpt-5.5"
EFFORT = "xhigh"


class ReasoningError(Exception):
    pass


def extract_yaml_request_hash(content: str) -> str | None:
    """Hand-rolled trivial YAML reader for the request_sha256 key."""
    if not content.startswith("---\n"):
        return None

    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None

    front_matter = content[4:end_idx]
    for line in front_matter.splitlines():
        if line.startswith("request_sha256:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def call_openai_responses_api(
    prompt_text: str,
    role_block: list[str],
    raw_csv_bytes: bytes,
    totals_bytes: bytes | None = None,
    client=None,
) -> str:
    if client is None:
        load_environment()
        if not os.getenv("OPENAI_API_KEY"):
            raise ReasoningError("OPENAI_API_KEY is not set.")
        client = openai.OpenAI(timeout=600.0, max_retries=10)

    data_block = rc.build_reasoning_data_block(raw_csv_bytes, totals_bytes)
    full_prompt = prompt_text + "\n\nData:\n" + data_block

    max_custom_retries = 10
    for attempt in range(max_custom_retries):
        try:
            response = client.responses.create(
                model=MODEL,
                reasoning={"effort": EFFORT},
                max_output_tokens=32_000,
                store=False,
                instructions="\n".join(role_block),
                input=[{"role": "user", "content": full_prompt}],
            )
            if not response.output_text or not response.output_text.strip():
                raise ReasoningError("Received empty or whitespace-only response from API")
            return response.output_text
        except openai.RateLimitError as e:
            if attempt < max_custom_retries - 1:
                logger.warning("OpenAI API rate limited, retrying in 30s...")
                time.sleep(30)
                continue
            req_id = getattr(e, "request_id", None)
            status = getattr(e, "status_code", None)
            raise ReasoningError(
                f"API call failed: type={type(e).__name__} status={status} request_id={req_id}"
            )
        except openai.APIError as e:
            req_id = getattr(e, "request_id", None)
            status = getattr(e, "status_code", None)
            raise ReasoningError(
                f"API call failed: type={type(e).__name__} status={status} request_id={req_id}"
            )
        except Exception as e:
            raise ReasoningError(f"API call failed: type={type(e).__name__}")


def run_reasoning(
    pack_dir: Path,
    *,
    force: bool = False,
    refresh_if_stale: bool = False,
    client=None,
) -> int:
    try:
        out_file = pack_dir / "chatgpt_a.md"

        if out_file.exists():
            if force:
                logger.info("Forcing re-run, removing existing output.")
                out_file.unlink()
            elif not refresh_if_stale:
                logger.info("Output exists. Clean no-op.")
                return 0

        raw_bytes, candidates_sha256 = rc.validate_candidates(pack_dir)
        totals_bytes, game_totals_sha256 = rc.load_game_totals(pack_dir)

        prompt_file = paths.PROJECT_ROOT / "prompts" / "A.md"
        if not prompt_file.exists():
            raise ReasoningError(f"Prompt file {prompt_file} missing.")

        prompt_text = prompt_file.read_text(encoding="utf-8")

        request_data = {
            "model": MODEL,
            "reasoning": {"effort": EFFORT},
            "role_block": pack.ROLE_BLOCK,
            "prompt": prompt_text,
            "candidates_hash": candidates_sha256,
            "game_totals_hash": game_totals_sha256,
        }
        canonical_json = json.dumps(request_data, sort_keys=True).encode("utf-8")
        request_sha256 = hashlib.sha256(canonical_json).hexdigest()

        if out_file.exists():
            if refresh_if_stale:
                content = out_file.read_text(encoding="utf-8")
                existing_hash = extract_yaml_request_hash(content)
                if existing_hash == request_sha256:
                    logger.info("Output exists and matches hash. Skipping.")
                    return 0
                else:
                    logger.info("Hash mismatch. Re-running reasoning.")
                    out_file.unlink()

        logger.info("Calling OpenAI Responses API...")
        output_text = call_openai_responses_api(
            prompt_text, pack.ROLE_BLOCK, raw_bytes, totals_bytes, client=client
        )

        utc_timestamp = datetime.now(timezone.utc).isoformat()

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"effort: {EFFORT}\n"
            f"timestamp: {utc_timestamp}\n"
            f"candidates_sha256: {candidates_sha256}\n"
            f"game_totals_sha256: {game_totals_sha256}\n"
            f"request_sha256: {request_sha256}\n"
            "---\n\n"
        )

        fd, tmp_path = tempfile.mkstemp(dir=str(pack_dir), prefix="chatgpt_a_tmp_", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(front_matter)
                f.write(output_text)
            os.replace(tmp_path, out_file)
            logger.info(f"Successfully wrote {out_file.name}")
        except Exception as e:
            os.unlink(tmp_path)
            raise ReasoningError(f"Failed to write output: {type(e).__name__}")

        return 0

    except (ReasoningError, rc.RunnerError) as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error(f"Unexpected reasoning error: {type(e).__name__}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reasoning runner for Outlier AI research desk.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)

    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_reasoning(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
