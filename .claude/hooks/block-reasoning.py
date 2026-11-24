#!/usr/bin/env python3
"""PreToolUse guard (cross-platform twin of block-reasoning.ps1).

Blocks paid AI Research Desk / reasoning commands unless the command carries
the literal token DESK_OK (only when the user explicitly asked this turn).
Fails open on any unexpected condition, like the .ps1 version.
Keep the patterns in sync with block-reasoning.ps1.
"""
import json
import os
import re
import sys
import time

PATTERN = re.compile(
    r"-m\s+outlier_scrapers\.(reasoning|gemini_research|c_research|claude_reasoning|claude_synthesis|run_desk2?)\b"
    r"|daily_job\b[^;|]*--run-reasoning"
    r"|pytest\b[^;|]*test_(reasoning|gemini_research|claude_reasoning|claude_synthesis|c_research)"
)
BYPASS = "DESK_OK"
LOG = os.path.join(os.path.expanduser("~"), ".claude", "reasoning-guard.log")


def audit(verdict, command):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        flat = re.sub(r"\r?\n", " <NL> ", command)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{verdict}\t{flat}\n")
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        command = (json.loads(raw).get("tool_input") or {}).get("command") or ""
        if not command.strip() or not PATTERN.search(command):
            return 0
        if BYPASS in command:
            audit(f"BYPASS({BYPASS})", command)
            return 0
        audit("BLOCKED", command)
        reason = (
            "BLOCKED by the outlier house rule: reasoning / AI Research Desk is OFF by default.\n"
            "This command invokes a paid provider path or a reasoning test. Only proceed if the "
            f"user explicitly asked THIS TURN; then re-run with the token {BYPASS} appended. "
            "Bypasses are logged in ~/.claude/reasoning-guard.log.\n"
            'Canonical rule: AGENTS.md -> "Never run reasoning models unless explicitly asked".'
        )
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason}}))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
