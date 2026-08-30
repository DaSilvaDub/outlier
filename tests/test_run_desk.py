"""Focused desk wiring tests for automated Prompt C support and PR2 parallel execution."""

import csv
import json
import threading

import pytest

from outlier_scrapers import desk_snapshot, pack, paths as pack_paths, run_desk
from outlier_scrapers import runner_common as rc
from outlier_scrapers import verdicts
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER
from outlier_scrapers.stage_result import (
    ArtifactState,
    StageErrorCode,
    StageExecutionState,
)


def _healthy_feed_health() -> dict:
    return {
        "props_status": "ok",
        "games_status": "ok",
        "insights_status": "ok",
        "injuries_status": "ok",
        "line_movement_status": "ok",
        "game_line_movement_status": "ok",
        "cards_status": "ok",
        "coverage_pct": 100.0,
        "oldest_source_age": 0.2,
        "latest_source_age": 0.1,
        "failed_ids": [],
        "schema_version": "1.0",
    }


def _write_prompts(root) -> None:
    prompts_dir = root / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("A.md", "B.md", "C.md", "D.md", "E.md"):
        (prompts_dir / name).write_text(f"Prompt {name} body", encoding="utf-8")


@pytest.fixture
def desk_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_desk, "load_environment", lambda: None)
    _write_prompts(tmp_path)
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
                "_event_starts_at": "2099-01-01T12:00:00Z",
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
                "team_name": "A",
                "opp_name": "B",
                "matchup": "A @ B",
            }
        )
        writer.writerow(row)
    return pack_dir


def _set_all_keys(monkeypatch) -> None:
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")


def _write_output(pack_dir, name, request_hash):
    (pack_dir / name).write_text(
        f"---\nrequest_sha256: {request_hash}\n---\nbody\n", encoding="utf-8"
    )


def _publish_stub(pack_dir, phase: str, request_hash: str) -> None:
    """Publish a real, gate-clean, empty-list publication for `phase`.

    Uses the module's own real `expected_request_sha256` value as
    `request_hash` so PR1's publication validation (manifest/file hashes,
    pack-identity match, request-hash match) reports it CURRENT exactly the
    way a real provider response would.
    """
    identity = rc.load_pack_identity(pack_dir)
    base = {
        "schema_version": verdicts.SCHEMA_VERSION,
        "pack_date": pack_dir.name,
        "candidates_sha256": identity.candidates_sha256,
        "game_totals_sha256": identity.game_totals_sha256,
        "team_totals_sha256": identity.team_totals_sha256,
    }
    common = dict(
        request_sha256=request_hash,
        candidates_sha256=identity.candidates_sha256,
        game_totals_sha256=identity.game_totals_sha256,
        team_totals_sha256=identity.team_totals_sha256,
        model="fake",
    )
    if phase == "C":
        envelope = {**base, "pass": "C", "findings": [], "no_sourced_findings": True}
        rc.publish_finding_pass(pack_dir, json.dumps(envelope), **common)
    elif phase == "E":
        docs = rc.load_current_publication_documents(pack_dir)
        upstream_ids = {
            name: (docs[name].publication_id if name in docs else None)
            for name in ("A", "D", "B", "C")
        }
        envelope = {
            **base,
            "pass": "E",
            "upstream_publication_ids": upstream_ids,
            "reconciliations": [],
            "slate_notes": [],
            "needs": [],
        }
        rc.publish_reconciliation_pass(pack_dir, json.dumps(envelope), **common)
    else:
        envelope = {**base, "pass": phase, "verdicts": [], "slate_notes": [], "needs": []}
        rc.publish_verdict_pass(pack_dir, json.dumps(envelope), pass_=phase, **common)


def _runner_for(phase, *, barrier: threading.Barrier | None = None, order: list | None = None):
    def run(pack_dir, **kwargs):
        if barrier is not None:
            barrier.wait(timeout=5)
        if order is not None:
            order.append(phase)
        module = run_desk.PHASE_MODULES[phase]
        req_hash = module.expected_request_sha256(pack_dir)
        _publish_stub(pack_dir, phase, req_hash)
        _write_output(pack_dir, run_desk.PHASE_OUTPUTS[phase], req_hash)
        return 0

    return run


def _mock_all_phase_runners(monkeypatch, *, barrier=None, order=None) -> None:
    for phase in run_desk.PHASES:
        monkeypatch.setitem(
            run_desk.PHASE_RUNNERS, phase, _runner_for(phase, barrier=barrier, order=order)
        )


