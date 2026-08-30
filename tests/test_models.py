import dataclasses

from outlier_scrapers import models


def test_request_fields_include_content_affecting_keys_only():
    fields = models.PASS_A_CONFIG.request_fields()
    assert fields == {
        "provider": "openai",
        "model": models.OPENAI_MODEL,
        "reasoning_effort": models.OPENAI_EFFORT,
    }
    # Execution policy (attempts/timeouts/backoff) must not leak into the hash.
    assert "max_attempts" not in fields
    assert "timeout_seconds" not in fields


def test_b_and_c_share_model_but_not_execution_policy():
    assert models.PASS_B_CONFIG.model == models.PASS_C_CONFIG.model == models.GEMINI_MODEL
    assert models.PASS_B_CONFIG != models.PASS_C_CONFIG
    assert models.PASS_B_CONFIG.max_attempts != models.PASS_C_CONFIG.max_attempts


def test_with_overrides_returns_a_new_immutable_config():
    changed = models.PASS_A_CONFIG.with_overrides(model="gpt-9.9")
    assert changed.model == "gpt-9.9"
    assert models.PASS_A_CONFIG.model == models.OPENAI_MODEL
    assert changed == dataclasses.replace(models.PASS_A_CONFIG, model="gpt-9.9")
