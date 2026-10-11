"""Tests for the fixed-slate snapshot harness (scripts/nfl_snapshot_diff.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import nfl_snapshot_diff as snap

REPO = Path(__file__).resolve().parent.parent


def test_record_key_uses_identity_fields_only():
    a = {"event_id": "e1", "player_name": "P", "market": "REC", "position": "OVER",
         "line": 5.5, "model_p": 0.6}
    b = dict(a, model_p=0.7)
    assert snap.record_key(a) == snap.record_key(b)
    assert snap.record_key(a) != snap.record_key(dict(a, line=6.5))


def test_diff_records_counts_added_removed_and_changed_fields():
    before = [{"event_id": "e1", "week": 1, "x": 1}, {"event_id": "e1", "week": 5, "x": 1}]
    after = [{"event_id": "e1", "week": 1, "x": 2}, {"event_id": "e1", "week": 2, "x": 1}]
    out = snap.diff_records(before, after)
    assert (out["count_before"], out["count_after"]) == (2, 2)
    assert (out["removed"], out["added"], out["changed"]) == (1, 1, 1)
    assert out["changed_fields"] == {"x": 1}
    assert out["removed_sample"] == ["event_id=e1|week=5"]


def _fake_capture(root: Path, files_by_step: list[dict[str, str]]) -> Path:
    """Build a capture from in-memory step contents via the real capture code."""
    work = root / "work"
    out = root / "cap" / "A"
    previous: dict[str, str] = {}
    steps = []
    for i, files in enumerate(files_by_step):
        for rel, text in files.items():
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        name = f"S{i}"
        steps.append(name)
        previous = snap._capture_step(work, out / name, previous, {}, {"summary_status": "OK"})
    (out / "meta.json").write_text(json.dumps({"steps": steps, "git_head": root.name}))
    return root / "cap"


def test_diff_captures_reports_rewritten_files_and_count_changes(tmp_path):
    full = json.dumps({"count": 3, "records": [{"event_id": f"e{i}"} for i in range(3)]})
    part = json.dumps({"count": 1, "records": [{"event_id": "e0"}]})
    before = _fake_capture(tmp_path / "b", [{"data/x_2026.json": full}, {"data/x_2026.json": part}])
    after = _fake_capture(tmp_path / "a", [
        {"data/x_2026.json": full},
        {"data/x_2026_1pm.json": part},
    ])
    report = snap.diff_captures(before, after)
    s1 = report["slates"]["A"]["steps"]["S1"]
    assert s1["only_after"] == ["data/x_2026_1pm.json"]
    changed = s1["changed"]["data/x_2026.json"]
    assert (changed["count_before"], changed["count_after"]) == (1, 3)
    assert changed["lists"]["records"]["added"] == 2
    assert "data/x_2026.json" in s1["written_before"]
    assert "data/x_2026.json" not in s1["written_after"]
    assert snap.has_differences(report)
    md = snap.render_markdown(report)
    assert "x_2026_1pm.json" in md and "count 1 → 3" in md


def test_frozen_nflverse_future_rows_switch():
    with_future = snap.frozen_nflverse(True)
    pregame = snap.frozen_nflverse(False)
    weeks = {int(r["week"]) for r in with_future["nextgen_stats/ngs_receiving.csv"]}
    assert weeks == set(range(1, 10)) | {18}
    for name, table in pregame.items():
        if name == "/games.csv":
            # A pregame download still lists upcoming games, just without results.
            assert all(r["home_score"] == "" for r in table if int(r["week"]) >= 4)
        else:
            assert all(int(r.get("week") or 0) <= 3 for r in table)
    week4 = [r for r in with_future["/games.csv"] if r["week"] == "4"]
    assert {(r["home_team"], r["away_team"]) for r in week4} >= {("KC", "BAL"), ("LAR", "SF"), ("PHI", "DAL")}
    assert all(r["gameday"] == "2026-10-04" for r in week4)
    kelce = [int(r["receiving_yards"]) for r in with_future["stats_player/stats_player_week_"]
             if r["player_display_name"] == "Travis Kelce"]
    assert round(sum(kelce[:3]) / 3, 1) == 66.7 and round(sum(kelce) / 9, 1) == 175.0


def test_frozen_client_moves_fixture_events_three_weeks():
    client = snap.FrozenOutlierClient(REPO / "tests" / "fixtures" / "nfl")
    times = sorted(e["scheduledTime"] for e in client.fetch_schedule()["events"])
    assert times[0].startswith("2026-10-04T17:00:00")
    assert times[-1].startswith("2026-10-05T00:20:00")


@pytest.mark.parametrize("slate", ["A", "B"])
def test_capture_is_deterministic(tmp_path, slate):
    for name in ("one", "two"):
        snap.capture(REPO, tmp_path / name, [slate], work=tmp_path / f"work_{name}")
    report = snap.diff_captures(tmp_path / "one", tmp_path / "two")
    assert not snap.has_differences(report), snap.render_markdown(report)
    steps = json.loads((tmp_path / "one" / slate / "meta.json").read_text())["steps"]
    assert steps == [s["name"] for s in snap.STEPS[slate]]
    for step in steps:
        inv = json.loads((tmp_path / "one" / slate / step / "inventory.json").read_text())
        assert inv["files"], f"{step} captured no files"


def test_every_nflverse_url_the_pipeline_requests_has_a_frozen_table():
    """A renamed upstream URL must fail here, not silently turn a source UNAVAILABLE."""
    from outlier_nfl import usage
    from outlier_nfl.external import ngs, pbp, schedule

    tables = snap.frozen_nflverse(True)
    urls = [schedule.SCHEDULE_URL, pbp.PBP_URL.format(season=2026),
            usage.PLAYER_WEEK_URL.format(season=2026), usage.EXPECTED_URL.format(season=2026)]
    urls += [ngs.NGS_URL.format(kind=k) for k in ("passing", "rushing", "receiving")]
    for url in urls:
        assert snap._rows_for(url, tables, ()), url


def test_bundle_manifest_hashes_and_sizes_are_scrubbed():
    manifest = {"run_id": "RUN-X", "artifacts": [
        {"name": "summary.json", "sha256": "ab" * 32, "bytes": 2822, "published_to": []},
    ]}
    out = json.loads(snap._scrub_bundle_hashes("data/NFL/runs/RUN-X/manifest.json",
                                               json.dumps(manifest)))
    assert out["artifacts"][0]["sha256"] == "<SHA256>"
    assert out["artifacts"][0]["bytes"] == "<BYTES>"
    # Other files keep their own "bytes" fields untouched.
    other = json.dumps({"bytes": 5})
    assert snap._scrub_bundle_hashes("data/NFL/normalized/x.json", other) == other


def test_phase5a_steps_and_placeholder_auth_cases():
    names = [s["name"] for s in snap.STEPS["A"] + snap.STEPS["B"]]
    assert "A8_auth_extraction" in names
    b = [s["name"] for s in snap.STEPS["B"]]
    assert b.index("B10_forecast_partial_window") < b.index("B4_after_kickoff")
    cases = snap.auth_cases()
    assert {c for c, (_, exp) in cases.items() if exp} == {"nested_auth_entry", "jwt_access_token"}
    assert snap.frozen_forecast("empty", "x")["hourly"]["time"] == []
    url = "https://api.open-meteo.com/v1/forecast?start_date=2026-10-04&end_date=2026-10-04"
    assert snap.frozen_forecast("partial", url)["hourly"]["time"] == ["2026-10-04T17:00"]


def test_auth_step_records_labels_never_values(tmp_path):
    snap._run_auth_step(tmp_path)
    out = json.loads((tmp_path / "data/NFL/math/auth_extraction.json").read_text("utf-8"))
    assert set(out) == set(snap.auth_cases())
    assert set(out.values()) <= {"none", "expected", "other"}


def test_phase5b_steps_do_not_collide_with_phase4a_names():
    names = [s["name"] for s in snap.STEPS["A"] + snap.STEPS["B"]]
    assert "A10_tape_admissibility" in names and "A11_roster_provenance" in names
    assert not any(n.startswith(("A9_", "B11_")) for n in names if "tape" in n or "roster" in n)


def test_tape_and_roster_steps_write_their_reports(tmp_path):
    snap._run_tape_step(tmp_path)
    snap._run_roster_step(tmp_path)
    tape = json.loads((tmp_path / "data/NFL/math/tape_admissibility.json").read_text("utf-8"))
    assert set(tape["roles"]) >= {"point_in_time", "future_update", "wrong_season", "unstamped"}
    assert tape["admission"]["fresh"] == "admitted"
    assert (tmp_path / "data/NFL/math/roster_provenance.json").exists()


def test_phase4b_steps_follow_a11_and_b11():
    assert snap.HARNESS_VERSION >= 9
    a = [s["name"] for s in snap.STEPS["A"]]
    b = [s["name"] for s in snap.STEPS["B"]]
    assert a.index("A12_identity_joins") == a.index("A11_roster_provenance") + 1
    assert a.index("A13_schema_integrity") == a.index("A12_identity_joins") + 1
    assert b.index("B12_identity_extras") == b.index("B11_market_scope") + 1
    assert len(set(a)) == len(a) and len(set(b)) == len(b)


def test_identity_and_schema_steps_write_their_reports(tmp_path):
    assert snap._run_identity_step(tmp_path) is None
    assert snap._run_schema_step(tmp_path) is None
    math = tmp_path / "data" / "NFL" / "math"
    ident = json.loads((math / "identity_joins.json").read_text(encoding="utf-8"))
    assert set(ident) == {"props", "boxscore_same_name", "projection_index"}
    schema = json.loads((math / "schema_integrity.json").read_text(encoding="utf-8"))
    assert set(schema) == {"tape", "boxscore", "normalized"}


def test_identity_extras_add_foreign_and_duplicate_props():
    client = snap.FrozenOutlierClient(REPO / "tests" / "fixtures" / "nfl", identity_extras=True)
    base = snap.FrozenOutlierClient(REPO / "tests" / "fixtures" / "nfl")
    assert len(client._props["props"]) == len(base._props["props"]) + 3


def test_phase6_steps_follow_a13(tmp_path):
    assert snap.HARNESS_VERSION >= 10
    a = [s["name"] for s in snap.STEPS["A"]]
    i = a.index("A13_schema_integrity")
    assert a[i + 1:i + 4] == ["A14_close_ownership", "A15_close_provenance", "A16_settle_clv"]
    assert snap._run_close_join_step(tmp_path) is None
    assert snap._run_close_provenance_step(tmp_path) is None
    math = tmp_path / "data" / "NFL" / "math"
    assert (math / "close_ownership.json").exists() and (math / "close_provenance.json").exists()
    assert len(snap._SETTLE_ROWS["clv"]) == 4


def test_phase7a_steps_follow_a16(tmp_path):
    assert snap.HARNESS_VERSION == 11
    a = [s["name"] for s in snap.STEPS["A"]]
    i = a.index("A16_settle_clv")
    assert a[i + 1:i + 3] == ["A17_alt_floor_eligibility", "A18_export_explicit_date"]
    assert snap._run_alt_floor_step(tmp_path) is None
    assert snap._run_export_pack_step(tmp_path, Path(snap.__file__).resolve().parents[1]) is None
    math = tmp_path / "data" / "NFL" / "math"
    assert (math / "alt_floor_eligibility.json").exists()
    assert (math / "export_explicit_date.json").exists()
