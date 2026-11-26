#!/usr/bin/env python3
"""PreToolUse guard (Linux/cloud twin of block-reasoning.ps1).

Blocks paid AI Research Desk / reasoning commands unless the command carries
the literal token DESK_OK (only when the user explicitly asked this turn).
Skips itself when pwsh exists, because block-reasoning.ps1 already runs then.
Fails open on any unparseable payload.
"""
import json
import os
import re
import shutil
import sys
import time

PATTERN = re.compile(
    r"-m\s+outlier_scrapers\.(reasoning|gemini_research|c_research|claude_reasoning|claude_synthesis|run_desk2?)\b"
    r"|daily_job\b[^;|]*--run-reasoning"
    r"|pytest\b[^;|]*test_(reasoning|gemini_research|claude_reasoning|claude_synthesis|c_research)"
)
BYPASS = "DESK_OK"
LOG = os.path.join(os.path.expanduser("~"), ".claude", "reasoning-guard.log")


def audit(verdict: str, command: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        flat = re.sub(r"\r?\n", " <NL> ", command)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{verdict}\t{flat}\n")
    except Exception:
        pass


def main() -> int:
    if shutil.which("pwsh"):
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        command = (payload.get("tool_input") or {}).get("command") or ""
    except Exception:
        return 0
    if not command.strip() or not PATTERN.search(command):
        return 0
    if BYPASS in command:
        audit(f"BYPASS({BYPASS})", command)
        return 0
    audit("BLOCKED", command)
    reason = (
        "BLOCKED by the outlier house rule: reasoning / AI Research Desk is OFF by default. "
        "Only proceed if the user explicitly asked THIS TURN; then re-run with the token "
        f"{BYPASS} appended to the command. Canonical rule: AGENTS.md -> "
        "'Never run reasoning models unless explicitly asked'."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
