"""Offline probe and config builder for grounded Gemini structured output.

Step 7 of docs/plans/2026-08-12-structured-ai-verdicts.md. This module inspects
the installed google-genai SDK and constructs GenerateContentConfig objects. It
never calls generate_content and never opens a network connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROBE_DATE = "2026-08-14"
EXPECTED_SDK = "2.10.0"


@dataclass(frozen=True)
class GroundedStructuredProbe:
    sdk_version: str
    has_response_mime_type: bool
    has_response_schema: bool
    has_response_json_schema: bool
    construct_json_schema_with_search: str
    construct_schema_with_search: str
    live_generate_invoked: bool
    recommendation: str
    recorded_at: str


def _sdk_version() -> str:
    import google.genai

    return str(getattr(google.genai, "__version__", "unknown"))


def _tiny_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }


def _try_construct(*, with_search: bool, json_schema: bool) -> str:
    from google.genai import types

    kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
    }
    if json_schema:
        kwargs["response_json_schema"] = _tiny_schema()
    else:
        kwargs["response_schema"] = _tiny_schema()
    if with_search:
        kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    try:
        types.GenerateContentConfig(**kwargs)
    except Exception as exc:
        return f"FAIL {type(exc).__name__}: {exc}"
    return "OK"


def run_construction_probe() -> GroundedStructuredProbe:
    """Record what the installed SDK accepts when *constructing* a config.

    Construction success is not live-API acceptance. The recommendation is
    therefore always attempt-then-fallback.
    """
    from google.genai import types

    fields = getattr(types.GenerateContentConfig, "model_fields", None)
    params = set(fields) if fields is not None else set(dir(types.GenerateContentConfig))
    json_with_search = _try_construct(with_search=True, json_schema=True)
    schema_with_search = _try_construct(with_search=True, json_schema=False)
    return GroundedStructuredProbe(
        sdk_version=_sdk_version(),
        has_response_mime_type="response_mime_type" in params or "responseMimeType" in params,
        has_response_schema="response_schema" in params or "responseSchema" in params,
        has_response_json_schema="response_json_schema" in params or "responseJsonSchema" in params,
        construct_json_schema_with_search=json_with_search,
        construct_schema_with_search=schema_with_search,
        live_generate_invoked=False,
        recommendation="attempt_then_fallback",
        recorded_at=PROBE_DATE,
    )


def build_grounded_config(
    *,
    role_block: list[str],
    max_output_tokens: int,
    schema: dict[str, Any] | None = None,
    prefer_json_schema: bool = True,
) -> tuple[Any, str]:
    """Build a grounded GenerateContentConfig.

    Returns (config, mode) where mode is ``response_json_schema``,
    ``response_schema``, or ``prompt_only``. Construction failures fall back;
    this function does not call the API.
    """
    from google.genai import types

    tools: types.ToolListUnion = [types.Tool(google_search=types.GoogleSearch())]
    system_instruction = "\n".join(role_block)

    def prompt_only_config() -> Any:
        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            max_output_tokens=max_output_tokens,
        )

    if schema is None:
        return prompt_only_config(), "prompt_only"

    attempts = (
        ("response_json_schema", "response_schema")
        if prefer_json_schema
        else ("response_schema", "response_json_schema")
    )

    for mode in attempts:
        try:
            if mode == "response_json_schema":
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                )
            else:
                config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    response_mime_type="application/json",
                    response_schema=schema,
                )
            return config, mode
        except Exception:
            continue
    return prompt_only_config(), "prompt_only"


def looks_like_structured_config_rejection(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "response_schema",
        "responseschema",
        "response_json_schema",
        "responsejsonschema",
        "response_mime_type",
        "responsemimetype",
        "invalid argument",
        "cannot use",
        "not supported",
        "google_search",
    )
    return any(needle in text for needle in needles)
