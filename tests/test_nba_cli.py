import json
from datetime import timedelta

import outlier_nba.cli as cli
from tests import nba_fixtures as fx

NOW = fx.STARTED_TIP + timedelta(minutes=30)


def test_capture_command_prints_the_run_summary_and_returns_its_exit_code(
    tmp_path, monkeypatch, capsys
):
    seen = {}

    def fake_run_capture(client, **kwargs):
        seen.update(kwargs)
        return cli.PipelineResult(
            run_id="r1", status="OK", exit_code=0, published=True, withheld_reason=None, receipts=[]
        )

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(cli, "run_capture", fake_run_capture)
    db = tmp_path / "ledger.sqlite3"
    code = cli.main(["capture", "--db", str(db), "--nba-dir", str(tmp_path / "nba")])
    assert code == 0
    assert seen["db_path"] == db and seen["fixture"] is False
    assert json.loads(capsys.readouterr().out)["run_id"] == "r1"


def test_capture_command_returns_three_when_publication_is_withheld(tmp_path, monkeypatch):
    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", lambda: "client")
    monkeypatch.setattr(
        cli,
        "run_capture",
        lambda client, **kw: cli.PipelineResult("r2", "FAILED", 3, False, "x", []),
    )
    assert cli.main(["capture", "--db", str(tmp_path / "l.sqlite3")]) == 3


def test_capture_command_without_auth_exits_one(tmp_path, monkeypatch, capsys):
    def boom():
        raise RuntimeError("no session")

    monkeypatch.setattr("outlier_scrapers.api.OutlierApiClient", boom)
    assert cli.main(["capture", "--db", str(tmp_path / "l.sqlite3")]) == 1
    assert "auth_required" in capsys.readouterr().out


def test_settle_command_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_settle", lambda db, lookback_days: {"settlement_rows": 2})
    assert cli.main(["settle", "--db", str(tmp_path / "l.sqlite3"), "--lookback-days", "5"]) == 0
    assert json.loads(capsys.readouterr().out) == {"settlement_rows": 2}


def test_capture_with_no_open_games_is_an_empty_ok_run_that_records_nothing(tmp_path):
    sched = {"events": []}
    result = cli.run_capture(
        fx.FakeClient(sched=sched),
        db_path=tmp_path / "ledger.sqlite3",
        nba_dir=tmp_path / "nba",
        clock=fx.clock_at(NOW),
    )
    # schedule EMPTY, props zero but no open events -> nothing is INCOMPLETE
    assert result.exit_code == 0
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_fixture_runs_refuse_the_shared_default_ledger(monkeypatch, capsys):
    from outlier_scrapers import paths

    monkeypatch.setattr(
        "outlier_scrapers.api.OutlierApiClient", lambda: (_ for _ in ()).throw(AssertionError)
    )
    assert cli.main(["capture", "--fixture", "--db", str(paths.FEEDBACK_DB_PATH)]) == 2
    assert "non-default" in capsys.readouterr().out
