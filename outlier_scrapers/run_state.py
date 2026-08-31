"""Separate pipeline vs reasoning status models.

Daily-job exit codes, pack-run health, and desk overall status used to share
the same string bucket. Keep the enums distinct and map legacy JSON on the
way in/out so existing consumers keep working.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class PipelineStatus(StrEnum):
    """Outcome of the deterministic pack/refresh pipeline."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReasoningOverall(StrEnum):
    """Desk publication completeness from reasoning_status.json."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    DATA_ONLY = "DATA_ONLY"
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    RUNNING = "running"


class DailyExitCode(IntEnum):
    SUCCESS = 0
    FATAL = 1
    LOCK_CONFLICT = 2


# Legacy strings that historically mixed pipeline + desk into one field.
_LEGACY_SUCCESS = {
    "ok",
    "degraded",
    "partial",
    "full",
    ReasoningOverall.OK.value,
    ReasoningOverall.DEGRADED.value,
    ReasoningOverall.PARTIAL.value,
    ReasoningOverall.FULL.value,
}

_ALIASES: dict[str, ReasoningOverall] = {
    "ok": ReasoningOverall.OK,
    "degraded": ReasoningOverall.DEGRADED,
    "partial": ReasoningOverall.PARTIAL,
    "full": ReasoningOverall.FULL,
    "data_only": ReasoningOverall.DATA_ONLY,
    "data-only": ReasoningOverall.DATA_ONLY,
    "failed": ReasoningOverall.FAILED,
    "error": ReasoningOverall.FAILED,
    "running": ReasoningOverall.RUNNING,
}


def parse_reasoning_overall(value: object) -> ReasoningOverall | None:
    text = str(value or "").strip()
    if not text:
        return None
    for item in ReasoningOverall:
        if item.value == text:
            return item
    return _ALIASES.get(text.lower())


def legacy_overall_string(value: ReasoningOverall) -> str:
    """Preserve historical JSON spelling for existing readers."""

    return value.value


def is_successful_overall(value: object) -> bool:
    parsed = parse_reasoning_overall(value)
    if parsed is None:
        return str(value or "").strip().lower() in _LEGACY_SUCCESS
    return parsed in {
        ReasoningOverall.FULL,
        ReasoningOverall.PARTIAL,
        ReasoningOverall.OK,
        ReasoningOverall.DEGRADED,
    }


def exit_code_for_overall(value: object) -> int:
    return DailyExitCode.SUCCESS if is_successful_overall(value) else DailyExitCode.FATAL
