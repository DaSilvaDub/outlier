import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from outlier_scrapers import pack, pack_index, paths, provider_executor, verdicts
from outlier_scrapers import runner_common as rc
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import PASS_A_CONFIG
from outlier_scrapers.verdict_gate import VerdictPolicy, load_verdict_policy, validate_envelope

logger = logging.getLogger(__name__)

CONFIG = PASS_A_CONFIG


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


def _call_openai_responses_api_once(
    client,
    prompt_text: str,
    role_block: list[str],
    full_prompt: str,
) -> str:
    """Issue exactly one Responses API attempt. Retries are the caller's job."""
    import openai

    structured = rc.request_structured("verdict")
    try:
        response = client.responses.create(
            model=CONFIG.model,
            reasoning={"effort": CONFIG.request_extra["reasoning_effort"]},
            max_output_tokens=32_000,
            store=False,
            instructions="\n".join(role_block),
            input=[{"role": "user", "content": full_prompt}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "outlier_verdicts",
                    "schema": structured.schema,
                    "strict": True,
                }
            },
        )
    except openai.RateLimitError as e:
        req_id = getattr(e, "request_id", None)
        status = getattr(e, "status_code", None)
        raise provider_executor.ProviderCallError(
            f"API call failed: type={type(e).__name__} status={status} request_id={req_id}",
            retryable=True,
        ) from e
    except openai.APIError as e:
        req_id = getattr(e, "request_id", None)
        status = getattr(e, "status_code", None)
        raise ReasoningError(
            f"API call failed: type={type(e).__name__} status={status} request_id={req_id}"
        ) from e
    except Exception as e:
        raise ReasoningError(f"API call failed: type={type(e).__name__}") from e

    if not response.output_text or not response.output_text.strip():
        raise ReasoningError("Received empty or whitespace-only response from API")
    return response.output_text


def call_openai_responses_api(
    prompt_text: str,
    role_block: list[str],
    raw_csv_bytes: bytes,
    totals_bytes: bytes | None = None,
    team_totals_bytes: bytes | None = None,
    client=None,
    identity_block: str = "",
) -> str:
    import openai

    if client is None:
        load_environment()
        if not os.getenv("OPENAI_API_KEY"):
            raise ReasoningError("OPENAI_API_KEY is not set.")
        # provider_executor now owns retries; the SDK's own retry loop would
        # otherwise stack with it (up to max_attempts * sdk_retries calls).
        client = openai.OpenAI(timeout=CONFIG.timeout_seconds, max_retries=0)

    data_block = rc.build_reasoning_data_block(
        raw_csv_bytes, totals_bytes, team_totals_bytes
    )
    full_prompt = prompt_text + "\n\n" + identity_block + "\nData:\n" + data_block

    try:
        return provider_executor.execute_with_retry(
            lambda: _call_openai_responses_api_once(client, prompt_text, role_block, full_prompt),
            max_attempts=CONFIG.max_attempts,
            base_delay_seconds=CONFIG.base_delay_seconds,
            max_delay_seconds=CONFIG.max_delay_seconds,
        )
    except provider_executor.ProviderCallError as e:
        raise ReasoningError(str(e)) from e


