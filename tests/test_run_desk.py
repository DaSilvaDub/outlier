"""Focused desk wiring tests for automated Prompt C support."""

import csv
import json

import pytest

from outlier_scrapers import pack, paths as pack_paths, run_desk


@pytest.fixture
def desk_pack(monkeypatch, tmp_path):
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_desk, "load_environment", lambda: None)
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
                "market_id": "m1",
                "market_type": "TOTAL",
                "selection": "OVER 8.5",
                "line": "8.5",
                "price": "-110",
            }
        )
        writer.writerow(row)
    return pack_dir


def _write_output(pack_dir, name, request_hash):
    (pack_dir / name).write_text(
        f"---\nrequest_sha256: {request_hash}\n---\nbody\n", encoding="utf-8"
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
                "card_id": "p1",
                "event_id": "EP",
                "market": "PTS",
                "matchup": "A @ B",
                "board": "B",
                "rank_value": 1.0,
                "headline_side": "OVER",
                "sides": {
                    "OVER": {
                        "outcome_id": "po",
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
                "card_id": "gm1",
                "board": "A",
                "rank_value": 9.0,
                "headline_side": "OVER",
                "sides": {
                    "OVER": {
                        "outcome_id": "go",
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
                "EG": {
                    "home_team_id": "T1",
                    "away_team_id": "T2",
                    "starts_at": "2099-07-07T23:10:00+00:00",
                }
            },
            "teams": {"T1": {"injuries": [{"player": "Hurt Guy"}]}},
        },
    }
    games_lm = {
        "generated_at": "GLM",
        "ev_records": [
            {
                "market_id": "gm1",
                "outcome_id": "go",
                "event_id": "EG",
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
                        "event_id": "EP",
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


def _mock_all_phase_runners(monkeypatch) -> None:
    def runner_for(phase):
        def run(pack_dir, **kwargs):
            _write_output(pack_dir, run_desk.PHASE_OUTPUTS[phase], phase.lower())
            return 0

        return run

    for phase in run_desk.PHASES:
        monkeypatch.setitem(run_desk.PHASE_RUNNERS, phase, runner_for(phase))


@pytest.fixture
def mlb_wnba_e2e_pack(monkeypatch, tmp_path):
    """Build a real MLB+WNBA pack, then run mocked A–E desk orchestration."""
    monkeypatch.setattr(run_desk.paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(run_desk, "load_environment", lambda: None)

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

    rows, target, games_norm = pack.build_pack(["MLB", "WNBA"], None, 15, 10)
    sports = {r["sport"] for r in rows}
    assert sports == {"MLB", "WNBA"}, f"pack build missing a league: {sports}"

    pack_dir = tmp_path / "packs" / target
    pack.write_pack(rows, pack_dir, games_norm_by_league=games_norm)

    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")
    _mock_all_phase_runners(monkeypatch)

    assert run_desk.orchestrate_desk(pack_dir) == 0
    return pack_dir


def test_c_is_wired_into_phase_maps():
    assert "C" in run_desk.PHASES
    assert run_desk.PHASE_OUTPUTS["C"] == "chatgpt_c.md"
    assert run_desk.PHASE_KEYS["C"] == "GEMINI_API_KEY"
    assert run_desk.PHASE_RUNNERS["C"] is run_desk.c_research.run_c_research


def test_cached_c_output_counts_as_cached(desk_pack, monkeypatch):
    _write_output(desk_pack, "chatgpt_c.md", "same")
    monkeypatch.setattr(
        run_desk.c_research,
        "run_c_research",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", run_desk.c_research.run_c_research)

    result = run_desk.run_phase("C", desk_pack)
    assert result["status"] == "cached"
    assert result["request_sha256"] == "same"


def test_failed_c_refresh_restores_previous_output(desk_pack, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    output = desk_pack / "chatgpt_c.md"
    output.write_bytes(b"previous")

    def fail_after_delete(*args, **kwargs):
        output.unlink()
        return 1

    monkeypatch.setitem(run_desk.PHASE_RUNNERS, "C", fail_after_delete)
    result = run_desk.run_phase("C", desk_pack)
    assert result["status"] == "failed"
    assert output.read_bytes() == b"previous"


def test_orchestrate_status_includes_game_totals(desk_pack, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")

    def runner_for(phase):
        def run(pack_dir, **kwargs):
            _write_output(pack_dir, run_desk.PHASE_OUTPUTS[phase], phase.lower())
            return 0

        return run

    for phase in run_desk.PHASES:
        monkeypatch.setitem(run_desk.PHASE_RUNNERS, phase, runner_for(phase))

    (desk_pack / "game_totals.csv").write_text("totals_id,market_id\nx,m1\n", encoding="utf-8")
    run_desk.orchestrate_desk(desk_pack)
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["game_totals"]["present"] is True
    assert status["game_totals"]["file"] == "game_totals.csv"


def test_default_orchestration_runs_c_and_writes_full_status(desk_pack, monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test-key")

    def runner_for(phase):
        def run(pack_dir, **kwargs):
            _write_output(pack_dir, run_desk.PHASE_OUTPUTS[phase], phase.lower())
            return 0

        return run

    for phase in run_desk.PHASES:
        monkeypatch.setitem(run_desk.PHASE_RUNNERS, phase, runner_for(phase))

    assert run_desk.orchestrate_desk(desk_pack) == 0
    status = json.loads((desk_pack / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert set(status["components"]) == set(run_desk.PHASES)
    assert status["components"]["C"]["status"] == "success"
    assert status["final_report"] == {"source": "claude_e", "file": "claude_e.md"}


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
    assert status["components"]["E"]["status"] == "gated-missing-input"


def test_mlb_wnba_e2e_pipeline(mlb_wnba_e2e_pack):
    """Combined MLB+WNBA pack through mocked desk A–E ends FULL with both leagues."""
    pack_dir = mlb_wnba_e2e_pack

    with (pack_dir / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        sports = {row["sport"] for row in csv.DictReader(handle)}
    assert sports == {"MLB", "WNBA"}

    assert (pack_dir / "briefing.md").exists()
    assert (pack_dir / "game_totals.csv").exists()
    for phase in run_desk.PHASES:
        assert (pack_dir / run_desk.PHASE_OUTPUTS[phase]).exists()

    status = json.loads((pack_dir / run_desk.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["overall"] == "FULL"
    assert set(status["components"]) == set(run_desk.PHASES)
    assert all(
        status["components"][phase]["status"] in run_desk.SUCCESS_STATES
        for phase in run_desk.PHASES
    )
    assert status["final_report"] == {"source": "claude_e", "file": "claude_e.md"}
    assert status["game_totals"]["present"] is True
    assert status["game_totals"]["file"] == "game_totals.csv"
