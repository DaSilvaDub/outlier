from __future__ import annotations

import importlib.util
import subprocess
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

RUNNER_SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "_shared"
    / "run_prompt_workflow.py"
)
RUNNER_SPEC = importlib.util.spec_from_file_location("run_prompt_workflow", RUNNER_SCRIPT)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = RUNNER
RUNNER_SPEC.loader.exec_module(RUNNER)


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


def test_provider_commands_are_noninteractive_and_guarded(tmp_path: Path) -> None:
    codex = RUNNER.command_for("codex", "codex.exe", tmp_path / "answer.md")
    claude = RUNNER.command_for("claude", "claude.exe", tmp_path / "unused.md")
    gemini = RUNNER.command_for("gemini", "gemini.exe", tmp_path / "unused.md")
    grok = RUNNER.grok_command("grok.exe", tmp_path / "prompt.md")

    assert codex[-1] == "-"
    assert "--ephemeral" in codex
    assert ["--sandbox", "read-only"] == codex[codex.index("--sandbox") :][:2]
    assert "--no-session-persistence" in claude
    assert claude[-2:] == ["--tools", ""]
    assert gemini[-2:] == ["--approval-mode", "plan"]
    assert "--prompt-file" in grok
    assert "--no-subagents" in grok


def test_live_execution_requires_explicit_authorization() -> None:
    with pytest.raises(SystemExit):
        RUNNER.parse_args(["--workflow", "generic", "--execute"])

    args = RUNNER.parse_args(
        [
            "--workflow",
            "generic",
            "--execute",
            "--authorization",
            "DESK_OK",
        ]
    )
    assert args.execute is True


def test_generic_batch_plans_every_latest_prompt_for_each_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = tmp_path / "Desk1_Automated"
    for name in (
        "1_Master_Cards_pack_2026-07-26.txt",
        "2_Master_HitRate_pack_2026-07-26.txt",
        "3_Master_Totals_pack_2026-07-26.txt",
    ):
        make_prompt(prompts, name)
    monkeypatch.setattr(MODULE, "prompt_roots", lambda _repo, _workflow: [prompts])
    args = RUNNER.parse_args(
        [
            "--workflow",
            "generic",
            "--all-prompts",
            "--agents",
            "codex,claude",
            "--report-root",
            str(tmp_path / "BETTING REPORTS"),
        ]
    )

    plans = RUNNER.plan_runs(args, tmp_path)

    assert len(plans) == 6
    assert {plan.agent for plan in plans} == {"codex", "claude"}
    assert all("GENERIC" in Path(plan.report_path).parts for plan in plans)


def test_run_model_uses_stdout_without_calling_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="# Report", stderr="")

    monkeypatch.setattr(RUNNER, "executable_for", lambda _agent: "claude.exe")
    monkeypatch.setattr(RUNNER.subprocess, "run", fake_run)

    output = RUNNER.run_model("claude", "PROMPT", tmp_path, 60)

    assert output == "# Report"
    assert captured["input"] == "PROMPT"
    assert captured["check"] is False
    assert "shell" not in captured


def test_full_sequential_execution_passes_prior_reports_and_saves_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_dir = tmp_path / "Desk2_Manual"
    details = (
        (1, "Q", "chatgpt"),
        (2, "R", "claude"),
        (3, "W", "gemini"),
        (4, "X", "grok"),
        (5, "S", "claude"),
    )
    plans = []
    report_root = tmp_path / "BETTING REPORTS"
    for order, phase, model in details:
        prompt = make_prompt(
            prompt_dir,
            f"{order}_Phase{phase}_{phase}_{model}_pack_2026-07-26.txt",
        )
        agent = RUNNER.PHASE_AGENT[phase]
        plans.append(
            RUNNER.PlannedRun(
                "sequential",
                str(prompt),
                str(RUNNER.planned_path(MODULE.classify_prompt(prompt), agent, report_root)),
                agent,
                "2026-07-26",
                phase,
            )
        )

    calls: list[tuple[str, str]] = []

    def fake_model(agent: str, model_input: str, _work_dir: Path, _timeout: int) -> str:
        calls.append((agent, model_input))
        return f"# {agent} report\n"

    monkeypatch.setattr(RUNNER, "run_model", fake_model)

    written = RUNNER.execute_runs(plans, report_root, 60)

    assert [path.name[:4] for path in written] == ["01_Q", "02_R", "03_W", "04_X", "05_S"]
    assert [agent for agent, _input in calls] == ["codex", "claude", "gemini", "grok", "claude"]
    assert "01_Q_CHAT_report" in calls[1][1]
    assert "04_X_GROK_report" in calls[4][1]
    assert all(path.is_file() and path.stat().st_size > 0 for path in written)
    assert 'workflow: "sequential"' in written[-1].read_text(encoding="utf-8")


def test_model_failure_writes_no_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = make_prompt(tmp_path, "1_Master_Cards_pack_2026-07-26.txt")
    report_root = tmp_path / "BETTING REPORTS"
    plan = RUNNER.PlannedRun(
        "generic",
        str(prompt),
        str(report_root / "unused.md"),
        "codex",
        "2026-07-26",
        None,
    )
    monkeypatch.setattr(
        RUNNER,
        "run_model",
        lambda *_args: (_ for _ in ()).throw(RUNNER.WorkflowError("provider failed")),
    )

    with pytest.raises(RUNNER.WorkflowError, match="provider failed"):
        RUNNER.execute_runs([plan], report_root, 60)

    assert not list(report_root.rglob("*.md"))
