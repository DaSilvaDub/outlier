"""Transport-policy contract for A/B/D/E.

Core tests must be collectable without importing OpenAI/Anthropic/Gemini SDKs.
Provider runners keep those imports inside the call functions, and retries
belong to provider_executor rather than stacked SDK loops.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "outlier_scrapers"
SDK_ROOTS = {"openai", "anthropic", "google"}
PROVIDER_MODULES = (
    "reasoning.py",
    "claude_reasoning.py",
    "claude_synthesis.py",
    "gemini_research.py",
    "c_research.py",
)


def _module_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_provider_runners_do_not_import_sdks_at_module_level() -> None:
    for filename in PROVIDER_MODULES:
        names = _module_level_imports(ROOT / filename)
        leaked = names & SDK_ROOTS
        assert not leaked, f"{filename} imports {sorted(leaked)} at module level"


def test_a_d_e_delegate_retries_to_provider_executor() -> None:
    for filename in ("reasoning.py", "claude_reasoning.py", "claude_synthesis.py"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "execute_with_retry" in source
        assert "max_retries=0" in source
