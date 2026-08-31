"""Central registry of AI research desk provider configuration.

Single source of truth for the model IDs *and* the execution policy (retry
attempts, backoff bounds, request timeout) each runner uses. Runners import
their ``PASS_<X>_CONFIG`` from here rather than hardcoding their own model
constant or retry knobs -- eliminating the "same value defined twice" debt
that used to exist between this file and each runner module.

API keys are NOT stored here. They are read from the environment / project
``.env`` at call time (see ``environment.load_environment``). Never put a key
in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# --- OpenAI (Prompt A -- stress-test, pack-only) --------------------------
OPENAI_MODEL = "gpt-5.5"
OPENAI_EFFORT = "xhigh"

# --- Google Gemini (Prompt B -- wide-scan, web allowed; Prompt C -- ------
# injury/lineup research, also web allowed). "Model code" per Google AI for
# Developers docs. This is a *preview* ID and will change at GA (the
# "-preview" suffix is dropped) -- update this one line.
GEMINI_MODEL = "gemini-3.1-pro-preview"

# --- Anthropic Claude (Prompts D/E -- synthesizer / red-team) -------------
# Exact model string from the Anthropic model catalog. Do NOT append a date
# suffix (e.g. "-20251101") -- the bare alias is complete as-is.
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"


@dataclass(frozen=True)
class ProviderConfig:
    """What model a pass calls, and how its runner is allowed to call it.

    ``request_extra`` holds fields that change the *content* of the request
    (reasoning effort, grounding mode, thinking mode, ...) -- these belong in
    every request hash so a config change invalidates cached output the same
    way a prompt or model change already does. The remaining fields are pure
    execution policy (attempts/timeouts/backoff): they change how a request
    is retried, never what is asked, so they are deliberately excluded from
    the hash.
    """

    provider: str
    model: str
    max_attempts: int = 1
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    timeout_seconds: float = 600.0
    request_extra: dict[str, object] = field(default_factory=dict)

    def request_fields(self) -> dict[str, object]:
        """Content-affecting fields that must join the request hash."""
        return {"provider": self.provider, "model": self.model, **self.request_extra}

    def with_overrides(self, **changes: Any) -> "ProviderConfig":
        return replace(self, **changes)


# Pass A (OpenAI, single provider-owned retry loop replaced by the shared
# executor -- see provider_executor.py). Rate limits are the only retryable
# failure, hence the generous attempt budget the old hand-rolled loop used.
PASS_A_CONFIG = ProviderConfig(
    provider="openai",
    model=OPENAI_MODEL,
    max_attempts=10,
    base_delay_seconds=30.0,
    max_delay_seconds=30.0,
    timeout_seconds=600.0,
    request_extra={"reasoning_effort": OPENAI_EFFORT},
)

# Pass B (Gemini, wide-scan). B and C share GEMINI_MODEL but do not have to
# share execution policy -- B is the higher-traffic, lower-stakes wide scan.
PASS_B_CONFIG = ProviderConfig(
    provider="google_gemini",
    model=GEMINI_MODEL,
    max_attempts=10,
    base_delay_seconds=1.0,
    max_delay_seconds=64.0,
    timeout_seconds=600.0,
    request_extra={"grounding": "google_search"},
)

# Pass C (Gemini, injury/lineup research). Same model as B, its own policy:
# C is gated much more strictly downstream (machine-validated quotes), so a
# slower, shorter retry budget is appropriate rather than inheriting B's.
PASS_C_CONFIG = ProviderConfig(
    provider="google_gemini",
    model=GEMINI_MODEL,
    max_attempts=5,
    base_delay_seconds=2.0,
    max_delay_seconds=64.0,
    timeout_seconds=600.0,
    request_extra={"grounding": "google_search"},
)

# Pass D (Claude, pack-only verdict pass). Same retry owner as A: SDK
# max_retries=0 and provider_executor.execute_with_retry.
PASS_D_CONFIG = ProviderConfig(
    provider="anthropic",
    model=CLAUDE_MODEL,
    max_attempts=1,
    timeout_seconds=600.0,
    request_extra={"effort": "high", "thinking": "adaptive"},
)

# Pass E (Claude, reconciliation). Same retry owner as D/A; kept as its own
# config object since D and E should not have to share it either.
PASS_E_CONFIG = ProviderConfig(
    provider="anthropic",
    model=CLAUDE_MODEL,
    max_attempts=1,
    timeout_seconds=600.0,
    request_extra={"effort": "high", "thinking": "adaptive"},
)
