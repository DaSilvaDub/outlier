"""The desk prompts and the structured-envelope contract must not drift apart.

Each pass's prompt is the only place a provider learns what to emit; the gate in
``verdict_gate`` rejects an envelope that misses a required field or echoes a
pack identity value it was never shown. These tests pin the couplings that broke
silently before: a prompt that names the wrong pass, a required schema field no
prompt mentions, a pack-only pass told to browse, and the Markdown Master Cards
lane pointing at the automated Pass A prompt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from outlier_scrapers import runner_common as rc
from outlier_scrapers import verdicts

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

IDENTITY_FIELDS = (
    "pack_date",
    "candidates_sha256",
    "game_totals_sha256",
    "team_totals_sha256",
)

VERDICT_PASSES = {"A": "A.md", "B": "B.md", "D": "D.md"}
ALL_PASSES = {"A": "A.md", "B": "B.md", "C": "C.md", "D": "D.md", "E": "E.md"}
PACK_ONLY_PASSES = ("A.md", "D.md")


def _text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _record_properties(kind: str) -> set[str]:
    schema = verdicts.json_schema_for(kind)
    array_key = {
        "verdict": "verdicts",
        "finding": "findings",
        "reconciliation": "reconciliations",
    }[kind]
    return set(schema["properties"][array_key]["items"]["properties"])


@pytest.mark.parametrize("pass_, filename", sorted(ALL_PASSES.items()))
def test_prompt_declares_its_own_pass(pass_: str, filename: str) -> None:
    """The envelope example must tag the pass the runner publishes it under."""
    body = _text(filename)
    example = json.loads(re.findall(r"```json\n(.*?)```", body, re.S)[0])
    assert example["pass"] == pass_
    for other in ALL_PASSES:
        if other != pass_:
            assert f'| `pass` | `"{other}"` |' not in body


@pytest.mark.parametrize(
    "filename, kind",
    [("A.md", "verdict"), ("B.md", "verdict"), ("D.md", "verdict"),
     ("C.md", "finding"), ("E.md", "reconciliation")],
)
def test_prompt_names_every_required_record_field(filename: str, kind: str) -> None:
    body = _text(filename)
    missing = sorted(field for field in _record_properties(kind) if field not in body)
    assert not missing, f"{filename} never mentions {missing}"


@pytest.mark.parametrize("filename", ["A.md", "B.md", "C.md", "D.md", "E.md"])
def test_prompt_asks_for_the_pack_identity_it_is_given(filename: str) -> None:
    body = _text(filename)
    assert "PACK IDENTITY" in body
    for field in IDENTITY_FIELDS:
        assert field in body, f"{filename} never mentions {field}"


@pytest.mark.parametrize("filename", PACK_ONLY_PASSES)
def test_pack_only_prompts_do_not_ask_for_web_research(filename: str) -> None:
    """A and D have no web tools; telling them to research invites fabrication."""
    body = _text(filename)
    assert "PACK-ONLY" in body
    for banned in ("site:", "Search first", "last 24 hours", "Tier 1 —"):
        assert banned not in body, f"{filename} instructs web research: {banned!r}"


def test_identity_block_matches_what_the_prompts_reference() -> None:
    block = rc.build_pack_identity_block(
        pack_date="2026-08-24",
        candidates_sha256="c" * 64,
        game_totals_sha256="g" * 64,
        team_totals_sha256="t" * 64,
    )
    assert "PACK IDENTITY" in block
    for field in IDENTITY_FIELDS:
        assert f"{field}: " in block


@pytest.mark.parametrize(
    "module_name",
    ["reasoning", "gemini_research", "c_research", "claude_reasoning"],
)
def test_every_pass_runner_sends_the_identity_block(module_name: str) -> None:
    source = (
        Path(__file__).resolve().parents[1] / "outlier_scrapers" / f"{module_name}.py"
    ).read_text(encoding="utf-8")
    assert "build_pack_identity_block" in source


def test_master_cards_lane_has_its_own_markdown_prompt() -> None:
    """prompts/A.md emits JSON now; the human paste lane must not inherit that."""
    master = _text("Master_Cards_Analysis.md")
    assert "Master Card" in master
    assert "REQUIRED FINAL OUTPUT" in master
    assert "verdict envelope" not in master

    script = (
        Path(__file__).resolve().parents[1]
        / ".agents"
        / "skills"
        / "export-manual-outlier-packs"
        / "scripts"
        / "generate_prompts.py"
    ).read_text(encoding="utf-8")
    assert 'load_prompt_template("Master_Cards_Analysis.md")' in script


@pytest.mark.parametrize("filename", sorted(ALL_PASSES.values()))
def test_every_json_example_is_parseable_json(filename: str) -> None:
    """B and C can fall back to prompt-only JSON, so the shown shape must parse."""
    blocks = re.findall(r"```json\n(.*?)```", _text(filename), re.S)
    assert blocks, f"{filename} shows no JSON envelope example"
    for block in blocks:
        json.loads(block)


@pytest.mark.parametrize("filename", sorted(ALL_PASSES.values()))
def test_prompts_name_both_quality_flag_columns(filename: str) -> None:
    """Totals boards carry quality_flags; candidates carry data_quality_flags."""
    if filename in ("C.md", "E.md"):  # neither gates on row flags itself
        pytest.skip("pass does not apply the integrity-flag gate")
    body = _text(filename)
    assert "data_quality_flags" in body
    assert "quality_flags" in body


def test_master_cards_states_the_desk_stake_limits() -> None:
    body = _text("Master_Cards_Analysis.md")
    assert "0.5-unit grid" in body
    assert "3.0 units" in body


def test_reconciliation_prompt_requires_an_upstream_bet() -> None:
    """E may only back what a verdict pass actually proposed as a BET."""
    body = _text("E.md")
    assert "at least one cited A / D / B record carries the verdict" in body
    assert "`null` when pass C did not run" in body
