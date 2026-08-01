"""Desk 2 — model-strength-specialized, manual/offline betting desk.

Desk 2 reassigns the A-E work across ChatGPT (Q), Gemini (W), Grok (X),
and Claude (R skeptic + S synthesis). It is MANUAL-ONLY: it generates
paste-ready docs and stitches saved replies. It never calls a provider API,
so these tests run with no API keys and no network.
"""

import csv
import json

import pytest

from outlier_scrapers import pack, run_desk2


@pytest.fixture
def desk2_pack(tmp_path):
    """A minimal pregame pack (briefing + one candidate) in a tmp pack dir.

    PROJECT_ROOT is left pointing at the real repo so prompt files under
    prompts/desk2/ are exercised; only the pack dir is a tmp fixture.
    """
    pack_dir = tmp_path / "packs" / "2026-07-13"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-07-13 MLB/WNBA", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "market_id": "MKT_PREGAME",
                "market_type": "TOTAL",
                "selection": "OVER 8.5",
                "line": "8.5",
                "price": "-110",
                "_event_starts_at": "2099-07-13T23:10:00+00:00",
            }
        )
        writer.writerow(row)
    return pack_dir


def _seed_replies(pack_dir):
    for phase, body in (
        ("Q", "Q verdict: BET MKT_PREGAME"),
        ("W", "W news: starter confirmed"),
        ("X", "X signal: beat writer buzz"),
        ("R", "R skeptic: PASS, thin sample"),
    ):
        (pack_dir / run_desk2.REPLY_OUTPUTS[phase]).write_text(
            f"---\nmodel: test\n---\n{body}\n", encoding="utf-8"
        )


# --- phase wiring -----------------------------------------------------------

def test_desk2_phase_map_puts_grok_in_the_x_seat():
    assert run_desk2.DESK2_PHASES == ("Q", "W", "X", "R", "S")
    assert run_desk2.PHASE_MODEL["X"] == "grok"
    assert run_desk2.PHASE_MODEL["Q"] == "chatgpt"
    assert run_desk2.PHASE_MODEL["W"] == "gemini"
    assert run_desk2.PHASE_MODEL["R"] == "claude"
    assert run_desk2.PHASE_MODEL["S"] == "claude"
    assert run_desk2.REPLY_OUTPUTS["X"] == "grok_x.md"


def test_reply_filenames_do_not_collide_with_desk1():
    desk1 = {"chatgpt_a.md", "gemini_b.md", "chatgpt_c.md", "claude_d.md", "claude_e.md"}
    assert set(run_desk2.REPLY_OUTPUTS.values()).isdisjoint(desk1)


def test_manual_only_no_provider_runners_or_sdk():
    # Guardrail: Desk 2 must not be able to call a reasoning provider.
    assert not hasattr(run_desk2, "PHASE_RUNNERS")
    assert not hasattr(run_desk2, "PHASE_KEYS")
    assert not hasattr(run_desk2, "openai")


# --- paste-doc assembly -----------------------------------------------------

def test_pack_only_paste_doc_carries_candidates_and_house_rules(desk2_pack):
    doc = run_desk2.build_paste_doc("Q", desk2_pack)
    assert "candidates.csv" in doc
    assert "MKT_PREGAME" in doc
    # House rules come from pack.ROLE_BLOCK (single source of truth).
    assert "HR markets are excluded" in doc
    assert "PREGAME-only" in doc


def test_research_paste_doc_includes_briefing_and_market_list(desk2_pack):
    doc = run_desk2.build_paste_doc("W", desk2_pack)
    assert "SLATE: 2026-07-13" in doc
    assert "MKT_PREGAME" in doc


def test_grok_paste_doc_carries_sentiment_tier3_guardrail(desk2_pack):
    doc = run_desk2.build_paste_doc("X", desk2_pack)
    assert "Tier-3" in doc
    assert "corroborat" in doc.lower()
    assert "sentiment" in doc.lower()


def test_synthesis_paste_doc_includes_saved_replies(desk2_pack):
    _seed_replies(desk2_pack)
    doc = run_desk2.build_paste_doc("S", desk2_pack)
    assert "Q verdict: BET MKT_PREGAME" in doc
    assert "X signal: beat writer buzz" in doc
    # AGREE/DISAGREE instruction comes from the S prompt.
    assert "AGREE" in doc.upper()


