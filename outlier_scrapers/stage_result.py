"""Typed orchestration results for the automated A/B/C/D/E desk."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class StageExecutionState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    GATED = "gated"


class ArtifactState(str, Enum):
    CURRENT = "current"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"


class StageErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    MISSING_INPUT = "MISSING_INPUT"
    NO_API_KEY = "NO_API_KEY"
    IO_ERROR = "IO_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    COMPATIBILITY_ARTIFACT_FAILED = "COMPATIBILITY_ARTIFACT_FAILED"


@dataclass(frozen=True)
class StageResult:
    phase: str
    execution_state: StageExecutionState
    artifact_state: ArtifactState
    output_file: str
    request_sha256: str = ""
    publication_id: str | None = None
    error_code: StageErrorCode | None = None
    reason: str = ""
    cache_hit: bool = False
    forced: bool = False
    used_local_fallback: bool = False

    @property
    def legacy_status(self) -> str:
        if self.cache_hit:
            return "cached"
        if self.execution_state is StageExecutionState.GATED:
            return "gated-missing-input"
        if self.execution_state is StageExecutionState.SKIPPED:
            return "skipped-no-key"
        if self.execution_state is StageExecutionState.FAILED:
            return "failed"
        if self.forced:
            return "forced-refresh"
        return "success"

    def as_status_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.legacy_status,
            "execution_state": self.execution_state.value,
            "artifact_state": self.artifact_state.value,
            "file": self.output_file,
            "request_sha256": self.request_sha256,
            "cache_hit": self.cache_hit,
            "forced": self.forced,
        }
        if self.publication_id:
            payload["publication_id"] = self.publication_id
        if self.error_code:
            payload["error_code"] = self.error_code.value
        if self.reason:
            payload["reason"] = self.reason
        if self.used_local_fallback:
            payload["used_local_fallback"] = True
        return payload


@dataclass(frozen=True)
class FinalReportResolution:
    state: ArtifactState
    authoritative: bool
    source: str = ""
    file: Path | None = None
    reason: str = ""
    compatibility_artifact: Path | None = None

    def as_status_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "state": self.state.value,
            "authoritative": self.authoritative,
        }
        if self.source:
            payload["source"] = self.source
        if self.file:
            payload["file"] = str(self.file)
        if self.reason:
            payload["reason"] = self.reason
        if self.compatibility_artifact:
            payload["compatibility_artifact"] = str(self.compatibility_artifact)
        return payload