def _seed_league_data(root, league: str) -> None:
    """Minimal multi-stream league tree for offline pack builds (self-contained)."""
    low = league.lower()
    (root / "cards").mkdir(parents=True, exist_ok=True)
    (root / "normalized").mkdir(parents=True, exist_ok=True)
    player_cards = {
        "generated_at": "PC",
        "board_a": [],
        "board_b": [
            {
                "card_id": f"p1_{low}",
                "event_id": f"EP_{low}",
                "market": "PTS",
                "matchup": "A @ B",
                "board": "B",
                "rank_value": 1.0,
                "headline_side": "OVER",
                "sides": {
                    "OVER": {
                        "outcome_id": f"po_{low}",
                        "line": 5.5,
                        "best_odds": -110,
                        "ev": None,
                    }
                },
            }
        ],
    }
    game_cards = {
        "generated_at": "GC",
        "board_a": [
            {
                "card_id": f"gm1_{low}",
                "board": "A",
                "rank_value": 9.0,
                "headline_side": "OVER",
                "sides": {
                    "OVER": {
                        "outcome_id": f"go_{low}",
                        "line": 8.5,
                        "best_odds": None,
                        "ev": {
                            "is_alt_line_fallback": False,
                            "devig_decimal": 2.0,
                            "best_ev_pct": 0.05,
                            "kelly_pct": 0.02,
                        },
                    }
                },
            }
        ],
        "board_b": [],
        "context": {
            "events": {
                f"EG_{low}": {
                    "home_team_id": f"T1_{low}",
                    "away_team_id": f"T2_{low}",
                    "starts_at": "2099-07-07T23:10:00+00:00",
                }
            },
            "teams": {f"T1_{low}": {"injuries": [{"player": "Hurt Guy"}]}},
        },
    }
    games_lm = {
        "generated_at": "GLM",
        "ev_records": [
            {
                "market_id": f"gm1_{low}",
                "outcome_id": f"go_{low}",
                "event_id": f"EG_{low}",
                "market": "TOTAL",
                "market_type": "GAMELINE",
                "book": "FD",
                "book_odds": 110,
                "book_decimal_odds": 2.1,
                "calculated_ev_pct": 0.05,
            }
        ],
    }
    (root / "cards" / f"{low}_cards_latest.json").write_text(
        json.dumps(player_cards), encoding="utf-8"
    )
    (root / "cards" / f"{low}_games_cards_latest.json").write_text(
        json.dumps(game_cards), encoding="utf-8"
    )
    (root / "normalized" / f"{low}_line_movement_latest.json").write_text(
        json.dumps({"generated_at": "LM", "ev_records": []}), encoding="utf-8"
    )
    (root / "normalized" / f"{low}_games_line_movement_latest.json").write_text(
        json.dumps(games_lm), encoding="utf-8"
    )
    (root / "normalized" / f"{low}_props_latest.json").write_text(
        json.dumps(
            {
                "generated_at": "PN",
                "records": [
                    {
                        "event_id": f"EP_{low}",
                        "sport_context": {
                            "event_starts_at": "2099-07-07T23:10:00+00:00"
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "normalized" / f"{low}_games_latest.json").write_text(
        json.dumps({"generated_at": "GN", "context": game_cards["context"]}),
        encoding="utf-8",
    )


def test_c_is_wired_into_phase_maps():
    assert "C" in run_desk.PHASES
    assert run_desk.PHASE_OUTPUTS["C"] == "chatgpt_c.md"
    assert run_desk.PHASE_KEYS["C"] == "GEMINI_API_KEY"
    assert run_desk.PHASE_RUNNERS["C"] is run_desk.c_research.run_c_research
    assert run_desk.PHASE_MODULES["C"] is run_desk.c_research


# --------------------------------------------------------------------------
# run_phase: caching, forcing, failure mapping
# --------------------------------------------------------------------------


def test_cached_publication_counts_as_cache_hit(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    req_hash = run_desk.c_research.expected_request_sha256(desk_pack)
    _publish_stub(desk_pack, "C", req_hash)

    result = run_desk.run_phase("C", desk_pack)
    assert result.cache_hit is True
    assert result.execution_state is StageExecutionState.SUCCEEDED
    assert result.artifact_state is ArtifactState.CURRENT
    assert result.request_sha256 == req_hash
    assert result.forced is False


def test_forced_run_with_unchanged_hash_is_not_a_cache_hit(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    req_hash = run_desk.c_research.expected_request_sha256(desk_pack)
    _publish_stub(desk_pack, "C", req_hash)
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", _runner_for("C"))

    result = run_desk.run_phase("C", desk_pack, force=True)
    assert result.forced is True
    assert result.cache_hit is False
    assert result.execution_state is StageExecutionState.SUCCEEDED
    assert result.request_sha256 == req_hash


def test_missing_api_key_is_skipped_with_no_api_key_code(desk_pack, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = run_desk.run_phase("C", desk_pack)
    assert result.execution_state is StageExecutionState.SKIPPED
    assert result.error_code is StageErrorCode.NO_API_KEY


def test_missing_briefing_maps_to_missing_input(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    (desk_pack / "briefing.md").unlink()
    result = run_desk.run_phase("C", desk_pack)
    assert result.execution_state is StageExecutionState.FAILED
    assert result.error_code is StageErrorCode.MISSING_INPUT


def test_runner_failure_without_publication_is_internal_error(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", lambda *a, **k: 1)
    result = run_desk.run_phase("C", desk_pack)
    assert result.execution_state is StageExecutionState.FAILED
    assert result.error_code is StageErrorCode.INTERNAL_ERROR
    assert result.artifact_state is not ArtifactState.CURRENT


def test_committed_publication_survives_markdown_render_failure(desk_pack, monkeypatch):
    """A publish that commits, followed by a legacy Markdown write failure,
    is still a SUCCEEDED StageResult flagged COMPATIBILITY_ARTIFACT_FAILED."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def run(pack_dir, **kwargs):
        req_hash = run_desk.c_research.expected_request_sha256(pack_dir)
        _publish_stub(pack_dir, "C", req_hash)
        # Markdown render step fails; legacy runners report this as exit 1
        # even though the publication already committed.
        return 1

    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", run)
    result = run_desk.run_phase("C", desk_pack)
    assert result.execution_state is StageExecutionState.SUCCEEDED
    assert result.artifact_state is ArtifactState.CURRENT
    assert result.error_code is StageErrorCode.COMPATIBILITY_ARTIFACT_FAILED
    assert result.publication_id


# --------------------------------------------------------------------------
# run_independent_phases: concurrency
# --------------------------------------------------------------------------


def test_run_independent_phases_rejects_e():
    with pytest.raises(ValueError):
        run_desk.run_independent_phases(["A", "E"], object())


def test_run_independent_phases_rejects_unknown():
    with pytest.raises(ValueError):
        run_desk.run_independent_phases(["Z"], object())


def test_run_independent_phases_rejects_nonpositive_workers(desk_pack):
    with pytest.raises(ValueError):
        run_desk.run_independent_phases(["A"], desk_pack, max_workers=0)


def test_run_independent_phases_returns_canonical_order(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    results = run_desk.run_independent_phases(["D", "A", "C", "B"], desk_pack)
    assert list(results) == ["A", "B", "C", "D"]


def test_all_independent_phases_actually_overlap(desk_pack, monkeypatch):
    """A shared 4-party barrier proves real concurrency, not sequential runs:
    if any phase ran strictly before another finished, the later phase's
    wait() would time out (each barrier.wait needs all 4 to arrive)."""
    _set_all_keys(monkeypatch)
    barrier = threading.Barrier(4)
    order: list[str] = []
    _mock_all_phase_runners(monkeypatch, barrier=barrier, order=order)

    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)

    assert set(order) == {"A", "B", "C", "D"}
    assert set(results) == {"A", "B", "C", "D"}
    for phase, result in results.items():
        assert result.execution_state is StageExecutionState.SUCCEEDED, phase


def test_one_phase_failing_does_not_block_the_others(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "B", lambda *a, **k: 1)

    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)

    assert set(results) == {"A", "B", "C", "D"}
    assert results["B"].execution_state is StageExecutionState.FAILED
    for phase in ("A", "C", "D"):
        assert results[phase].execution_state is StageExecutionState.SUCCEEDED


def test_uncaught_worker_exception_becomes_internal_error(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)

    def boom(pack_dir, **kwargs):
        raise RuntimeError("boom")

    # Patch run_phase itself so the exception escapes the try/except inside
    # run_phase and is only caught by run_independent_phases' future.result().
    monkeypatch.setattr(
        run_desk,
        "run_phase",
        lambda phase, pack_dir, **kw: boom(pack_dir) if phase == "B" else _real_run_phase(phase, pack_dir, **kw),
    )
    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)
    assert results["B"].execution_state is StageExecutionState.FAILED
    assert results["B"].error_code is StageErrorCode.INTERNAL_ERROR
    for phase in ("A", "C", "D"):
        assert results[phase].execution_state is StageExecutionState.SUCCEEDED


_real_run_phase = run_desk.run_phase


# --------------------------------------------------------------------------
# Synthesis gating (E)
# --------------------------------------------------------------------------


def test_e_gated_when_upstream_not_ready(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    called = {"n": 0}

    def should_not_run(*args, **kwargs):
        called["n"] += 1
        return 0

    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "E", should_not_run)
    result = run_desk.run_synthesis_phase(desk_pack, {})
    assert result.execution_state is StageExecutionState.GATED
    assert result.error_code is StageErrorCode.MISSING_INPUT
    assert called["n"] == 0


def test_stale_adb_publications_cannot_unlock_e(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    run_desk.run_independent_phases(("A", "B", "D"), desk_pack)

    # Live pack changes after A/B/D published -> their publications go STALE.
    raw = (desk_pack / "candidates.csv").read_text(encoding="utf-8")
    (desk_pack / "candidates.csv").write_text(
        raw.replace("Player One Over 5.5", "Player One Over 6.5"), encoding="utf-8"
    )
    assert desk_snapshot.upstream_ready_for_e(desk_pack, {}) is False


def test_optional_c_failure_does_not_block_e(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", lambda *a, **k: 1)

    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)
    assert results["C"].execution_state is StageExecutionState.FAILED
    assert desk_snapshot.upstream_ready_for_e(desk_pack, results) is True

    e_result = run_desk.run_synthesis_phase(desk_pack, results)
    assert e_result.execution_state is StageExecutionState.SUCCEEDED


def test_e_only_starts_after_readiness_gate_passes(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    call_order: list[str] = []
    original_ready = desk_snapshot.upstream_ready_for_e

    def spy_ready(pack_dir, results, **kw):
        call_order.append("gate")
        return original_ready(pack_dir, results, **kw)

    def spy_e_runner(pack_dir, **kwargs):
        call_order.append("e_ran")
        req_hash = run_desk.claude_synthesis.expected_request_sha256(pack_dir)
        _publish_stub(pack_dir, "E", req_hash)
        _write_output(pack_dir, run_desk.PHASE_OUTPUTS["E"], req_hash)
        return 0

    monkeypatch.setattr(desk_snapshot, "upstream_ready_for_e", spy_ready)
    _mock_all_phase_runners(monkeypatch)
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "E", spy_e_runner)

    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)
    run_desk.run_synthesis_phase(desk_pack, results)

    assert call_order.index("gate") < call_order.index("e_ran")


# --------------------------------------------------------------------------
# resolve_final_report: snapshot-first authority
# --------------------------------------------------------------------------


def test_stale_e_markdown_is_not_authoritative_without_a_snapshot(desk_pack):
    (desk_pack / "claude_e.md").write_text(
        "---\nrequest_sha256: whatever\n---\nstale prose\n", encoding="utf-8"
    )
    resolution = run_desk.resolve_final_report(desk_pack)
    assert resolution.authoritative is False


def test_final_report_authoritative_once_snapshot_advances(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    results = run_desk.run_independent_phases(list(run_desk.INDEPENDENT_PHASES), desk_pack)
    run_desk.run_synthesis_phase(desk_pack, results)
    desk_snapshot.maybe_advance_desk(desk_pack, hold_locks=False)

    resolution = run_desk.resolve_final_report(desk_pack)
    assert resolution.authoritative is True
    assert resolution.source == "claude_e"
    assert resolution.compatibility_artifact == desk_pack / "claude_e.md"


# --------------------------------------------------------------------------
# End-to-end orchestration
# --------------------------------------------------------------------------


def test_orchestrate_status_includes_game_totals(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)
    with (desk_pack / "game_totals.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GAME_TOTALS_HEADER)
        writer.writeheader()
    run_desk.orchestrate_desk(desk_pack)
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["game_totals"]["present"] is True
    assert status["game_totals"]["file"] == "game_totals.csv"


def test_default_orchestration_runs_c_and_writes_full_status(desk_pack, monkeypatch):
    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)

    assert run_desk.orchestrate_desk(desk_pack) == 0
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert set(status["components"]) == set(run_desk.PHASES)
    assert status["components"]["C"]["execution_state"] == "succeeded"
    assert status["final_report"]["authoritative"] is True
    assert status["final_report"]["source"] == "claude_e"


def test_manual_report_includes_game_totals(desk_pack):
    (desk_pack / "game_totals.csv").write_text(
        "sport,market_id,edge_pct\nMLB,gm1,0.05\n", encoding="utf-8"
    )
    path = run_desk.produce_manual_betting_report(desk_pack)
    text = path.read_text(encoding="utf-8")
    assert "## Game Totals (projection board)" in text
    assert "gm1" in text


def test_manual_report_drops_locked_events_and_writes_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    pack_dir = tmp_path / "packs" / "2026-06-28"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE", encoding="utf-8")
    with (pack_dir / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=pack.CANDIDATES_HEADER)
        writer.writeheader()
        for mid, start in (
            ("PREGAME_MKT", "2099-01-01T00:00:00Z"),
            ("LOCKED_MKT", "2000-01-01T00:00:00Z"),
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

    path = run_desk.produce_manual_betting_report(pack_dir)
    text = path.read_text(encoding="utf-8")
    # Only the provably-pregame candidate is quoted; the started one is dropped.
    assert "PREGAME_MKT" in text
    assert "LOCKED_MKT" not in text
    # Atomic write leaves no partial temp file behind.
    assert list(pack_dir.glob("manual_betting_report_tmp_*")) == []


def test_local_synthesize_includes_game_totals(desk_pack):
    (desk_pack / "game_totals.csv").write_text("sport,market_id\nMLB,gm1\n", encoding="utf-8")
    text = run_desk.local_synthesize_inputs(desk_pack)
    assert "GAME_TOTALS.CSV" in text
    assert "gm1" in text


def test_orchestrate_desk_forwards_hold_locks(desk_pack, monkeypatch):
    seen: dict[str, object] = {}

    def fake_maybe_advance(pack_dir, **kwargs):
        seen["pack_dir"] = pack_dir
        seen["kwargs"] = kwargs
        return None

    monkeypatch.setattr(
        "outlier_scrapers.desk_snapshot.maybe_advance_desk", fake_maybe_advance
    )
    run_desk.orchestrate_desk(desk_pack, steps=["E"], hold_locks=False)
    assert seen["pack_dir"] == desk_pack
    assert seen["kwargs"].get("hold_locks") is False


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
    assert status["components"]["E"]["execution_state"] == "gated"


def test_mlb_wnba_e2e_pipeline(monkeypatch, tmp_path):
    """Combined MLB+WNBA pack through mocked desk A-E ends FULL with both leagues."""
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_desk, "load_environment", lambda: None)
    _write_prompts(tmp_path)

    def fake_lp(lg):
        root = tmp_path / "data" / lg.upper()
        return pack_paths.LeaguePaths(
            league=lg.upper(),
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            reports=root / "reports",
        )

    for league in ("MLB", "WNBA"):
        _seed_league_data(tmp_path / "data" / league, league)
    monkeypatch.setattr("outlier_scrapers.pack.paths.league_paths", fake_lp)
    health_by_league = {league: _healthy_feed_health() for league in ("MLB", "WNBA")}

    rows, target, games_norm, coverage = pack.build_pack_with_coverage(
        ["MLB", "WNBA"], None, 15, 10, feed_health_by_league=health_by_league
    )
    sports = {r["sport"] for r in rows}
    assert sports == {"MLB", "WNBA"}, f"pack build missing a league: {sports}"

    pack_dir = tmp_path / "packs" / target
    pack.write_pack(
        rows,
        pack_dir,
        games_norm_by_league=games_norm,
        coverage=coverage,
        feed_health_by_league=health_by_league,
    )

    _set_all_keys(monkeypatch)
    _mock_all_phase_runners(monkeypatch)

    assert run_desk.orchestrate_desk(pack_dir) == 0

    with (pack_dir / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        seen_sports = {row["sport"] for row in csv.DictReader(handle)}
    assert seen_sports == {"MLB", "WNBA"}

    assert (pack_dir / "briefing.md").exists()
    assert (pack_dir / "game_totals.csv").exists()
    for phase in run_desk.PHASES:
        assert (pack_dir / run_desk.PHASE_OUTPUTS[phase]).exists()

    status = json.loads((pack_dir / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert set(status["components"]) == set(run_desk.PHASES)
    assert all(
        status["components"][phase]["execution_state"] == "succeeded"
        for phase in run_desk.PHASES
    )
    assert status["final_report"]["authoritative"] is True
    assert status["final_report"]["source"] == "claude_e"
    assert status["game_totals"]["present"] is True
    assert status["game_totals"]["file"] == "game_totals.csv"


def test_cli_rejects_nonpositive_max_workers(desk_pack, capsys):
    with pytest.raises(SystemExit):
        run_desk.main(["--date", desk_pack.name, "--max-workers", "0"])