# --- generate stage (offline) ----------------------------------------------

def test_generate_writes_all_paste_docs_with_no_api_keys(desk2_pack, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    written = run_desk2.generate_paste_docs(desk2_pack, run_desk2.DESK2_PHASES)
    assert set(written) == set(run_desk2.DESK2_PHASES)
    for phase in run_desk2.DESK2_PHASES:
        assert (desk2_pack / run_desk2.PASTE_OUTPUTS[phase]).exists()


def test_generate_drops_locked_events(tmp_path):
    pack_dir = tmp_path / "packs" / "2026-07-13"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        for mid, start in (
            ("MKT_PREGAME", "2099-01-01T00:00:00Z"),
            ("MKT_LOCKED", "2000-01-01T00:00:00Z"),
        ):
            row = {field: "" for field in pack.CANDIDATES_HEADER}
            row.update(
                {
                    "market_id": mid,
                    "selection": f"sel {mid}",
                    "line": "8.5",
                    "price": "-110",
                    "_event_starts_at": start,
                }
            )
            writer.writerow(row)
    doc = run_desk2.build_paste_doc("Q", pack_dir)
    assert "MKT_PREGAME" in doc
    assert "MKT_LOCKED" not in doc


# --- assemble stage (manual synthesis) -------------------------------------

def test_manual_report_quotes_pack_lines_and_includes_replies(desk2_pack):
    _seed_replies(desk2_pack)
    path = run_desk2.produce_manual_report(desk2_pack)
    text = path.read_text(encoding="utf-8")
    assert "MKT_PREGAME" in text
    assert "Q verdict: BET MKT_PREGAME" in text
    assert "X signal: beat writer buzz" in text
    # Atomic write leaves no partial temp file behind.
    assert list(desk2_pack.glob("manual_desk2_report_tmp_*")) == []


def test_assemble_is_data_only_when_no_replies(desk2_pack):
    rc = run_desk2.orchestrate_desk2(desk2_pack, stage="assemble")
    assert rc == 1
    status = json.loads((desk2_pack / run_desk2.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "DATA_ONLY"


def test_assemble_is_full_when_all_four_replies_present(desk2_pack):
    _seed_replies(desk2_pack)
    rc = run_desk2.orchestrate_desk2(desk2_pack, stage="assemble")
    assert rc == 0
    status = json.loads((desk2_pack / run_desk2.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert status["final_report"]["file"] == "manual_desk2_report.md"


# --- orchestrate generate ---------------------------------------------------

def test_generate_stage_writes_status(desk2_pack):
    rc = run_desk2.orchestrate_desk2(desk2_pack, stage="generate")
    assert rc == 0
    status = json.loads((desk2_pack / run_desk2.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "GENERATED"
    assert set(status["paste_docs"]) == set(run_desk2.DESK2_PHASES)


def test_generate_stage_data_only_when_pack_missing(tmp_path):
    pack_dir = tmp_path / "packs" / "empty"
    pack_dir.mkdir(parents=True)
    rc = run_desk2.orchestrate_desk2(pack_dir, stage="generate")
    assert rc == 1
    status = json.loads((pack_dir / run_desk2.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "DATA_ONLY"


# --- prompt files exist -----------------------------------------------------

def test_all_desk2_prompt_files_exist_and_carry_their_role():
    from outlier_scrapers import paths

    prompt_dir = paths.PROJECT_ROOT / "prompts" / "desk2"
    for phase in run_desk2.DESK2_PHASES:
        p = prompt_dir / run_desk2.PROMPT_FILES[phase]
        assert p.exists(), f"missing prompt file for phase {phase}: {p}"
        assert p.read_text(encoding="utf-8").strip(), f"empty prompt file: {p}"
    # The Grok prompt must carry the sentiment/Tier-3 guardrail.
    grok = (prompt_dir / run_desk2.PROMPT_FILES["X"]).read_text(encoding="utf-8")
    assert "Tier-3" in grok
