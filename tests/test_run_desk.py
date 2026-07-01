"""Focused desk wiring tests for automated Prompt C support."""

import csv
import json

import pytest

from outlier_scrapers import pack, run_desk


@pytest.fixture
def desk_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_desk, "load_environment", lambda: None)
    pack_dir = tmp_path / "packs" / "2026-06-28"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-06-28", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "market_id": "m1",
                "market_type": "TOTAL",
                "selection": "OVER 8.5",
                "line": "8.5",
                "price": "-110",
            }
        )
        writer.writerow(row)
    return pack_dir


def _write_output(pack_dir, name, request_hash):
    (pack_dir / name).write_text(
        f"---\nrequest_sha256: {request_hash}\n---\nbody\n", encoding="utf-8"
    )


def test_c_is_wired_into_phase_maps():
    assert "C" in run_desk.PHASES
    assert run_desk.PHASE_OUTPUTS["C"] == "chatgpt_c.md"
    assert run_desk.PHASE_KEYS["C"] == "GEMINI_API_KEY"
    assert run_desk.PHASE_RUNNERS["C"] is run_desk.c_research.run_c_research


def test_cached_c_output_counts_as_cached(desk_pack, monkeypatch):
    _write_output(desk_pack, "chatgpt_c.md", "same")
    monkeypatch.setattr(
        run_desk.c_research,
        "run_c_research",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", run_desk.c_research.run_c_research)

    result = run_desk.run_phase("C", desk_pack)
    assert result["status"] == "cached"
    assert result["request_sha256"] == "same"


def test_failed_c_refresh_restores_previous_output(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    output = desk_pack / "chatgpt_c.md"
    output.write_bytes(b"previous")

    def fail_after_delete(*args, **kwargs):
        output.unlink()
        return 1

    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", fail_after_delete)
    result = run_desk.run_phase("C", desk_pack)
    assert result["status"] == "failed"
    assert output.read_bytes() == b"previous"


def test_default_orchestration_runs_c_and_writes_full_status(desk_pack, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")

    def runner_for(phase):
        def run(pack_dir, **kwargs):
            _write_output(pack_dir, run_desk.PHASE_OUTPUTS[phase], phase.lower())
            return 0

        return run

    for phase in run_desk.PHASES:
        monkeypatch.setitem(run_desk.PHASE_RUNNERS, phase, runner_for(phase))

    assert run_desk.orchestrate_desk(desk_pack) == 0
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert set(status["components"]) == set(run_desk.PHASES)
    assert status["components"]["C"]["status"] == "success"
    assert status["final_report"] == {"source": "claude_e", "file": "claude_e.md"}


def test_e_is_gated_when_required_inputs_are_missing(desk_pack, monkeypatch):
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "E", should_not_run)
    assert run_desk.orchestrate_desk(desk_pack, steps=["E"]) == 1
    assert called is False
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "DATA_ONLY"
    assert status["components"]["E"]["status"] == "gated-missing-input"
