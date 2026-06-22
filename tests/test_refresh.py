from outlier_scrapers import refresh as refresh_mod


class FakeClient:
    pass


def test_refresh_skips_line_movement_when_same_league_props_fail(monkeypatch, capsys):
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


def test_refresh_cards_only_does_not_require_api_client(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("API client must not be constructed for --cards only")

    monkeypatch.setattr(refresh_mod, "OutlierApiClient", boom)

    def fake_cards(league):
        return {"coverage": {"cards_total": 5, "board_a_cards": 2, "board_b_cards": 3}}

    monkeypatch.setattr(refresh_mod, "export_cards_for_league", fake_cards)

    result = refresh_mod.main(["--league", "WNBA", "--cards"])

    captured = capsys.readouterr()
    assert result == 0
    assert "WNBA cards: exported 5 cards (Board A=2, Board B=3)" in captured.out
