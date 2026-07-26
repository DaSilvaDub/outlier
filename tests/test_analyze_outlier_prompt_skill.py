from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "analyze-outlier-prompt"
    / "scripts"
    / "resolve_report_path.py"
)
SPEC = importlib.util.spec_from_file_location("resolve_report_path", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_resolve_desktop_prompt_and_avoid_overwrite(tmp_path: Path) -> None:
    prompt = tmp_path / "1_Master_Cards_pack_2026-07-26.txt"
    prompt.write_text("prompt", encoding="utf-8")

    first = MODULE.resolve(prompt, "claude", tmp_path)
    assert Path(first.report_path).name == "CLAUDE_Master_Cards_report_2026-07-26.md"
    Path(first.report_path).write_text("existing", encoding="utf-8")

    second = MODULE.resolve(prompt, "claude", tmp_path)
    assert Path(second.report_path).name == "CLAUDE_Master_Cards_report_2026-07-26_v2.md"


def test_resolve_desk2_prompt_uses_pack_directory_date(tmp_path: Path) -> None:
    prompt = tmp_path / "packs" / "2026-07-26" / "paste_x_grok.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("prompt", encoding="utf-8")

    result = MODULE.resolve(prompt, "grok", tmp_path / "reports")
    assert result.pack_date == "2026-07-26"
    assert result.prompt_kind == "x_grok"
    assert Path(result.report_path).name == "GROK_x_grok_report_2026-07-26.md"


def test_choose_prompt_fails_closed_when_latest_date_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name in (
        "1_Master_Cards_pack_2026-07-26.txt",
        "2_Master_HitRate_pack_2026-07-26.txt",
    ):
        (prompts / name).write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(MODULE, "default_prompt_roots", lambda _repo: [prompts])

    with pytest.raises(ValueError, match="pass --prompt"):
        MODULE.choose_prompt(None, tmp_path)


def test_choose_prompt_prefers_exact_supported_file(tmp_path: Path) -> None:
    prompt = tmp_path / "3_Master_Totals_pack_2026-07-26.txt"
    prompt.write_text("prompt", encoding="utf-8")

    assert MODULE.choose_prompt(str(prompt), tmp_path) == prompt.resolve()


def test_choose_prompt_rejects_pack_data_that_is_not_a_prompt(tmp_path: Path) -> None:
    briefing = tmp_path / "packs" / "2026-07-26" / "briefing.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("pack data", encoding="utf-8")

    with pytest.raises(ValueError, match=r"paste_\*\.md"):
        MODULE.choose_prompt(str(briefing), tmp_path)
