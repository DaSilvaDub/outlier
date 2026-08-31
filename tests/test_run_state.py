from __future__ import annotations

from outlier_scrapers.run_state import (
    DailyExitCode,
    PipelineStatus,
    ReasoningOverall,
    exit_code_for_overall,
    is_successful_overall,
    legacy_overall_string,
    parse_reasoning_overall,
)


def test_pipeline_and_reasoning_enums_are_separate() -> None:
    assert PipelineStatus.OK.value == "ok"
    assert ReasoningOverall.FULL.value == "FULL"
    assert PipelineStatus.FAILED is not ReasoningOverall.FAILED


def test_legacy_overall_mapping_preserves_exit_contract() -> None:
    assert parse_reasoning_overall("PARTIAL") is ReasoningOverall.PARTIAL
    assert parse_reasoning_overall("partial") is ReasoningOverall.PARTIAL
    assert parse_reasoning_overall("FULL") is ReasoningOverall.FULL
    assert parse_reasoning_overall("DATA_ONLY") is ReasoningOverall.DATA_ONLY
    assert parse_reasoning_overall("error") is ReasoningOverall.FAILED
    assert legacy_overall_string(ReasoningOverall.PARTIAL) == "PARTIAL"
    assert is_successful_overall("PARTIAL")
    assert is_successful_overall("ok")
    assert is_successful_overall("degraded")
    assert not is_successful_overall("DATA_ONLY")
    assert not is_successful_overall("failed")
    assert exit_code_for_overall("FULL") == DailyExitCode.SUCCESS
    assert exit_code_for_overall("DATA_ONLY") == DailyExitCode.FATAL
    assert exit_code_for_overall("running") == DailyExitCode.FATAL
