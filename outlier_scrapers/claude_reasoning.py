"""Claude Prompt D runner — pack-only verdict pass (input: candidates.csv).

Pack-only: no web tools. Output is claude_d.md plus a versioned verdicts/D
publication.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from outlier_scrapers import pack, paths
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import PASS_D_CONFIG
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

CONFIG = PASS_D_CONFIG
MAX_TOKENS = 8192
OUT_NAME = "claude_d.md"
PROMPT_FILE = "D.md"


def call_claude(
    prompt_text: str,
    role_block: list[str],
    raw_csv_bytes: bytes,
    totals_bytes: bytes | None = None,
    team_totals_bytes: bytes | None = None,
    client=None,
    identity_block: str = "",
) -> str:
    import anthropic

    if client is None:
        load_environment()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        # Retries are the Anthropic SDK's own transport-level policy here --
        # there is no hand-rolled sleep loop to move into provider_executor,
        # so CONFIG just supplies the values the client is constructed with.
        client = anthropic.Anthropic(timeout=CONFIG.timeout_seconds, max_retries=CONFIG.max_attempts)

    data_block = rc.build_reasoning_data_block(
        raw_csv_bytes, totals_bytes, team_totals_bytes
    )
    full_prompt = prompt_text + "\n\n" + identity_block + "\nData:\n" + data_block
    structured = rc.request_structured("verdict")
    try:
        with client.messages.stream(
            model=CONFIG.model,
            max_tokens=MAX_TOKENS,
            system="\n".join(role_block),
            messages=[{"role": "user", "content": full_prompt}],
            tools=[{"name": "emit_verdicts", "input_schema": structured.schema}],
            tool_choice={"type": "tool", "name": "emit_verdicts"},
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        error_body = e.response.text if hasattr(e, "response") else str(e)
        raise rc.RunnerError(
            f"API call failed: type={type(e).__name__} "
            f"status={getattr(e, 'status_code', None)} request_id={getattr(e, 'request_id', None)} "
            f"details={error_body}"
        )
    except Exception as e:
        raise rc.RunnerError(f"API call failed: type={type(e).__name__}")

    if getattr(message, "stop_reason", None) == "refusal":
        raise rc.RunnerError("Claude refused the request (stop_reason=refusal).")
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "emit_verdicts":
            return json.dumps(getattr(block, "input", {}))
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise rc.RunnerError("Received empty or whitespace-only response from API")
    return text


def provider_configuration() -> dict[str, object]:
    return {
        "provider": "anthropic",
        "model": CONFIG.model,
        "effort": CONFIG.request_extra["effort"],
        "thinking": CONFIG.request_extra["thinking"],
        "structured_kind": "verdict",
    }


def expected_request_sha256(pack_dir: Path) -> str:
    totals, game_hash, team_totals, team_hash = rc.load_all_totals(pack_dir)
    _, candidates_hash = rc.validate_candidates(
        pack_dir, allow_empty=rc.has_actionable_any_totals(totals, team_totals)
    )
    prompt_text = rc.read_required_text(
        paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
    )
    identity = rc.PackIdentity(pack_dir.name, candidates_hash, game_hash, team_hash)
    return rc.compute_request_hash(
        {
            **CONFIG.request_fields(),
            "role_block": pack.ROLE_BLOCK,
            "prompt": prompt_text,
            "candidates_hash": candidates_hash,
            "game_totals_hash": game_hash,
            "team_totals_hash": team_hash,
            **rc.structured_request_fields(identity),
        }
    )


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

        totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256 = (
            rc.load_all_totals(pack_dir)
        )
        raw_bytes, candidates_sha256 = rc.validate_candidates(
            pack_dir,
            allow_empty=rc.has_actionable_any_totals(totals_bytes, team_totals_bytes),
        )
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        request_sha256 = rc.compute_request_hash(
            {
                **CONFIG.request_fields(),
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "candidates_hash": candidates_sha256,
                "game_totals_hash": game_totals_sha256,
                "team_totals_hash": team_totals_sha256,
                **rc.structured_request_fields(
                    pack_date=pack_dir.name,
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
            out_file.unlink()

        logger.info("Calling Claude (Prompt D)...")
        output_text = call_claude(
            prompt_text,
            pack.ROLE_BLOCK,
            raw_bytes,
            totals_bytes,
            team_totals_bytes,
            client=client,
            identity_block=rc.build_pack_identity_block(
                pack_date=pack_dir.name,
                candidates_sha256=candidates_sha256,
                game_totals_sha256=game_totals_sha256,
                team_totals_sha256=team_totals_sha256,
            ),
        )
        rc.publish_verdict_pass(
            pack_dir,
            output_text,
            pass_="D",
            request_sha256=request_sha256,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
            model=CONFIG.model,
        )

        front_matter = (
            "---\n"
            f"model: {CONFIG.model}\n"
            f"effort: {CONFIG.request_extra['effort']}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"candidates_sha256: {candidates_sha256}\n"
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
    parser = argparse.ArgumentParser(description="Claude Prompt D runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_claude_d(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
