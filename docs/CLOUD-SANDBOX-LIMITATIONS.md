# Cloud / Sandbox Limitations

This document records known constraints when running agent sessions in cloud
sandboxes (Codex, remote Linux VMs) versus the canonical local Windows
environment.

## PyPI egress blocked

Cloud sandboxes block outbound network access to PyPI. `pip install` and
`uv pip install` fail with DNS or proxy errors (`pypi.org` answers 403 through
the agent proxy), so no project dependency can be installed in-session.

**Affected dependencies:** structlog, numpy, SQLAlchemy, psycopg2-binary,
tzdata, openai, anthropic, google-genai, playwright, and any other package not
pre-installed in the sandbox image.

**Impact: no test runs at all, not merely a reduced subset.** An autouse
fixture in `tests/conftest.py` (the ledger tripwire) imports
`outlier_scrapers.feedback`, which imports `structlog`, so *every* test errors
during setup — a bare `pytest --continue-on-collection-errors` reports
~2060 errors and 0 passed. `--continue-on-collection-errors` alone therefore
buys nothing; do not read its exit code as evidence of coverage.

`structlog` is the single blocker for the great majority of the suite. Putting
a throwaway `structlog` stand-in on `PYTHONPATH` **outside the checkout**
(never committed) is enough to collect and run most tests, which is the only
way a cloud session can exercise them today. Tests that additionally need
numpy, SQLAlchemy or a provider SDK still error.

Treat hosted CI (GitHub Actions) as the authority for test results reported
from a cloud session, and say in the handoff how the suite was run.

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
