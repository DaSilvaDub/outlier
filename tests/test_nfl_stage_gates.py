"""F03 (#224): required-stage receipts gate publication; failures are distinct and nonzero."""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import pipeline as nfl_pipeline
from outlier_nfl.pipeline import EXIT_REQUIRED_STAGE_FAILED, NflPipeline
from outlier_nfl.run_context import parse_utc
from scripts import nfl_snapshot_diff as snap

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-10-04"
PREGAME = "2026-10-04T15:00:00+00:00"
LATER = "2026-10-04T16:00:00+00:00"


def _published(tmp_path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for root in (tmp_path / "NFL" / "normalized", tmp_path / "NFL" / "exports", tmp_path / "reports"):
        if root.is_dir():
            out.update({p.relative_to(tmp_path).as_posix(): p.read_bytes()
                        for p in sorted(root.rglob("*")) if p.is_file()})
    return out


def _run(tmp_path: Path, client: Any, clock: str = LATER, day: str = SLATE) -> dict[str, Any]:
    tape = tmp_path / "NFL" / "tape" / "prior_week.json"
    if not tape.exists():
        tape.parent.mkdir(parents=True, exist_ok=True)
        tape.write_text(json.dumps(snap.frozen_tape(FIXTURES_DIR)), encoding="utf-8")
    now = parse_utc(clock)
    return NflPipeline(client=client, data_dir=tmp_path, clock=lambda: now).run(
        date=day, as_of_utc=clock, reports_dir=tmp_path / "reports"
    )


def _receipt(summary: dict[str, Any], name: str) -> dict[str, Any]:
    return next(r for r in summary["stage_receipts"] if r["name"] == name)


class _MalformedProps(snap.FrozenOutlierClient):
    def fetch_player_props(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        out = super().fetch_player_props()
        out["props"][0] = {"not_an_outcome": True}
        return out


class _OneEventMarketsFail(snap.FrozenOutlierClient):
    def fetch_event_markets(self, event_id: str, *a: Any, **k: Any) -> dict[str, Any]:
        if event_id == "nfl-event-2026-w1-kc-bal":
            raise snap._http_error("HTTP 503 for markets", 503)
        return super().fetch_event_markets(event_id, *a, **k)


def _assert_gated(tmp_path: Path, run: dict[str, Any], before: dict[str, bytes], pregame_id: str):
    assert run["publication"] == "bundle_only"
    assert run["publication_reason"].startswith("required_stage_not_ok: ")
    assert _published(tmp_path) == before  # nothing dated or latest replaced
    pointer = json.loads((tmp_path / "NFL" / "normalized" / "nfl_run_pointer_latest.json").read_text())
    assert pointer["run_id"] == pregame_id  # latest pointer not advanced
    manifest = json.loads((Path(run["run_dir"]) / "manifest.json").read_text())
    assert manifest["status"] == run["status"]
    assert all(a["published_to"] == [] for a in manifest["artifacts"])
    saved = json.loads((Path(run["run_dir"]) / "summary.json").read_text())
    assert saved["status"] == run["status"] and saved["stage_receipts"] == run["stage_receipts"]


@pytest.fixture
def pregame(tmp_path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    good = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), clock=PREGAME)
    assert good["status"] == "OK" and good["publication"] == "published"
    assert all(r["status"] == "OK" for r in good["stage_receipts"]), good["stage_receipts"]
    return good, _published(tmp_path)


def test_props_page_two_failure_is_partial_and_publishes_nothing(tmp_path, pregame):
    good, before = pregame
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_props_page=2))
    assert run["status"] == "PARTIAL"
    r = _receipt(run, "player_props")
    assert (r["status"], r["expected"], r["received"]) == ("INCOMPLETE", 2, 1)
    assert run["player_props_count"] == 0  # page 1 is not passed off as the slate
    _assert_gated(tmp_path, run, before, good["run_id"])


def test_every_event_market_failing_is_failed(tmp_path, pregame):
    good, before = pregame
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True))
    assert run["status"] == "FAILED"
    r = _receipt(run, "event_markets")
    assert (r["status"], r["expected"], r["received"]) == ("FAILED", 6, 0)
    _assert_gated(tmp_path, run, before, good["run_id"])


def test_one_event_markets_failing_is_partial(tmp_path, pregame):
    good, before = pregame
    run = _run(tmp_path, _OneEventMarketsFail(FIXTURES_DIR))
    assert run["status"] == "PARTIAL"
    assert _receipt(run, "event_markets")["status"] == "INCOMPLETE"
    _assert_gated(tmp_path, run, before, good["run_id"])


def test_malformed_props_payload_is_failed_invalid(tmp_path, pregame):
    good, before = pregame
    run = _run(tmp_path, _MalformedProps(FIXTURES_DIR))
    assert run["status"] == "FAILED"
    r = _receipt(run, "player_props")
    assert r["status"] == "INVALID" and "index 0" in r["errors"][0]
    _assert_gated(tmp_path, run, before, good["run_id"])


def test_invalid_normalized_rows_withhold_everything_already_staged(tmp_path, pregame, monkeypatch):
    good, before = pregame
    real = nfl_pipeline.validate_normalized_dataset

    def bad_props(records, dataset_type="games"):
        return ["Record 0: line is NaN"] if dataset_type == "props" else real(records, dataset_type)

    monkeypatch.setattr(nfl_pipeline, "validate_normalized_dataset", bad_props)
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR))
    assert run["status"] == "FAILED"
    assert _receipt(run, "normalized_validation")["status"] == "INVALID"
    # External metrics / weather were staged for publication before validation ran.
    _assert_gated(tmp_path, run, before, good["run_id"])


