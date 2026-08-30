import csv
import hashlib
import io
import json
import shutil
from dataclasses import FrozenInstanceError
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


def test_build_repair_block_uses_authoritative_priced_line():
    from outlier_scrapers.verdict_gate import Violation

    block = runner_common.build_repair_block(
        [
            Violation(
                code="priced_line_unreconciled",
                outcome_id="out1",
                market_id="mkt1",
                detail="priced_line is '6.5'; verdict line is '5.5'.",
                severity="reject",
            )
        ]
    )
    assert "pack_value=6.5" in block
    assert "5.5" not in block


def test_structured_request_fields_include_pack_date_hashes_and_schema():
    extra = runner_common.structured_request_fields(
        pack_date="2026-08-25",
        candidates_sha256="c" * 64,
        game_totals_sha256="g" * 64,
        team_totals_sha256="t" * 64,
    )
    assert extra["pack_date"] == "2026-08-25"
    assert extra["candidates_sha256"] == "c" * 64
    assert extra["game_totals_sha256"] == "g" * 64
    assert extra["team_totals_sha256"] == "t" * 64
    assert extra["schema_version"] == "1.0"
    hashed = runner_common.compute_request_hash({**{"model": "m"}, **extra})
    assert len(hashed) == 64


def test_request_hash_changes_with_pack_date_alone():
    """Copying a pack to a new date leaves every hash identical, so without the
    date in the request hash refresh_if_stale would skip the required re-run."""
    common = {
        "candidates_sha256": "c" * 64,
        "game_totals_sha256": "g" * 64,
        "team_totals_sha256": "t" * 64,
    }
    first = runner_common.compute_request_hash(
        runner_common.structured_request_fields(pack_date="2026-08-24", **common)
    )
    second = runner_common.compute_request_hash(
        runner_common.structured_request_fields(pack_date="2026-08-25", **common)
    )
    assert first != second


def test_structured_request_fields_requires_pack_date():
    with pytest.raises(TypeError):
        runner_common.structured_request_fields(  # type: ignore[call-arg]
            candidates_sha256="c" * 64,
            game_totals_sha256="g" * 64,
            team_totals_sha256="t" * 64,
        )


def test_pack_identity_contract_is_immutable_and_supported_by_request_helpers():
    identity = runner_common.PackIdentity(
        pack_date="2026-08-25",
        candidates_sha256="c" * 64,
        game_totals_sha256="d" * 64,
        team_totals_sha256="e" * 64,
    )
    assert runner_common.structured_request_fields(identity) == {
        **identity.as_dict(),
        "schema_version": "1.0",
    }
    assert "pack_date: 2026-08-25" in runner_common.build_pack_identity_block(identity)
    with pytest.raises(FrozenInstanceError):
        identity.pack_date = "2026-08-26"  # type: ignore[misc]
    with pytest.raises(TypeError, match="cannot be mixed"):
        runner_common.structured_request_fields(identity, pack_date="2026-08-25")
    with pytest.raises(TypeError, match="cannot be mixed"):
        runner_common.build_pack_identity_block(identity, candidates_sha256="c" * 64)


def test_configuration_sha256_changes_for_config_but_not_secrets():
    base = {
        "provider": "openai",
        "model": "test-model",
        "max_output_tokens": 1000,
        "api_key": "first-secret",
        "nested": {"client_secret": "also-secret", "effort": "high"},
    }
    same = {
        **base,
        "api_key": "different-secret",
        "nested": {"client_secret": "changed-secret", "effort": "high"},
    }
    changed = {**same, "max_output_tokens": 2000}
    assert runner_common.configuration_sha256(base) == runner_common.configuration_sha256(same)
    assert runner_common.configuration_sha256(base) != runner_common.configuration_sha256(changed)


def test_current_git_sha_returns_none_on_git_failure(monkeypatch, tmp_path):
    class Result:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(runner_common.subprocess, "run", lambda *args, **kwargs: Result())
    assert runner_common.current_git_sha(tmp_path) is None


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


