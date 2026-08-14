import csv
import hashlib
import io
import json
from datetime import datetime, timezone

import pytest

from outlier_scrapers import pack, runner_common


def test_sha256_bytes_matches_hashlib():
    data = b"hello"
    assert runner_common.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_compute_request_hash_is_order_independent():
    a = runner_common.compute_request_hash({"x": 1, "y": 2})
    b = runner_common.compute_request_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64


def test_extract_yaml_request_hash_reads_front_matter():
    content = "---\nmodel: m\nrequest_sha256: abc123\n---\n\nbody"
    assert runner_common.extract_yaml_request_hash(content) == "abc123"


def test_extract_yaml_request_hash_none_when_no_front_matter():
    assert runner_common.extract_yaml_request_hash("no front matter") is None


def test_validate_candidates_returns_bytes_and_hash(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(pack.CANDIDATES_HEADER)
        row = {k: "" for k in pack.CANDIDATES_HEADER}
        row["_event_starts_at"] = "2099-12-31T00:00:00Z"
        row["market_id"] = "data"
        w.writerow([row[k] for k in pack.CANDIDATES_HEADER])
    raw, digest = runner_common.validate_candidates(tmp_path)
    assert digest == hashlib.sha256(raw).hexdigest()
    assert b"data" in raw


def test_validate_candidates_excludes_historical_edge_from_ai_bytes(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "_event_starts_at": "2099-12-31T00:00:00Z",
                "market_id": "m1",
                "edge_pct": "0.10",
                "historical_edge_pct": "0.25",
            }
        )
        writer.writerow(row)

    raw, _ = runner_common.validate_candidates(tmp_path)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    rows = list(reader)

    assert "historical_edge_pct" in f.read_text(encoding="utf-8").splitlines()[0]
    assert "historical_edge_pct" not in (reader.fieldnames or [])
    assert rows[0]["edge_pct"] == "0.10"
    assert "0.25" not in raw.decode("utf-8-sig")


