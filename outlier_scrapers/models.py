"""Central registry of the model IDs used by the AI research desk runners.

Single source of truth for the exact provider model-code strings. Future
Gemini (Prompt B) and Claude (Prompts D/E) runners should import from here
rather than hardcoding their own constants, mirroring how ``reasoning.py``
isolates ``MODEL``/``EFFORT`` for the OpenAI Prompt A runner.

API keys are NOT stored here. They are read from the environment / project
``.env`` at call time (see ``environment.load_environment``). Never put a key
in this file.
"""

# --- OpenAI (Prompt A — stress-test, pack-only) --------------------------
# NOTE: reasoning.py currently hardcodes this same value as its own MODEL
# constant. Kept here too as the canonical reference; reasoning.py is left
# untouched to avoid disturbing tested code.
OPENAI_MODEL = "gpt-5.5"
OPENAI_EFFORT = "xhigh"

# --- Google Gemini (Prompt B — wide-scan, web allowed) -------------------
# "Model code" per Google AI for Developers docs. This is a *preview* ID and
# will change at GA (the "-preview" suffix is dropped) — update this one line.
GEMINI_MODEL = "gemini-3.1-pro-preview"

# --- Anthropic Claude (Prompts D/E — synthesizer / red-team) -------------
# Exact model string from the Anthropic model catalog. Do NOT append a date
# suffix (e.g. "-20251101") — the bare alias is complete as-is.
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