def _publication_identity(pack_date="2026-08-25"):
    return runner_common.PackIdentity(
        pack_date=pack_date,
        candidates_sha256="1" * 64,
        game_totals_sha256="2" * 64,
        team_totals_sha256="3" * 64,
    )


def _identity_artifacts(
    identity,
    *,
    pass_name="A",
    request_sha256="a" * 64,
    verdict_pass=None,
    verdict_request=None,
    upstream=None,
    report_fragment=b"# report\n",
):
    verdict_data = {
        "schema_version": "1.0",
        "pass": verdict_pass or pass_name,
        **identity.as_dict(),
        "request_sha256": verdict_request or request_sha256,
        "verdicts": [],
    }
    if upstream is not None:
        verdict_data["upstream_publication_ids"] = upstream
    return runner_common.PassArtifacts(
        pass_=pass_name,
        request_sha256=request_sha256,
        schema_version="1.0",
        verdicts_json=json.dumps(verdict_data, sort_keys=True).encode("utf-8"),
        violations_json=b"[]",
        report_fragment=report_fragment,
        status_fragment=b'{"envelope_present":true}',
    )


def test_validate_current_publication_missing_pointer(tmp_path):
    result = runner_common.validate_current_publication(
        tmp_path, "A", _publication_identity()
    )
    assert result.state == runner_common.ArtifactState.MISSING
    assert result.reason == "current_pointer_missing"


def test_validate_current_publication_rejects_malformed_pointer(tmp_path):
    pointer = tmp_path / "verdicts" / "A" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("{", encoding="utf-8")
    result = runner_common.validate_current_publication(
        tmp_path, "A", _publication_identity()
    )
    assert result.state == runner_common.ArtifactState.INVALID
    assert result.reason == "current_pointer_malformed"


def test_validate_current_publication_missing_target_directory(tmp_path):
    pointer = tmp_path / "verdicts" / "A" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps({"publication_id": "f" * 64, "request_sha256": "a" * 64}),
        encoding="utf-8",
    )
    result = runner_common.validate_current_publication(
        tmp_path, "A", _publication_identity()
    )
    assert result.state == runner_common.ArtifactState.MISSING
    assert result.reason == "publication_directory_missing"


def test_validate_current_publication_is_current_and_exposes_identity(tmp_path):
    identity = _publication_identity()
    published = runner_common.publish_pass(tmp_path, _identity_artifacts(identity))
    result = runner_common.validate_current_publication(
        tmp_path, "A", identity, expected_request_sha256="a" * 64
    )
    assert result.state == runner_common.ArtifactState.CURRENT
    assert result.current
    assert result.publication_id == published.publication_id
    assert result.request_sha256 == "a" * 64
    assert result.path == published.path


def test_validate_current_publication_marks_pack_or_request_changes_stale(tmp_path):
    identity = _publication_identity()
    runner_common.publish_pass(tmp_path, _identity_artifacts(identity))
    stale_pack = runner_common.validate_current_publication(
        tmp_path, "A", _publication_identity("2026-08-26")
    )
    stale_request = runner_common.validate_current_publication(
        tmp_path, "A", identity, expected_request_sha256="b" * 64
    )
    assert stale_pack.state == runner_common.ArtifactState.STALE
    assert stale_pack.reason == "pack_identity_mismatch:pack_date"
    assert stale_request.state == runner_common.ArtifactState.STALE
    assert stale_request.reason == "request_hash_mismatch"


