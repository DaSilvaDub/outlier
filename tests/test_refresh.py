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
