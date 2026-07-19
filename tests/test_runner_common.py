import csv
import hashlib
import io

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