@pytest.mark.parametrize(
    ("mutation", "reason_prefix"),
    [
        ("remove_artifact", "publication_files_missing"),
        ("tamper_artifact", "publication_file_hash_mismatch"),
        ("tamper_manifest", "publication_file_hash_mismatch"),
    ],
)
def test_validate_current_publication_checks_complete_tree_integrity(
    tmp_path, mutation, reason_prefix
):
    identity = _publication_identity()
    published = runner_common.publish_pass(tmp_path, _identity_artifacts(identity))
    if mutation == "remove_artifact":
        (published.path / "status_fragment.json").unlink()
    elif mutation == "tamper_artifact":
        (published.path / "report_fragment.md").write_text("tampered", encoding="utf-8")
    else:
        manifest = json.loads((published.path / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"]["verdicts.json"] = "0" * 64
        (published.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = runner_common.validate_current_publication(tmp_path, "A", identity)
    assert result.state in {
        runner_common.ArtifactState.MISSING,
        runner_common.ArtifactState.INVALID,
    }
    assert result.reason.startswith(reason_prefix)


def test_validate_current_publication_rejects_wrong_pass(tmp_path):
    identity = _publication_identity()
    runner_common.publish_pass(
        tmp_path,
        _identity_artifacts(identity, verdict_pass="D"),
    )
    result = runner_common.validate_current_publication(tmp_path, "A", identity)
    assert result.state == runner_common.ArtifactState.INVALID
    assert result.reason == "verdict_pass_mismatch"


def test_validate_current_publication_rejects_inconsistent_request_hash(tmp_path):
    identity = _publication_identity()
    runner_common.publish_pass(
        tmp_path,
        _identity_artifacts(identity, verdict_request="b" * 64),
    )
    result = runner_common.validate_current_publication(tmp_path, "A", identity)
    assert result.state == runner_common.ArtifactState.INVALID
    assert result.reason == "verdict_request_hash_mismatch"


def test_validate_current_publication_rejects_pointer_request_disagreement(tmp_path):
    identity = _publication_identity()
    runner_common.publish_pass(tmp_path, _identity_artifacts(identity))
    pointer = tmp_path / "verdicts" / "A" / "current.json"
    data = json.loads(pointer.read_text(encoding="utf-8"))
    data["request_sha256"] = "b" * 64
    pointer.write_text(json.dumps(data), encoding="utf-8")
    result = runner_common.validate_current_publication(tmp_path, "A", identity)
    assert result.state == runner_common.ArtifactState.INVALID
    assert result.reason == "pointer_request_hash_mismatch"


def test_validate_e_requires_exact_selected_upstream_ids(tmp_path):
    identity = _publication_identity()
    selected = {"A": "1" * 64, "D": "2" * 64, "B": "3" * 64, "C": None}
    runner_common.publish_pass(
        tmp_path,
        _identity_artifacts(identity, pass_name="E", upstream=selected),
    )
    current = runner_common.validate_current_publication(
        tmp_path, "E", identity, expected_upstream=selected
    )
    stale = runner_common.validate_current_publication(
        tmp_path,
        "E",
        identity,
        expected_upstream={**selected, "A": "4" * 64},
    )
    assert current.state == runner_common.ArtifactState.CURRENT
    assert stale.state == runner_common.ArtifactState.STALE
    assert stale.reason == "upstream_publication_mismatch:A"


def test_validate_pinned_publication_does_not_follow_new_current_pointer(tmp_path):
    identity = _publication_identity()
    old = runner_common.publish_pass(tmp_path, _identity_artifacts(identity))
    runner_common.publish_pass(
        tmp_path,
        _identity_artifacts(identity, report_fragment=b"# newer report\n"),
    )
    pinned = runner_common.validate_publication(
        tmp_path, "A", old.publication_id, identity
    )
    assert pinned.state == runner_common.ArtifactState.CURRENT
    assert pinned.publication_id == old.publication_id


# ---------------------------------------------------------------------------
# The envelope's declared pass must match the pass being published.
# publish_verdict_pass needs no provider SDK, so this runs anywhere.
# ---------------------------------------------------------------------------


def _publishable_pack(tmp_path):
    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER

    pack_dir = tmp_path / "packs" / "2026-08-14"
    pack_dir.mkdir(parents=True)
    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        row = {field: "" for field in pack.CANDIDATES_HEADER}
        row.update(
            {
                "sport": "MLB",
                "event_id": "evt1",
                "_event_starts_at": "2099-12-31T00:00:00Z",
                "market_id": "mkt1",
                "outcome_id": "out1",
                "market_type": "PLAYER_PROP",
                "market_label": "SO",
                "selection": "Player One Over 5.5",
                "line": "5.5",
                "price": "-110",
                "book": "FD",
                "actionable": "true",
                "board": "A",
                "max_units": "2.0",
                "recommended_units_pre_news": "1.5",
            }
        )
        writer.writerow(row)
    with open(pack_dir / "game_totals.csv", "w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=GAME_TOTALS_HEADER).writeheader()
    return pack_dir


def test_load_pack_identity_uses_pack_index_and_empty_total_hash(tmp_path):
    pack_dir = _publishable_pack(tmp_path)
    identity = runner_common.load_pack_identity(pack_dir)
    _, expected_candidates = runner_common.validate_candidates(pack_dir, allow_empty=True)
    assert identity.pack_date == "2026-08-14"
    assert identity.candidates_sha256 == expected_candidates
    assert identity.game_totals_sha256 == runner_common.sha256_bytes(
        (pack_dir / "game_totals.csv").read_bytes()
    )
    assert identity.team_totals_sha256 == runner_common.empty_team_totals_hash()


def test_copied_pack_keeps_hashes_but_uses_destination_pack_date(tmp_path):
    original = _publishable_pack(tmp_path / "original")
    copied = tmp_path / "copied" / "packs" / "2026-08-15"
    shutil.copytree(original, copied)
    first = runner_common.load_pack_identity(original)
    second = runner_common.load_pack_identity(copied)
    assert first.pack_date == "2026-08-14"
    assert second.pack_date == "2026-08-15"
    assert first.candidates_sha256 == second.candidates_sha256
    assert first.game_totals_sha256 == second.game_totals_sha256
    assert first.team_totals_sha256 == second.team_totals_sha256


def test_create_run_context_is_utc_uuid_and_stable_for_nonsecret_config(
    tmp_path, monkeypatch
):
    pack_dir = _publishable_pack(tmp_path)
    monkeypatch.setattr(runner_common, "best_effort_git_sha", lambda repository=None: "f" * 40)
    context = runner_common.create_run_context(
        pack_dir,
        {"provider": "test", "api_key": "never-hash-me"},
        started_at=datetime(2026, 8, 14, 1, 2, 3),
    )
    assert len(context.run_id) == 36
    assert context.started_at == datetime(2026, 8, 14, 1, 2, 3, tzinfo=timezone.utc)
    assert context.git_sha == "f" * 40
    assert context.configuration_sha256 == runner_common.configuration_sha256(
        {"provider": "test", "api_key": "different-secret"}
    )


def _verdict_envelope_json(pack_dir, declared_pass: str) -> str:
    from outlier_scrapers import pack_index

    index = pack_index.build_pack_index(pack_dir)
    return json.dumps(
        {
            "schema_version": "1.0",
            "pass": declared_pass,
            "pack_date": "2026-08-14",
            "candidates_sha256": index.candidates_sha256,
            "game_totals_sha256": index.game_totals_sha256,
            "team_totals_sha256": index.team_totals_sha256,
            "verdicts": [
                {
                    "market_id": "mkt1",
                    "outcome_id": "out1",
                    "stream": "candidates",
                    "selection": "Player One Over 5.5",
                    "line": "5.5",
                    "price": "-110",
                    "book": "FD",
                    "verdict": "PASS",
                    "confidence": 0.5,
                    "recommended_units": 0,
                    "evidence": [],
                    "contradictions": [],
                    "kill_triggers": [],
                    "rejection_reasons": ["thin_edge"],
                }
            ],
            "slate_notes": [],
            "needs": [],
        }
    )


def _publish(pack_dir, output_text, pass_):
    return runner_common.publish_verdict_pass(
        pack_dir,
        output_text,
        pass_=pass_,
        request_sha256="r" * 64,
        candidates_sha256="c" * 64,
        game_totals_sha256="g" * 64,
        team_totals_sha256="t" * 64,
        model="test-model",
    )


def test_publish_rejects_an_envelope_declaring_another_pass(tmp_path, monkeypatch):
    """Nothing compared the envelope's `pass` to the invoking runner, so an A
    response tagged "D" would have published under D and corrupted E's citations."""
    monkeypatch.setattr(runner_common_paths(), "PROJECT_ROOT", tmp_path)
    pack_dir = _publishable_pack(tmp_path)
    output = _verdict_envelope_json(pack_dir, declared_pass="D")

    with pytest.raises(runner_common.RunnerError) as excinfo:
        _publish(pack_dir, output, "A")
    assert "declares pass 'D'" in str(excinfo.value)
    assert not (pack_dir / "verdicts" / "A").exists()


def test_publish_accepts_a_matching_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_common_paths(), "PROJECT_ROOT", tmp_path)
    pack_dir = _publishable_pack(tmp_path)
    result = _publish(pack_dir, _verdict_envelope_json(pack_dir, "A"), "A")
    assert result.publication_id
    assert (pack_dir / "verdicts" / "A" / result.publication_id / "verdicts.json").is_file()


def runner_common_paths():
    from outlier_scrapers import paths

    return paths


# ---------------------------------------------------------------------------
# E must not be handed upstream records the gate rejected.
# ---------------------------------------------------------------------------


def _publish_upstream(pack_dir, pass_name, records, violations):
    """Write a publication tree by hand: verdicts.json plus its violations.json."""
    pub_id = f"pub_{pass_name.lower()}"
    dest = pack_dir / "verdicts" / pass_name / pub_id
    dest.mkdir(parents=True)
    (dest / "verdicts.json").write_text(
        json.dumps({"pass": pass_name, "verdicts": records}), encoding="utf-8"
    )
    (dest / "violations.json").write_text(json.dumps(violations), encoding="utf-8")
    (pack_dir / "verdicts" / pass_name / "current.json").write_text(
        json.dumps({"publication_id": pub_id}), encoding="utf-8"
    )
    return pub_id


def _upstream_record(outcome_id, record_id, verdict="BET"):
    return {
        "record_id": record_id,
        "outcome_id": outcome_id,
        "verdict": verdict,
        "recommended_units": 1.0,
        "claim": "",
        "evidence": [],
    }


def test_rejected_upstream_records_are_hidden_from_pass_e(tmp_path):
    """A pass publishes when its reject ratio is under policy, so a publication
    can carry individually rejected records. E must not cite one as backing."""
    pack_dir = tmp_path / "packs" / "2026-08-14"
    pack_dir.mkdir(parents=True)
    _publish_upstream(
        pack_dir,
        "A",
        [_upstream_record("out_ok", "rec_ok"), _upstream_record("out_bad", "rec_bad")],
        [{"code": "line_tampered", "outcome_id": "out_bad", "severity": "reject"}],
    )

    pubs = runner_common.load_current_publications(pack_dir)
    pub = pubs["A"]
    assert "out_ok" in pub.bet_outcome_ids
    assert "out_bad" not in pub.bet_outcome_ids, "rejected outcome still offered to E"
    assert "rec_bad" not in pub.record_ids
    assert "out_bad" not in pub.stakes


def test_warn_only_violations_do_not_hide_an_upstream_record(tmp_path):
    """Only reject-severity violations disqualify a record; warns are telemetry."""
    pack_dir = tmp_path / "packs" / "2026-08-14"
    pack_dir.mkdir(parents=True)
    _publish_upstream(
        pack_dir,
        "A",
        [_upstream_record("out_ok", "rec_ok")],
        [{"code": "bet_missing_evidence", "outcome_id": "out_ok", "severity": "warn"}],
    )

    pub = runner_common.load_current_publications(pack_dir)["A"]
    assert "out_ok" in pub.bet_outcome_ids
    assert "rec_ok" in pub.record_ids