def _report_fragment(envelope: verdicts.VerdictEnvelope) -> bytes:
    lines = ["# Pass A", ""]
    for record in envelope.verdicts:
        lines.append(
            f"- {record.verdict} {record.selection} {record.line} {record.price} "
            f"({record.recommended_units}u)"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _status_fragment(
    envelope: verdicts.VerdictEnvelope, gate, policy: VerdictPolicy
) -> bytes:
    codes: dict[str, int] = {}
    for item in gate.violations:
        codes[item.code] = codes.get(item.code, 0) + 1
    payload = {
        "envelope_present": True,
        "envelope_kind": "verdict",
        "schema_version": envelope.schema_version,
        "record_count": len(envelope.verdicts),
        "bet_count": sum(1 for record in envelope.verdicts if record.verdict == "BET"),
        "rejected_count": sum(1 for item in gate.violations if item.severity == "reject"),
        "violation_codes": codes,
        "mode": policy.mode,
        "repair_attempts": 0,
        "structured_output_native": True,
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _publish_pass_a(
    pack_dir: Path,
    output_text: str,
    *,
    request_sha256: str,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
    policy: VerdictPolicy | None = None,
) -> None:
    try:
        parsed = rc.parse_envelope(output_text, "verdict")
    except (verdicts.EnvelopeUnparseableError, verdicts.SchemaInvalidError) as exc:
        raise ReasoningError(f"Pass A output is not a valid verdict envelope: {exc}") from exc
    if not isinstance(parsed.envelope, verdicts.VerdictEnvelope):
        raise ReasoningError("Pass A output did not parse as a verdict envelope")
    index = pack_index.build_pack_index(pack_dir)
    verdict_policy = policy or load_verdict_policy(
        paths.PROJECT_ROOT / "config" / "verdict_policy.json"
    )
    gate = validate_envelope(
        parsed,
        index,
        datetime.now().astimezone(),
        policy=verdict_policy,
    )
    if gate.pass_fails:
        raise ReasoningError(
            "Pass A failed structured validation: " + ",".join(gate.fail_reasons)
        )
    artifacts = rc.PassArtifacts(
        pass_="A",
        request_sha256=request_sha256,
        schema_version=verdicts.SCHEMA_VERSION,
        verdicts_json=rc.write_envelope(
            parsed.envelope,
            request_sha256=request_sha256,
            model=CONFIG.model,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
        ),
        violations_json=rc.write_violations(gate.violations),
        report_fragment=_report_fragment(parsed.envelope),
        status_fragment=_status_fragment(parsed.envelope, gate, verdict_policy),
    )
    rc.publish_pass(pack_dir, artifacts)


def provider_configuration() -> dict[str, object]:
    return {
        "provider": CONFIG.provider,
        "model": CONFIG.model,
        "reasoning_effort": CONFIG.request_extra["reasoning_effort"],
        "response_schema_version": verdicts.SCHEMA_VERSION,
    }


def expected_request_sha256(pack_dir: Path) -> str:
    totals_bytes, game_hash, team_totals_bytes, team_hash = rc.load_all_totals(pack_dir)
    _, candidates_hash = rc.validate_candidates(
        pack_dir,
        allow_empty=rc.has_actionable_any_totals(totals_bytes, team_totals_bytes),
    )
    prompt_text = rc.read_required_text(paths.PROJECT_ROOT / "prompts" / "A.md", "Prompt file")
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

        totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256 = (
            rc.load_all_totals(pack_dir)
        )
        raw_bytes, candidates_sha256 = rc.validate_candidates(
            pack_dir,
            allow_empty=rc.has_actionable_any_totals(totals_bytes, team_totals_bytes),
        )

        prompt_file = paths.PROJECT_ROOT / "prompts" / "A.md"
        if not prompt_file.exists():
            raise ReasoningError(f"Prompt file {prompt_file} missing.")

        prompt_text = prompt_file.read_text(encoding="utf-8")

        request_data = {
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
        request_sha256 = rc.compute_request_hash(request_data)

        if out_file.exists():
            if refresh_if_stale:
                content = out_file.read_text(encoding="utf-8")
                existing_hash = extract_yaml_request_hash(content)
                if existing_hash == request_sha256:
                    logger.info("Output exists and matches hash. Skipping.")
                    return 0
                logger.info("Hash mismatch. Re-running.")
                out_file.unlink()

        logger.info("Calling OpenAI Responses API...")
        output_text = call_openai_responses_api(
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
        _publish_pass_a(
            pack_dir,
            output_text,
            request_sha256=request_sha256,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
        )

        utc_timestamp = datetime.now(timezone.utc).isoformat()

        front_matter = (
            "---\n"
            f"model: {CONFIG.model}\n"
            f"effort: {CONFIG.request_extra['reasoning_effort']}\n"
            f"timestamp: {utc_timestamp}\n"
            f"candidates_sha256: {candidates_sha256}\n"
            f"game_totals_sha256: {game_totals_sha256}\n"
            f"team_totals_sha256: {team_totals_sha256}\n"
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
        logger.error(f"Unexpected runner error: {type(e).__name__}")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pass A runner for Outlier AI research desk.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)

    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_reasoning(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
