"""Offline probe of grounded Gemini structured-output config construction."""

from outlier_scrapers import gemini_structured


def test_construction_probe_records_installed_sdk_without_live_call():
    result = gemini_structured.run_construction_probe()
    assert tuple(int(part) for part in result.sdk_version.split(".")[:2]) >= (2, 10)
    assert result.has_response_mime_type
    assert result.has_response_schema
    assert result.has_response_json_schema
    assert result.construct_json_schema_with_search == "OK"
    assert result.construct_schema_with_search == "OK"
    assert result.live_generate_invoked is False
    assert result.recommendation == "attempt_then_fallback"


def test_build_grounded_config_prefers_json_schema_when_constructable():
    config, mode = gemini_structured.build_grounded_config(
        role_block=["role"],
        max_output_tokens=128,
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    )
    assert mode == "response_json_schema"
    assert config.response_mime_type == "application/json"
    assert config.tools


def test_build_grounded_config_without_schema_is_prompt_only():
    config, mode = gemini_structured.build_grounded_config(
        role_block=["role"],
        max_output_tokens=128,
        schema=None,
    )
    assert mode == "prompt_only"
    assert config.response_mime_type is None
    assert config.tools


def test_looks_like_structured_config_rejection():
    assert gemini_structured.looks_like_structured_config_rejection(
        ValueError("response_json_schema is not supported with google_search")
    )
    assert not gemini_structured.looks_like_structured_config_rejection(RuntimeError("429 Too Many Requests"))
