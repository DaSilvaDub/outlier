# Cloud / Sandbox Limitations

This document records known constraints when running agent sessions in cloud
sandboxes (Codex, remote Linux VMs) versus the canonical local Windows
environment.

## PyPI egress blocked

Codex sandboxes block outbound network access to PyPI. Packages installed via
`pip install` or `uv pip install` will fail with connection errors.

**Affected dependencies:** sqlalchemy, openai, anthropic, google-generativeai,
playwright, and any other package not pre-installed in the sandbox image.

**Impact:** Tests and modules that import these packages at the top level will
fail at collection time. Use `--continue-on-collection-errors` when running
pytest in sandboxes to exercise the tests that can actually import their
dependencies.

**Not affected:**
- CI (GitHub Actions) — has full PyPI access and installs from `requirements.txt`.
- Local Windows dev — has full PyPI access via `pip` / `uv`.

## PowerShell / Windows sync scripts unavailable

The STEP 0 sync protocol (`report-sync.ps1`, `sync-outlier.ps1`,
`scripts/verify-sync.ps1`) requires PowerShell 7+ on Windows. These scripts are
not executable in Linux sandboxes.

Cloud agents must use the equivalent procedure documented in `AGENTS.md` under
"Cloud / Sandbox Agents" (clone/fetch + reset to `origin/master` + grep for the
five upgrade markers).

## OneDrive not applicable

The OneDrive mirror (`~\OneDrive\Documents\outlier`) and the `safe_copy()` retry
logic in `organize_today_run2.py` are Windows/OneDrive-specific. Cloud sandboxes
have no OneDrive integration and no drive-sync file locks.

## House rule enforcement

The `block-reasoning.ps1` PreToolUse hook requires PowerShell and will not fire
in Linux-based agent runtimes. The house rule ("never run reasoning unless
explicitly asked") still applies by policy — cloud agents must self-enforce.
