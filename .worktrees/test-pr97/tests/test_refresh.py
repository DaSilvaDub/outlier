import json

import pytest

from outlier_scrapers import refresh as refresh_mod


class FakeClient:
    pass


def test_refresh_auth_failure_replaces_all_requested_statuses(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")

    def fail_auth():
        raise RuntimeError("session expired")

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", fail_auth)

    assert refresh_mod.main(["--league", "MLB", "--all"]) == 1
    reports = league_paths("MLB").reports
    for status_name in refresh_mod._PRODUCER_STATUS_FILES.values():
        status = json.loads((reports / status_name).read_text(encoding="utf-8"))
        assert status["status"] == "error"
        assert status["error"] == "session expired"


def test_refresh_skips_line_movement_when_same_league_props_fail(
    tmp_path, monkeypatch, capsys
):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    calls = {"line_movement": 0}

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)

    def fail_props(client, league):
        raise RuntimeError("temporary props failure")

    def line_movement(client, league):
        calls["line_movement"] += 1
        return {"record_count": 0, "markets_fetched": 0, "markets_requested": 0}

    monkeypatch.setattr(refresh_mod, "export_props_for_league", fail_props)
    monkeypatch.setattr(refresh_mod, "export_line_movement_for_league", line_movement)

    result = refresh_mod.main(["--league", "MLB", "--props", "--line-movement"])

    captured = capsys.readouterr()
    assert result == 1
    assert "MLB props: failed" in captured.out
    assert "MLB line movement: skipped (props failed)" in captured.out
    assert calls["line_movement"] == 0
    status = json.loads(
        (
            league_paths("MLB").reports / "line_movement_status_latest.json"
        ).read_text(encoding="utf-8")
    )
    assert status["status"] == "error"
    assert status["error"] == "skipped because props refresh failed"


def test_refresh_runs_line_movement_without_props_step(monkeypatch, capsys):
    calls = {"line_movement": 0}

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)

    def line_movement(client, league):
        calls["line_movement"] += 1
        return {"record_count": 2, "markets_fetched": 1, "markets_requested": 1}

    monkeypatch.setattr(refresh_mod, "export_line_movement_for_league", line_movement)

    result = refresh_mod.main(["--league", "MLB", "--line-movement"])

    captured = capsys.readouterr()
    assert result == 0
    assert "MLB line movement: exported 2 records from 1/1 markets" in captured.out
    assert calls["line_movement"] == 1


def test_refresh_runs_insights(monkeypatch, capsys):
    calls = {"insights": 0}

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)

    def insights(client, league):
        calls["insights"] += 1
        return {"record_count": 3}

    monkeypatch.setattr(refresh_mod, "export_insights_for_league", insights)

    result = refresh_mod.main(["--league", "WNBA", "--insights"])

    captured = capsys.readouterr()
    assert result == 0
    assert "WNBA insights: exported 3 insights" in captured.out
    assert calls["insights"] == 1


