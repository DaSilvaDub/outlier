<#
.SYNOPSIS
    PreToolUse guard: blocks paid AI Research Desk / reasoning commands.

.DESCRIPTION
    Enforces the AGENTS.md house rule "Never run reasoning models unless
    explicitly asked" mechanically instead of by prose. Reads the Claude Code
    PreToolUse hook payload on stdin and emits a "deny" permission decision
    when the proposed shell command would invoke a paid reasoning path.

    Replaces .claude/hookify.no-reasoning-unless-asked.local.md, which was
    action:warn (advisory only) and matched .gitignore's ".claude/*.local.md"
    so it never reached any other ent or a fresh clone.

    Escape hatch: when the user HAS explicitly asked this turn, append the
    literal token  DESK_OK  to the command. Both blocks and bypasses are
    appended to the audit log so an unexplained bypass is discoverable.

.NOTES
    Never throws. A guard that crashes is a guard that fails open, so every
    unexpected condition falls through to "allow" rather than wedging the
    session -- with one exception: a payload we cannot parse is treated as
    allow, because we cannot claim a command is dangerous without reading it.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$LogPath = Join-Path $env:USERPROFILE '.claude\reasoning-guard.log'

# Paid/networked reasoning surface. Derived from the AGENTS.md forbidden list.
# - "-m outlier_scrapers.<runner>" covers python, py, python3, uv run, poetry run
# - run_desk2? covers both the run_desk and run_desk2 orchestrators
# - [^;|]* keeps a match anchored to one command, not across a pipe/chain
$ReasoningPattern = '-m\s+outlier_scrapers\.(reasoning|gemini_research|c_research|claude_reasoning|claude_synthesis|run_desk2?)\b' +
                    '|daily_job\b[^;|]*--run-reasoning' +
                    '|pytest\b[^;|]*test_(reasoning|gemini_research|claude_reasoning|claude_synthesis|c_research)'

$BypassToken = 'DESK_OK'

function Write-AuditLine {
    param([string]$Verdict, [string]$Command)
    try {
        $dir = Split-Path -Parent $LogPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        # Collapse newlines so one event stays one grep-able line.
        $flat = ($Command -replace '\r?\n', ' <NL> ')
        Add-Content -LiteralPath $LogPath -Value "$stamp`t$Verdict`t$flat" -Encoding UTF8
    } catch {
        # Logging must never be the reason a session breaks.
    }
}

function Deny {
    param([string]$Reason)
    @{
        hookSpecificOutput = @{
            hookEventName            = 'PreToolUse'
            permissionDecision       = 'deny'
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Depth 6 -Compress
    exit 0
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload = $raw | ConvertFrom-Json
    $command = $payload.tool_input.command
    if ([string]::IsNullOrWhiteSpace($command)) { exit 0 }

    if ($command -notmatch $ReasoningPattern) { exit 0 }

    if ($command -cmatch $BypassToken) {
        Write-AuditLine -Verdict "BYPASS($BypassToken)" -Command $command
        exit 0
    }

    Write-AuditLine -Verdict 'BLOCKED' -Command $command

    Deny @"
BLOCKED by the outlier house rule: reasoning / AI Research Desk is OFF by default.

This command invokes a paid provider path (OpenAI / Anthropic / Gemini) or a
reasoning test that can leave the mock path. Cost, hangs, and quota burn are real.

Only proceed if the user explicitly asked THIS TURN -- e.g. "run the desk",
"run reasoning", "run A/B/C/D/E".

  - They did NOT ask: do not retry. Run pack/scrapers/offline tests instead, or ask first.
  - They DID ask: re-run with the token $BypassToken appended to the command.
    The bypass is recorded in ~/.claude/reasoning-guard.log.

Canonical rule: AGENTS.md -> "Never run reasoning models unless explicitly asked".
"@
} catch {
    # Fail open: an unparseable payload is not evidence of a dangerous command.
    exit 0
}
