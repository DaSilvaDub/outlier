# Cloud / Sandbox Limitations

This document records known constraints when running agent sessions in cloud
sandboxes (Codex, remote Linux VMs) versus the canonical local Windows
environment.

## PyPI egress blocked

Codex sandboxes block outbound network access to PyPI. Packages installed via
`pip install` or `uv pip install` will fail with connection errors.

**Affected dependencies observed in this environment:** `structlog`,
`sqlalchemy`, `numpy`, `google-genai`, and `python-dateutil`, along with any
other package not pre-installed in the sandbox image.

**Impact:** Tests and modules that import these packages at the top level can
fail at collection time. `--continue-on-collection-errors` reports additional
collection failures; it does not supply the missing dependencies or guarantee
that any tests run. In one documented attempt, a local wheel cache plus a
temporary logging shim allowed pytest to start, but zero tests passed, so that
attempt provided no test coverage and is not equivalent to CI. Do not describe
such a run as partial coverage unless the test report shows passing tests with
the real dependencies installed.

Prefer an available local wheel cache with the project's declared dependencies.
If required packages are missing and cannot be installed, report the suite as
blocked and list the affected modules; do not replace dependencies with shims
and treat the result as CI-equivalent evidence.

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