def test_validate_candidates_drops_locked_events(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(pack.CANDIDATES_HEADER)
        # Row 1: future
        row1 = {k: "" for k in pack.CANDIDATES_HEADER}
        row1["_event_starts_at"] = "2099-12-31T00:00:00Z"
        row1["market_id"] = "future_event"
        w.writerow([row1[k] for k in pack.CANDIDATES_HEADER])
        # Row 2: past
        row2 = {k: "" for k in pack.CANDIDATES_HEADER}
        row2["_event_starts_at"] = "1999-12-31T00:00:00Z"
        row2["market_id"] = "past_event"
        w.writerow([row2[k] for k in pack.CANDIDATES_HEADER])
    
    raw, digest = runner_common.validate_candidates(tmp_path)
    assert b"future_event" in raw
    assert b"past_event" not in raw


def test_validate_candidates_rejects_bad_header(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["bad", "header"])
        w.writerow(["x", "y"])
    with pytest.raises(runner_common.RunnerError):
        runner_common.validate_candidates(tmp_path)


def test_validate_candidates_allows_header_only_for_actionable_totals(tmp_path):
    with open(tmp_path / "candidates.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(pack.CANDIDATES_HEADER)

    with pytest.raises(runner_common.RunnerError):
        runner_common.validate_candidates(tmp_path)

    raw, digest = runner_common.validate_candidates(tmp_path, allow_empty=True)
    assert digest == hashlib.sha256(raw).hexdigest()
    expected_header = [
        field
        for field in pack.CANDIDATES_HEADER
        if field not in runner_common.AI_EXCLUDED_CANDIDATE_FIELDS
    ]
    assert raw.decode("utf-8-sig").splitlines() == [",".join(expected_header)]


def test_has_actionable_game_totals():
    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER

    def board(actionable):
        import io

        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=GAME_TOTALS_HEADER)
        writer.writeheader()
        row = {field: "" for field in GAME_TOTALS_HEADER}
        row.update(
            totals_id="t1",
            market_id="m1",
            selection="A @ B Total OVER 8.5",
            line="8.5",
            price="-110",
            actionable=actionable,
        )
        writer.writerow(row)
        return stream.getvalue().encode()

    assert runner_common.has_actionable_game_totals(board("TRUE"))
    assert not runner_common.has_actionable_game_totals(board("false"))


@pytest.mark.parametrize(
    "payload",
    [
        b"actionable\ntrue\n",
        b"totals_id,actionable\nt1,true,extra\n",
    ],
)
def test_game_totals_gate_rejects_wrong_schema_or_malformed_rows(payload):
    with pytest.raises(runner_common.RunnerError):
        runner_common.has_actionable_game_totals(payload)


def test_atomic_write_writes_front_matter_then_body_and_leaves_no_tmp(tmp_path):
    runner_common.atomic_write(tmp_path, "out.md", "---\nk: v\n---\n\n", "BODY")
    out = tmp_path / "out.md"
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert text.endswith("BODY")
    assert list(tmp_path.glob("out_tmp_*")) == []


def test_load_game_totals_missing_returns_empty_hash(tmp_path):
    raw, digest = runner_common.load_game_totals(tmp_path)
    assert raw is None
    assert digest == runner_common.empty_game_totals_hash()


def test_build_reasoning_data_block_includes_totals(tmp_path):
    (tmp_path / runner_common.GAME_TOTALS_NAME).write_text(
        "sport,market_id\nMLB,gm1\n", encoding="utf-8"
    )
    totals_bytes, _ = runner_common.load_game_totals(tmp_path)
    block = runner_common.build_reasoning_data_block(b"a,b\n1,2", totals_bytes)
    assert "candidates.csv:" in block
    assert "GAME_TOTALS.CSV" in block
    assert "gm1" in block


def _totals_csv_bytes(rows: list[dict], *, header=None) -> bytes:
    import io
    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER

    fields = header or GAME_TOTALS_HEADER
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        full = {field: "" for field in fields}
        full.update(row)
        writer.writerow(full)
    return stream.getvalue().encode()


def test_append_totals_block_includes_team_totals():
    game = _totals_csv_bytes(
        [
            {
                "totals_id": "g1",
                "market_id": "mg",
                "selection": "A @ B Total O/U OVER 8.5",
                "line": "8.5",
                "price": "-110",
                "total_kind": "game",
                "actionable": "true",
            }
        ]
    )
    team = _totals_csv_bytes(
        [
            {
                "totals_id": "t1",
                "market_id": "mt",
                "selection": "A Team Total OVER 4.5",
                "line": "4.5",
                "price": "-110",
                "total_kind": "team",
                "actionable": "true",
            }
        ]
    )
    block = runner_common.append_totals_block("base", game, team)
    assert "GAME_TOTALS.CSV" in block
    assert "TEAM_TOTALS.CSV" in block
    assert "mg" in block and "mt" in block


def test_parse_game_totals_filters_legacy_mixed_file():
    mixed = _totals_csv_bytes(
        [
            {
                "totals_id": "g1",
                "market_id": "mg",
                "selection": "game",
                "line": "8.5",
                "price": "-110",
                "total_kind": "game",
                "actionable": "true",
            },
            {
                "totals_id": "t1",
                "market_id": "mt",
                "selection": "team",
                "line": "4.5",
                "price": "-110",
                "total_kind": "team",
                "actionable": "true",
            },
            {
                "totals_id": "g2",
                "market_id": "mg2",
                "selection": "blank-kind",
                "line": "9.5",
                "price": "-105",
                "total_kind": "",
                "actionable": "false",
            },
        ]
    )
    game_rows = runner_common.parse_game_totals(mixed)
    team_rows = runner_common.parse_team_totals(mixed)
    assert {r["totals_id"] for r in game_rows} == {"g1", "g2"}
    assert {r["totals_id"] for r in team_rows} == {"t1"}


def test_has_actionable_any_totals():
    game = _totals_csv_bytes(
        [
            {
                "totals_id": "g1",
                "market_id": "mg",
                "selection": "game",
                "line": "8.5",
                "price": "-110",
                "total_kind": "game",
                "actionable": "false",
            }
        ]
    )
    team = _totals_csv_bytes(
        [
            {
                "totals_id": "t1",
                "market_id": "mt",
                "selection": "team",
                "line": "4.5",
                "price": "-110",
                "total_kind": "team",
                "actionable": "true",
            }
        ]
    )
    assert not runner_common.has_actionable_any_totals(game, None)
    assert runner_common.has_actionable_any_totals(game, team)
    assert runner_common.has_actionable_team_totals(team)


# ---------------------------------------------------------------------------
# Step 5: structured request + versioned publication
# ---------------------------------------------------------------------------


def _artifacts(**overrides):
    files = {
        "verdicts_json": b'{"schema_version":"1.0","pass":"A","verdicts":[]}',
        "violations_json": b"[]",
        "report_fragment": b"# A fragment\n",
        "status_fragment": b'{"envelope_present":true,"record_count":0}',
    }
    files.update(overrides)
    return runner_common.PassArtifacts(
        pass_="A",
        request_sha256="a" * 64,
        schema_version="1.0",
        verdicts_json=files["verdicts_json"],
        violations_json=files["violations_json"],
        report_fragment=files["report_fragment"],
        status_fragment=files["status_fragment"],
    )


def test_request_structured_returns_schema_for_kind():
    from outlier_scrapers import verdicts

    req = runner_common.request_structured("verdict")
    assert req.kind == "verdict"
    assert req.schema_version == verdicts.SCHEMA_VERSION
    assert req.schema == verdicts.json_schema_for("verdict")


def test_parse_envelope_delegates_to_verdicts():
    from outlier_scrapers import verdicts
    from tests.test_verdicts import verdict_envelope_dict

    raw = json.dumps(verdict_envelope_dict())
    parsed = runner_common.parse_envelope(raw, "verdict")
    assert isinstance(parsed.envelope, verdicts.VerdictEnvelope)
    assert parsed.envelope.pass_ == "A"


def test_write_envelope_round_trips_through_parse():
    from outlier_scrapers import verdicts
    from tests.test_verdicts import verdict_envelope_dict

    parsed = runner_common.parse_envelope(json.dumps(verdict_envelope_dict()), "verdict")
    blob = runner_common.write_envelope(
        parsed.envelope,
        request_sha256="b" * 64,
        model="test-model",
        candidates_sha256="a" * 64,
        game_totals_sha256="b" * 64,
        team_totals_sha256="c" * 64,
    )
    data = json.loads(blob.decode("utf-8"))
    assert data["request_sha256"] == "b" * 64
    assert data["model"] == "test-model"
    assert data["pass"] == "A"
    assert "publication_id" not in data
    reparsed = runner_common.parse_envelope(blob.decode("utf-8"), "verdict")
    env = reparsed.envelope
    assert isinstance(env, verdicts.VerdictEnvelope)
    assert env.verdicts[0].outcome_id == "out_123"


def test_build_repair_block_lists_code_outcome_and_pack_value():
    from outlier_scrapers.verdict_gate import Violation

    block = runner_common.build_repair_block(
        [
            Violation(
                code="line_tampered",
                outcome_id="out1",
                market_id="mkt1",
                detail="line 6.5 != pack line 5.5.",
                severity="reject",
            )
        ]
    )
    assert "line_tampered" in block
    assert "out1" in block
    assert "5.5" in block
    assert "6.5" not in block.split("pack_value=", 1)[1]


def test_structured_request_fields_include_three_hashes_and_schema():
    extra = runner_common.structured_request_fields(
        candidates_sha256="c" * 64,
        game_totals_sha256="g" * 64,
        team_totals_sha256="t" * 64,
    )
    assert extra["candidates_sha256"] == "c" * 64
    assert extra["game_totals_sha256"] == "g" * 64
    assert extra["team_totals_sha256"] == "t" * 64
    assert extra["schema_version"] == "1.0"
    hashed = runner_common.compute_request_hash({**{"model": "m"}, **extra})
    assert len(hashed) == 64


def test_publication_id_covers_all_four_files():
    base = _artifacts()
    changed_md = _artifacts(report_fragment=b"# renderer fix\n")
    assert runner_common.publication_id_for(base) != runner_common.publication_id_for(changed_md)
    assert runner_common.publication_id_for(base) == runner_common.publication_id_for(_artifacts())


def test_publish_pass_writes_versioned_dir_and_current_pointer(tmp_path):
    artifacts = _artifacts()
    result = runner_common.publish_pass(tmp_path, artifacts, now=datetime(2026, 8, 14, tzinfo=timezone.utc))
    dest = tmp_path / "verdicts" / "A" / result.publication_id
    assert dest.is_dir()
    for name in runner_common.PUBLICATION_FILES:
        assert (dest / name).is_file()
    assert (dest / "manifest.json").is_file()
    current = json.loads((tmp_path / "verdicts" / "A" / "current.json").read_text(encoding="utf-8"))
    assert current["publication_id"] == result.publication_id
    assert current["request_sha256"] == artifacts.request_sha256
    assert result.wrote is True


def test_manifest_hash_equals_directory_name(tmp_path):
    result = runner_common.publish_pass(tmp_path, _artifacts())
    dest = tmp_path / "verdicts" / "A" / result.publication_id
    recomputed = runner_common.recompute_publication_id(dest)
    assert recomputed == result.publication_id
    (dest / "report_fragment.md").write_bytes(b"tampered")
    assert runner_common.recompute_publication_id(dest) != result.publication_id


def test_identical_republish_is_noop(tmp_path):
    artifacts = _artifacts()
    first = runner_common.publish_pass(tmp_path, artifacts)
    second = runner_common.publish_pass(tmp_path, artifacts)
    assert first.publication_id == second.publication_id
    assert second.wrote is False
    parent = tmp_path / "verdicts" / "A"
    assert list(parent.glob("*.tmp-*")) == []
    assert [p.name for p in parent.iterdir() if p.is_dir()] == [first.publication_id]


def test_different_envelope_same_request_hash_gets_new_directory(tmp_path):
    first = runner_common.publish_pass(tmp_path, _artifacts())
    second = runner_common.publish_pass(
        tmp_path, _artifacts(verdicts_json=b'{"schema_version":"1.0","pass":"A","verdicts":[{"x":1}]}')
    )
    assert first.publication_id != second.publication_id
    parent = tmp_path / "verdicts" / "A"
    assert (parent / first.publication_id).is_dir()
    assert (parent / second.publication_id).is_dir()
    current = json.loads((parent / "current.json").read_text(encoding="utf-8"))
    assert current["publication_id"] == second.publication_id
    assert current["request_sha256"] == first.request_sha256


def test_crash_before_pointer_swap_leaves_previous_current(tmp_path):
    first = runner_common.publish_pass(tmp_path, _artifacts())
    parent = tmp_path / "verdicts" / "A"
    new_art = _artifacts(report_fragment=b"# new\n")
    new_id = runner_common.publication_id_for(new_art)
    staging = parent / f"{new_id}.tmp-99999"
    staging.mkdir()
    (staging / "report_fragment.md").write_bytes(new_art.report_fragment)
    current = json.loads((parent / "current.json").read_text(encoding="utf-8"))
    assert current["publication_id"] == first.publication_id
    assert not (parent / new_id).exists()