def test_refresh_flags_games_error_and_skips_game_cards(tmp_path, monkeypatch, capsys):
    """A games export that returns status=error must set a non-zero exit code and
    not rebuild game cards on top of empty data (Finding #1)."""
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    calls = {"game_cards": 0}

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)

    def games_error(client, league):
        return {
            "status": "error",
            "record_count": 0,
            "enrichment_count": 0,
            "fetch_errors": [{"step": "markets", "event_id": "e1"}],
            "fetch_error_count": 1,
        }

    def game_cards(league):
        calls["game_cards"] += 1
        return {"coverage": {"cards_total": 0}}

    monkeypatch.setattr(refresh_mod, "export_games_for_league", games_error)
    monkeypatch.setattr(refresh_mod, "export_game_cards_for_league", game_cards)

    result = refresh_mod.main(["--league", "MLB", "--games", "--game-cards"])

    captured = capsys.readouterr()
    assert result == 1
    assert "MLB games: error" in captured.out
    assert calls["game_cards"] == 0
    status = json.loads(
        (league_paths("MLB").reports / "games_cards_status_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["status"] == "error"


def test_refresh_cards_only_does_not_require_api_client(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("API client must not be constructed for --cards only")

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", boom)

    calls = {"game_cards": 0}

    def fake_cards(league):
        return {"coverage": {"cards_total": 5, "board_a_cards": 2, "board_b_cards": 3}}

    def fake_game_cards(league):
        calls["game_cards"] += 1
        return {"coverage": {"cards_total": 5}}

    monkeypatch.setattr(refresh_mod, "export_cards_for_league", fake_cards)
    monkeypatch.setattr(refresh_mod, "export_game_cards_for_league", fake_game_cards)

    result = refresh_mod.main(["--league", "WNBA", "--cards"])

    captured = capsys.readouterr()
    assert result == 0
    assert "WNBA cards: exported 5 cards (Board A=2, Board B=3)" in captured.out
    assert calls["game_cards"] == 0


def test_refresh_game_cards_only_does_not_require_api_client(monkeypatch, capsys):
    calls = {"cards": 0}

    def boom(*args, **kwargs):
        raise AssertionError("API client must not be constructed for --game-cards only")

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", boom)

    def fake_cards(league):
        calls["cards"] += 1
        return {"coverage": {"cards_total": 5, "board_a_cards": 2, "board_b_cards": 3}}

    def fake_game_cards(league):
        return {"coverage": {"cards_total": 8}}

    monkeypatch.setattr(refresh_mod, "export_cards_for_league", fake_cards)
    monkeypatch.setattr(refresh_mod, "export_game_cards_for_league", fake_game_cards)

    result = refresh_mod.main(["--league", "WNBA", "--game-cards"])

    captured = capsys.readouterr()
    assert result == 0
    assert "WNBA game cards: exported 8 cards" in captured.out
    assert calls["cards"] == 0


def test_refresh_skips_game_cards_when_game_line_movement_fails(
    tmp_path, monkeypatch, capsys
):
    """A game line-movement export that raises an exception must set the failed flag
    and skip game cards rebuild (Finding #2)."""
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    calls = {"game_cards": 0}

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)

    def fail_game_lm(client, league, source=None):
        raise RuntimeError("temporary game LM failure")

    def game_cards(league):
        calls["game_cards"] += 1
        return {"coverage": {"cards_total": 0}}

    monkeypatch.setattr(refresh_mod, "export_line_movement_for_league", fail_game_lm)
    monkeypatch.setattr(refresh_mod, "export_game_cards_for_league", game_cards)

    result = refresh_mod.main(["--league", "MLB", "--game-line-movement", "--game-cards"])

    captured = capsys.readouterr()
    assert result == 1
    assert "MLB game line movement: failed" in captured.out
    assert calls["game_cards"] == 0
    status = json.loads(
        (league_paths("MLB").reports / "games_cards_status_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["status"] == "error"


@pytest.mark.parametrize(
    ("flag", "export_name", "status_name"),
    [
        ("--props", "export_props_for_league", "props_export_status_latest.json"),
        ("--insights", "export_insights_for_league", "insights_status_latest.json"),
        (
            "--line-movement",
            "export_line_movement_for_league",
            "line_movement_status_latest.json",
        ),
        ("--games", "export_games_for_league", "games_status_latest.json"),
        (
            "--game-line-movement",
            "export_line_movement_for_league",
            "games_line_movement_status_latest.json",
        ),
        ("--cards", "export_cards_for_league", "cards_status_latest.json"),
        ("--game-cards", "export_game_cards_for_league", "games_cards_status_latest.json"),
    ],
)
def test_refresh_failure_replaces_exact_producer_success_status(
    tmp_path, monkeypatch, flag, export_name, status_name
):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(refresh_mod, "OutlierApiClient", FakeClient)
    status_path = league_paths("MLB").ensure().reports / status_name
    status_path.write_text(
        json.dumps(
            {
                "league": "MLB",
                "status": "ok",
                "generated_at": "2026-08-11T04:00:00-04:00",
            }
        ),
        encoding="utf-8",
    )

    def fail(*args, **kwargs):
        raise RuntimeError("x" * 500)

    monkeypatch.setattr(refresh_mod, export_name, fail)
    result = refresh_mod.main(["--league", "MLB", flag])

    assert result == 1
    persisted = json.loads(status_path.read_text(encoding="utf-8"))
    assert persisted["league"] == "MLB"
    assert persisted["status"] == "error"
    assert persisted["generated_at"] != "2026-08-11T04:00:00-04:00"
    assert len(persisted["error"]) == 300
    assert not list(status_path.parent.glob(f".{status_path.name}.*.tmp"))