def test_genuinely_empty_scheduled_slate_is_ok_with_zero_rows(tmp_path):
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR), clock="2026-10-06T15:00:00+00:00",
               day="2026-10-06")
    assert run["status"] == "OK" and run["publication"] == "published"
    assert run["events_count"] == 0 and run["player_props_count"] == 0
    assert {r["name"]: r["status"] for r in run["stage_receipts"]}["event_markets"] == "EMPTY"


def test_failure_kinds_are_distinct(tmp_path):
    outcomes = set()
    for i, client in enumerate([
        snap.FrozenOutlierClient(FIXTURES_DIR, fail_props_page=2),
        snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True),
        _MalformedProps(FIXTURES_DIR),
    ]):
        run = _run(tmp_path / str(i), client)
        bad = tuple((r["name"], r["status"]) for r in run["stage_receipts"]
                    if r["status"] not in ("OK", "EMPTY"))
        outcomes.add((run["status"], bad))
    assert len(outcomes) == 3


def test_invalid_schedule_fixture_is_failed(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    for name in ("event_markets.json", "player_props.json"):
        (fx / name).write_text((FIXTURES_DIR / name).read_text())
    sched = json.loads((FIXTURES_DIR / "schedule.json").read_text())
    sched["events"] = {"not": "a list"}
    (fx / "schedule.json").write_text(json.dumps(sched))
    run = NflPipeline(data_dir=tmp_path).run(date="2026-09-13", offline_fixtures_dir=fx,
                                             reports_dir=tmp_path / "reports")
    assert run["status"] == "FAILED" and _receipt(run, "schedule")["status"] == "INVALID"
    assert not list((tmp_path / "NFL" / "normalized").glob("*.json"))


def _failed_summary() -> dict[str, Any]:
    return {"status": "FAILED", "date": SLATE, "run_dir": "data/NFL/runs/x", "stage_receipts": [
        {"name": "event_markets", "required": True, "status": "FAILED",
         "reason": "6 of 6 market requests failed", "errors": ["e1 GAMELINE: HTTP 500"]}]}


def test_cli_exits_nonzero_and_says_how_to_recover(monkeypatch, capsys):
    monkeypatch.setattr(NflPipeline, "run", lambda self, **k: _failed_summary())
    monkeypatch.setattr(sys, "argv", ["outlier_nfl.pipeline", "--date", SLATE])
    assert nfl_pipeline.main() == EXIT_REQUIRED_STAGE_FAILED
    err = capsys.readouterr().err
    assert "nothing was published" in err and "event_markets: FAILED" in err and "rerun" in err


def test_weekly_rejects_a_non_ok_slate(tmp_path, monkeypatch, capsys):
    from outlier_nfl import weekly

    client = snap.FrozenOutlierClient(FIXTURES_DIR, fail_markets=True)
    pipeline = NflPipeline(client=client, data_dir=tmp_path)
    with pytest.raises(weekly.RequiredStageError, match="event_markets=FAILED"):
        weekly.run_week(pipeline, date(2026, 10, 4), events=copy.deepcopy(client.fetch_schedule()["events"]),
                        reports_dir=tmp_path / "reports", run_stamp="0900")
    assert not list((tmp_path / "NFL" / "normalized").glob("nfl_best_bets_week_*.json"))

    def boom(*_a: Any, **_k: Any) -> Any:
        raise weekly.RequiredStageError(SLATE, _failed_summary())

    monkeypatch.setattr(weekly, "run_week", boom)
    assert weekly.main(["--today", SLATE, "--no-refresh-tape", "--data-dir", str(tmp_path)]) == \
        EXIT_REQUIRED_STAGE_FAILED
    assert "Weekly card not written" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Review round 1: a slate with events but zero admitted props never publishes
# ---------------------------------------------------------------------------

class _NoMatchingProps(snap.FrozenOutlierClient):
    def fetch_player_props(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        out = super().fetch_player_props()
        for p in out["props"]:
            p["outcome"]["eventId"] = "nfl-event-some-other-slate"
        return out


@pytest.mark.parametrize(
    "client",
    [snap.FrozenOutlierClient(FIXTURES_DIR, empty_props=True), _NoMatchingProps(FIXTURES_DIR)],
    ids=["feed_returns_zero_props", "props_match_no_slate_event"],
)
def test_zero_admitted_props_with_events_blocks_publication(tmp_path, pregame, client, monkeypatch,
                                                           capsys):
    good, before = pregame
    run = _run(tmp_path, client)
    assert run["status"] == "PARTIAL" and run["player_props_count"] == 0
    r = _receipt(run, "player_props")
    assert (r["status"], r["expected"], r["received"]) == ("INCOMPLETE", 3, 0)
    assert "0 props admitted for 3 slate events" in r["reason"] and "rerun later" in r["reason"]
    _assert_gated(tmp_path, run, before, good["run_id"])

    monkeypatch.setattr(NflPipeline, "run", lambda self, **k: run)
    monkeypatch.setattr(sys, "argv", ["outlier_nfl.pipeline", "--date", SLATE])
    assert nfl_pipeline.main() == EXIT_REQUIRED_STAGE_FAILED
    err = capsys.readouterr().err
    assert "0 props admitted for 3 slate events" in err and "too early" in err


def test_zero_event_slate_stays_ok_and_props_receipt_empty(tmp_path):
    run = _run(tmp_path, snap.FrozenOutlierClient(FIXTURES_DIR, empty_props=True),
               clock="2026-10-06T15:00:00+00:00", day="2026-10-06")
    assert run["status"] == "OK" and run["publication"] == "published"
    assert _receipt(run, "player_props")["status"] == "EMPTY"


def test_admitted_props_count_is_recorded(tmp_path, pregame):
    good, _ = pregame
    assert _receipt(good, "player_props")["received"] == good["player_props_count"] == 8
