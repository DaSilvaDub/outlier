Branch: feat/add-type-checking
Last Commit SHA: 1e4e5f9 (on feat/add-type-checking)
PR Link: https://github.com/DaSilvaDub/outlier/pull/24

Next Steps:
- Static type checking with MyPy and Pyright is fully implemented and configured in pyproject.toml.
- Added a GitHub Action workflow `.github/workflows/typecheck.yml` to automatically run mypy and pyright checks on pull requests.
- Type errors in login, pack, game_totals, run_desk, reasoning, insights, and games have been completely fixed (including a critical NameError bug in login.py).
- Legacy files that still need gradual type migration are cleanly excluded from MyPy and Pyright checks.
- Pull Request #24 is open. Wait for code review and merge.
