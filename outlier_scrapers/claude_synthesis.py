"""Claude Prompt E runner — synthesis pass (inputs: briefing + A/B/D outputs).

Combines the prior structured outputs into the final guide via Claude Opus 4.8.
A (chatgpt_a.md), B (gemini_b.md), D (claude_d.md), and briefing.md are
required; C (chatgpt_c.md, manual Deep Research) is included when present.
Output is ``claude_e.md`` with hash-based idempotency over every input.
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
MAX_TOKENS = 8192
OUT_NAME = "claude_e.md"
PROMPT_FILE = "E.md"

REQUIRED_INPUTS = {
    "briefing": "briefing.md",
    "prompt_a": "chatgpt_a.md",
    "prompt_b": "gemini_b.md",
    "prompt_d": "claude_d.md",
}
OPTIONAL_INPUTS = {
    "prompt_c": "chatgpt_c.md",
    "game_totals": rc.GAME_TOTALS_NAME,
}


def gather_inputs(pack_dir: Path) -> dict[str, str]:
    """Return {label: text} for all required (and any present optional) inputs."""
    collected: dict[str, str] = {}
    for label, fname in REQUIRED_INPUTS.items():
        collected[label] = rc.read_required_text(pack_dir / fname, f"Required input '{label}'")
    for label, fname in OPTIONAL_INPUTS.items():
        path = pack_dir / fname
        if path.exists():
            collected[label] = path.read_text(encoding="utf-8")
    return collected


def build_user_content(prompt_text: str, inputs: dict[str, str]) -> str:
    sections = [prompt_text]
    for label, text in inputs.items():
        sections.append(f"\n\n===== {label.upper()} =====\n{text}")
    return "".join(sections)


def call_claude(user_content: str, role_block: list[str], client=None) -> str:
    if client is None:
        load_environment()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(timeout=600.0, max_retries=1)
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system="\n".join(role_block),
            messages=[{"role": "user", "content": user_content}],
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


def run_claude_e(
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

        inputs = gather_inputs(pack_dir)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        input_hashes = {label: rc.sha256_text(text) for label, text in inputs.items()}
        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "effort": EFFORT,
                "thinking": "adaptive",
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "input_hashes": input_hashes,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Claude (Prompt E synthesis)...")
        user_content = build_user_content(prompt_text, inputs)
        output_text = call_claude(user_content, pack.ROLE_BLOCK, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"effort: {EFFORT}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"inputs: {','.join(sorted(inputs))}\n"
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
    parser = argparse.ArgumentParser(description="Claude Prompt E synthesis runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_claude_e(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
