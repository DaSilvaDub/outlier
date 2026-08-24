"""Claude Prompt E runner — reconciliation pass.

Combines validated A/B/D (and optional C) publications into a reconciliation
envelope. Output is claude_e.md plus verdicts/E/<publication_id>.
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
from outlier_scrapers.models import CLAUDE_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = CLAUDE_MODEL
EFFORT = "high"
MAX_TOKENS = 8192
OUT_NAME = "claude_e.md"
PROMPT_FILE = "E.md"

# A/D/B are the verdict passes E reconciles; C is optional supporting research.
# E is fed their *validated published envelopes*, never their Markdown: the
# Markdown is prose that was never gate-checked, so synthesising from it lets an
# upstream fabrication reach the final report even when the upstream pass itself
# validated clean. The envelopes also carry the publication_id / record_id pairs
# E must cite -- it cannot invent those.
REQUIRED_UPSTREAM = ("A", "D", "B")
OPTIONAL_UPSTREAM = ("C",)

# briefing.md is narrative context only. No number or identity may be sourced
# from it; those come from the pack index via the gate.
REQUIRED_INPUTS = {"briefing": "briefing.md"}
OPTIONAL_INPUTS = {
    "game_totals": rc.GAME_TOTALS_NAME,
    "team_totals": rc.TEAM_TOTALS_NAME,
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


def gather_upstream(pack_dir: Path) -> dict[str, rc.PublishedEnvelope]:
    """Return the current published envelope for each upstream pass.

    A/D/B must all be published; E reconciles them and cannot run against a
    partial desk. C is included when present.
    """
    documents = rc.load_current_publication_documents(pack_dir)
    missing = [name for name in REQUIRED_UPSTREAM if name not in documents]
    if missing:
        raise rc.RunnerError(
            "Pass E requires published verdict envelopes for "
            f"{', '.join(REQUIRED_UPSTREAM)}; missing: {', '.join(missing)}. "
            "Run those passes first."
        )
    selected = {name: documents[name] for name in REQUIRED_UPSTREAM}
    for name in OPTIONAL_UPSTREAM:
        if name in documents:
            selected[name] = documents[name]
    return selected


def build_user_content(
    prompt_text: str,
    inputs: dict[str, str],
    extra: str = "",
    upstream: dict[str, rc.PublishedEnvelope] | None = None,
) -> str:
    sections = [prompt_text]
    if extra:
        sections.append("\n\n===== PACK IDENTITY =====\n" + extra)
    for name, published in (upstream or {}).items():
        sections.append(
            f"\n\n===== UPSTREAM PASS {name} "
            f"(publication_id: {published.publication_id}) =====\n"
            f"{published.envelope_json}"
        )
    for label, text in inputs.items():
        sections.append(f"\n\n===== {label.upper()} =====\n{text}")
    return "".join(sections)


def call_claude(user_content: str, role_block: list[str], client=None) -> str:
    import anthropic

    if client is None:
        load_environment()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(timeout=600.0, max_retries=1)
    structured = rc.request_structured("reconciliation")
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system="\n".join(role_block),
            messages=[{"role": "user", "content": user_content}],
            tools=[{"name": "emit_reconciliations", "input_schema": structured.schema}],
            tool_choice={"type": "tool", "name": "emit_reconciliations"},
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
    for block in message.content:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "emit_reconciliations"
        ):
            return json.dumps(getattr(block, "input", {}))
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
        upstream = gather_upstream(pack_dir)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )
        totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256 = (
            rc.load_all_totals(pack_dir)
        )
        _candidates_bytes, candidates_sha256 = rc.validate_candidates(
            pack_dir,
            allow_empty=rc.has_actionable_any_totals(totals_bytes, team_totals_bytes),
        )

        input_hashes = {label: rc.sha256_text(text) for label, text in inputs.items()}
        # Upstream identity is the publication_id, not a hash of prose: a forced
        # rerun can produce a different response under an unchanged request hash,
        # and E must re-run when the publication it reconciles actually changes.
        # All four keys, always: the reconciliation schema requires A/D/B/C with C
        # nullable, and E.md tells the model to echo this object verbatim. Dropping
        # the key when C did not run made a faithful echo fail schema validation.
        upstream_publication_ids: dict[str, str | None] = {
            name: upstream[name].publication_id if name in upstream else None
            for name in (*REQUIRED_UPSTREAM, *OPTIONAL_UPSTREAM)
        }
        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "effort": EFFORT,
                "thinking": "adaptive",
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "input_hashes": input_hashes,
                "upstream_publication_ids": upstream_publication_ids,
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
            out_file.unlink()

        logger.info("Calling Claude (Prompt E synthesis)...")
        identity = (
            rc.build_pack_identity_block(
                pack_date=pack_dir.name,
                candidates_sha256=candidates_sha256,
                game_totals_sha256=game_totals_sha256,
                team_totals_sha256=team_totals_sha256,
            )
            + "upstream_publication_ids: "
            + json.dumps(upstream_publication_ids, sort_keys=True)
            + "\n"
        )
        user_content = build_user_content(
            prompt_text, inputs, extra=identity, upstream=upstream
        )
        output_text = call_claude(user_content, pack.ROLE_BLOCK, client=client)
        rc.publish_reconciliation_pass(
            pack_dir,
            output_text,
            request_sha256=request_sha256,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
            model=MODEL,
        )

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
