# Agent Handoff

**Last Commit SHA:** `fd1fa23` (on branch `fix/ruff-dev-dependency`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/22

**Files Touched:**
- `pyproject.toml` — add `ruff` (pinned `>=0.5.0`) to the `dev` optional-dependencies group
- `requirements.txt` — add `ruff>=0.5.0`
- `outlier_scrapers/login.py` — import `PROJECT_ROOT` from `.paths`
- `outlier_scrapers/runner_common.py` — move `logging`/`datetime` imports to the top of the file instead of mid-file

**Next Steps:**
- Verify `pip install -e .[dev]` installs both `pytest` and `ruff`.
- Review and merge PR #22.
