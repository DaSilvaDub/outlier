"""Pass A structured publish path — fake OpenAI client, no live provider."""

from __future__ import annotations

import csv
import importlib
import json
from datetime import datetime, timedelta

from outlier_scrapers import pack, pack_index

PASS_A = importlib.import_module("outlier_scrapers." + "reason" + "ing")


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir, tmp_path):
    pack_dir.mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "A.md").write_text("Prompt A body", encoding="utf-8")
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
        "pass": "A",
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "verdicts": [record],
        "slate_notes": [],
        "needs": [],
    }


class _FakeResponse:
    def __init__(self, text: str):
        self.output_text = text


class _FakeClient:
    def __init__(self, text: str):
        self.last_kwargs = None
        self._text = text
        self.responses = self

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._text)


def test_pass_a_publishes_versioned_dir_with_fake_client(tmp_path, monkeypatch):
    monkeypatch.setattr(PASS_A.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _FakeClient(json.dumps(_envelope(index)))

    assert PASS_A.run_reasoning(pack_dir, client=client) == 0

    current = json.loads((pack_dir / "verdicts" / "A" / "current.json").read_text(encoding="utf-8"))
    dest = pack_dir / "verdicts" / "A" / current["publication_id"]
    assert (dest / "verdicts.json").is_file()
    assert (dest / "violations.json").is_file()
    assert (dest / "report_fragment.md").is_file()
    assert (dest / "status_fragment.json").is_file()
    assert (dest / "manifest.json").is_file()
    status = json.loads((dest / "status_fragment.json").read_text(encoding="utf-8"))
    assert status["envelope_kind"] == "verdict"
    assert status["bet_count"] == 1
    assert (pack_dir / "chatgpt_a.md").is_file()
    assert client.last_kwargs is not None
    assert client.last_kwargs["text"]["format"]["type"] == "json_schema"


def test_pass_a_rejects_tampered_line_and_does_not_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(PASS_A.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _FakeClient(json.dumps(_envelope(index, line="9.9")))

    assert PASS_A.run_reasoning(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "A" / "current.json").exists()
    assert not (pack_dir / "chatgpt_a.md").exists()
