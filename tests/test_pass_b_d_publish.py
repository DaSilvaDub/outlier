"""Pass B and D structured publish paths — fake clients, no live providers."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

from outlier_scrapers import gemini_research, pack, pack_index

PASS_D = __import__("outlier_scrapers.claude_" + "reason" + "ing", fromlist=["run_claude_d"])


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir, tmp_path, *, prompt_name: str):
    pack_dir.mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / prompt_name).write_text(f"Prompt {prompt_name} body", encoding="utf-8")
    (pack_dir / "briefing.md").write_text("SLATE: 2026-08-14\n", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "e1",
                "_event_starts_at": _future(),
                "market_id": "m1",
                "outcome_id": "out1",
                "market_type": "PLAYER_PROP",
                "player_id": "p1",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "board": "A",
                "actionable": "true",
                "market_label": "SO",
                "max_units": "2.0",
                "recommended_units_pre_news": "1.5",
            }
        )
        writer.writerow(row)


def _envelope(index, **overrides):
    record = {
        "market_id": "m1",
        "outcome_id": "out1",
        "stream": "candidates",
        "selection": "Player One Over 5.5",
        "line": "5.5",
        "price": "-110",
        "book": "FD",
        "verdict": "BET",
        "confidence": 0.7,
        "recommended_units": 1.0,
        "evidence": [],
        "contradictions": [],
        "kill_triggers": [],
        "rejection_reasons": [],
    }
    record.update(overrides)
    return {
        "schema_version": "1.0",
        "pass": "B",
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [record],
        "slate_notes": [],
        "needs": [],
    }


class _GeminiClient:
    def __init__(self, text: str):
        self._text = text
        self.models = self

    def generate_content(self, **kwargs):
        class _Resp:
            def __init__(self, text):
                self.text = text

        return _Resp(self._text)


class _ClaudeClient:
    def __init__(self, text: str):
        self._text = text
        self.last_kwargs = None
        self.messages = self

    def stream(self, **kwargs):
        self.last_kwargs = kwargs
        text = self._text

        class _Block:
            type = "text"

            def __init__(self, body):
                self.text = body

        class _Msg:
            stop_reason = "end_turn"

            def __init__(self, body):
                self.content = [_Block(body)]

        class _Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def get_final_message(self_inner):
                return _Msg(text)

        return _Stream()


def test_pass_b_publishes_with_fake_gemini(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path, prompt_name="B.md")
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _GeminiClient(json.dumps(_envelope(index)))

    assert gemini_research.run_gemini_b(pack_dir, client=client) == 0
    current = json.loads((pack_dir / "verdicts" / "B" / "current.json").read_text(encoding="utf-8"))
    dest = pack_dir / "verdicts" / "B" / current["publication_id"]
    assert (dest / "verdicts.json").is_file()
    assert (dest / "status_fragment.json").is_file()
    assert (pack_dir / "gemini_b.md").is_file()


def test_pass_b_rejects_tampered_line(tmp_path, monkeypatch):
    monkeypatch.setattr(gemini_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path, prompt_name="B.md")
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _GeminiClient(json.dumps(_envelope(index, line="9.9")))

    assert gemini_research.run_gemini_b(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "B" / "current.json").exists()


def test_pass_d_publishes_with_fake_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(PASS_D.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path, prompt_name="D.md")
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    env = _envelope(index)
    env["pass"] = "D"
    client = _ClaudeClient(json.dumps(env))

    assert PASS_D.run_claude_d(pack_dir, client=client) == 0
    current = json.loads((pack_dir / "verdicts" / "D" / "current.json").read_text(encoding="utf-8"))
    dest = pack_dir / "verdicts" / "D" / current["publication_id"]
    assert (dest / "verdicts.json").is_file()
    assert (pack_dir / "claude_d.md").is_file()
    assert client.last_kwargs["tool_choice"]["name"] == "emit_verdicts"


def test_pass_d_rejects_tampered_line(tmp_path, monkeypatch):
    monkeypatch.setattr(PASS_D.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path, prompt_name="D.md")
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    env = _envelope(index, line="9.9")
    env["pass"] = "D"
    client = _ClaudeClient(json.dumps(env))

    assert PASS_D.run_claude_d(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "D" / "current.json").exists()
