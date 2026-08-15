"""Pass C structured publish path — fake Gemini client, no live provider."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

from outlier_scrapers import c_research, pack, pack_index


def _future() -> str:
    return (datetime.now().astimezone() + timedelta(hours=6)).isoformat()


def _write_pack(pack_dir, tmp_path):
    pack_dir.mkdir(parents=True)
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "C.md").write_text("Prompt C body", encoding="utf-8")
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
        "verdict": "CONFIRMS",
        "claim": "Starter confirmed.",
        "source_name": "Official",
        "source_tier": 1,
        "source_timestamp": "2026-08-14T12:00:00Z",
        "evidence": [],
    }
    record.update(overrides)
    return {
        "schema_version": "1.0",
        "pass": "C",
        "pack_date": "2026-08-14",
        "candidates_sha256": index.candidates_sha256,
        "game_totals_sha256": index.game_totals_sha256,
        "team_totals_sha256": index.team_totals_sha256,
        "findings": [record],
        "no_sourced_findings": False,
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


def test_pass_c_publishes_versioned_dir_with_fake_gemini(tmp_path, monkeypatch):
    monkeypatch.setattr(c_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _GeminiClient(json.dumps(_envelope(index)))

    assert c_research.run_c_research(pack_dir, client=client) == 0

    current = json.loads(
        (pack_dir / "verdicts" / "C" / "current.json").read_text(encoding="utf-8")
    )
    dest = pack_dir / "verdicts" / "C" / current["publication_id"]
    assert (dest / "verdicts.json").is_file()
    assert (dest / "violations.json").is_file()
    assert (dest / "report_fragment.md").is_file()
    assert (dest / "status_fragment.json").is_file()
    assert (dest / "manifest.json").is_file()
    status = json.loads((dest / "status_fragment.json").read_text(encoding="utf-8"))
    assert status["envelope_kind"] == "finding"
    assert status["record_count"] == 1
    assert (pack_dir / "chatgpt_c.md").is_file()


def test_pass_c_publishes_from_legacy_pipe_output(tmp_path, monkeypatch):
    monkeypatch.setattr(c_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    pipe_output = (
        "FINDING | market_id=m1 | selection=Player One Over 5.5 | line=5.5 | price=-110 | "
        "verdict=CONFIRMS | claim=Starter confirmed | source_name=Official | source_tier=1 | "
        "source_timestamp=2026-08-14T12:00:00Z"
    )
    client = _GeminiClient(pipe_output)

    assert c_research.run_c_research(pack_dir, client=client) == 0

    current = json.loads(
        (pack_dir / "verdicts" / "C" / "current.json").read_text(encoding="utf-8")
    )
    dest = pack_dir / "verdicts" / "C" / current["publication_id"]
    data = json.loads((dest / "verdicts.json").read_text(encoding="utf-8"))
    assert data["findings"][0]["claim"] == "Starter confirmed"
    assert (pack_dir / "chatgpt_c.md").read_text(encoding="utf-8").strip().endswith(
        pipe_output
    )


def test_pass_c_publishes_no_sourced_findings_as_empty_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(c_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    client = _GeminiClient(c_research.NO_FINDINGS)

    assert c_research.run_c_research(pack_dir, client=client) == 0

    current = json.loads(
        (pack_dir / "verdicts" / "C" / "current.json").read_text(encoding="utf-8")
    )
    dest = pack_dir / "verdicts" / "C" / current["publication_id"]
    data = json.loads((dest / "verdicts.json").read_text(encoding="utf-8"))
    assert data["findings"] == []
    assert data["no_sourced_findings"] is True


def test_pass_c_rejects_tampered_line_and_does_not_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(c_research.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-08-14"
    _write_pack(pack_dir, tmp_path)
    index = pack_index.build_pack_index(pack_dir, policy_path=tmp_path / "no-policy.json")
    client = _GeminiClient(json.dumps(_envelope(index, line="9.9")))

    assert c_research.run_c_research(pack_dir, client=client) == 1
    assert not (pack_dir / "verdicts" / "C" / "current.json").exists()
    assert not (pack_dir / "chatgpt_c.md").exists()
