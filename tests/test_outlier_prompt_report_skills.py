from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "_shared"
    / "resolve_prompt_report.py"
)
SPEC = importlib.util.spec_from_file_location("resolve_prompt_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_prompt(tmp_path: Path, relative: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("prompt", encoding="utf-8")
    return path


def test_generic_report_uses_separate_generic_date_folder(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "1_Master_Cards_pack_2026-07-26.txt")
    info = MODULE.classify_prompt(prompt)

    result = MODULE.resolve(info, "claude", tmp_path / "BETTING REPORTS")

    assert Path(result.report_path) == (
        tmp_path
        / "BETTING REPORTS"
        / "GENERIC"
        / "2026-07-26"
        / "CLAUDE_Master_Cards_report_2026-07-26.md"
    )
    assert result.prior_report_paths == ()


def test_sequential_q_starts_without_predecessors(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "1_PhaseQ_Q_chatgpt_pack_2026-07-26.txt")
    result = MODULE.resolve(
        MODULE.classify_prompt(prompt), "codex", tmp_path / "BETTING REPORTS"
    )

    assert Path(result.report_path).name == "01_Q_CHAT_report_2026-07-26.md"
    assert "SEQUENTIAL" in Path(result.report_path).parts
    assert result.prior_report_paths == ()


def test_sequential_phase_refuses_missing_predecessors(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "2_PhaseR_R_claude_pack_2026-07-26.txt")

    with pytest.raises(ValueError, match="missing predecessor reports: Q"):
        MODULE.resolve(
            MODULE.classify_prompt(prompt), "claude", tmp_path / "BETTING REPORTS"
        )


def test_sequential_r_receives_q_report(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "2_PhaseR_R_claude_pack_2026-07-26.txt")
    report_dir = tmp_path / "BETTING REPORTS" / "SEQUENTIAL" / "2026-07-26"
    q_report = make_prompt(report_dir, "01_Q_CHAT_report_2026-07-26.md")

    result = MODULE.resolve(
        MODULE.classify_prompt(prompt), "claude", tmp_path / "BETTING REPORTS"
    )

    assert result.prior_report_paths == (str(q_report.resolve()),)
    assert Path(result.report_path).name == "02_R_CLAUDE_report_2026-07-26.md"


def test_sequential_phase_rejects_wrong_agent(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "3_PhaseW_W_gemini_pack_2026-07-26.txt")

    with pytest.raises(ValueError, match="must be run by gemini"):
        MODULE.resolve(
            MODULE.classify_prompt(prompt), "grok", tmp_path / "BETTING REPORTS"
        )


def test_choose_next_sequential_phase_obeys_q_r_w_x_s_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "Desk2_Manual"
    names = (
        "1_PhaseQ_Q_chatgpt_pack_2026-07-26.txt",
        "2_PhaseR_R_claude_pack_2026-07-26.txt",
        "3_PhaseW_W_gemini_pack_2026-07-26.txt",
        "4_PhaseX_X_grok_pack_2026-07-26.txt",
        "5_PhaseS_S_claude_pack_2026-07-26.txt",
    )
    for name in names:
        make_prompt(prompts, name)
    monkeypatch.setattr(MODULE, "prompt_roots", lambda _repo, _workflow: [prompts])
    report_root = tmp_path / "BETTING REPORTS"

    first = MODULE.choose_prompt(None, tmp_path, "sequential", report_root)
    assert first.phase == "Q"

    report_dir = report_root / "SEQUENTIAL" / "2026-07-26"
    make_prompt(report_dir, "01_Q_CHAT_report_2026-07-26.md")
    second = MODULE.choose_prompt(None, tmp_path, "sequential", report_root)
    assert second.phase == "R"


def test_next_sequential_phase_prefers_numbered_desktop_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop_prompts = tmp_path / "Desk2_Manual"
    pack_prompts = tmp_path / "packs" / "2026-07-26"
    for order, phase, model in (
        (1, "Q", "chatgpt"),
        (2, "R", "claude"),
        (3, "W", "gemini"),
        (4, "X", "grok"),
        (5, "S", "claude"),
    ):
        make_prompt(
            desktop_prompts,
            f"{order}_Phase{phase}_{phase}_{model}_pack_2026-07-26.txt",
        )
        make_prompt(pack_prompts, f"paste_{phase.lower()}_{model}.md")
    monkeypatch.setattr(
        MODULE,
        "prompt_roots",
        lambda _repo, _workflow: [desktop_prompts, pack_prompts],
    )

    selected = MODULE.choose_prompt(
        None, tmp_path, "sequential", tmp_path / "BETTING REPORTS"
    )

    assert selected.phase == "Q"
    assert selected.path.suffix == ".txt"


def test_generic_latest_fails_closed_when_multiple_prompts_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "Desk1_Automated"
    for name in (
        "1_Master_Cards_pack_2026-07-26.txt",
        "2_Master_HitRate_pack_2026-07-26.txt",
    ):
        make_prompt(prompts, name)
    monkeypatch.setattr(MODULE, "prompt_roots", lambda _repo, _workflow: [prompts])

    with pytest.raises(ValueError, match="generic prompts; pass --prompt"):
        MODULE.choose_prompt(None, tmp_path, "generic", tmp_path / "reports")


def test_prompt_cannot_cross_workflow_boundary(tmp_path: Path) -> None:
    prompt = make_prompt(tmp_path, "1_Master_Cards_pack_2026-07-26.txt")

    with pytest.raises(ValueError, match="belongs to generic, not sequential"):
        MODULE.choose_prompt(
            str(prompt), tmp_path, "sequential", tmp_path / "BETTING REPORTS"
        )
