# Agent Handoff

**Last Commit SHA:** `9833b7a` (on branch `feat/schema-compatibility-gates`)

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/23

**Files Touched:**
- `outlier_scrapers/schema.py`
- `outlier_scrapers/normalizer.py`
- `outlier_scrapers/line_movement.py`
- `outlier_scrapers/pack.py`
- `tests/test_schema.py`

**Next Steps:**
- Monitor PR 23 CI / review and merge it.

---

## Handoff — `fix/ruff-dev-dependency` (PR #22)

**Last Commit SHA:** `9c5b1d6`

**PR Link:** https://github.com/DaSilvaDub/outlier/pull/22

**Files Touched:**
- `pyproject.toml` — add `ruff` (pinned `>=0.5.0`) to the `dev` optional-dependencies group
- `requirements.txt` — add `ruff>=0.5.0`
- `outlier_scrapers/login.py` — import `PROJECT_ROOT` from `.paths`
- `outlier_scrapers/runner_common.py` — move `logging`/`datetime` imports to the top of the file instead of mid-file

**Next Steps:**
- Verify `pip install -e .[dev]` installs both `pytest` and `ruff`.
- Review and merge PR #22.
