"""Publication gate on the pipeline path (not just on a receipt list).

NFL finding B: the matchup-rankings FAILED receipt was appended after the
publish decision, so a failed run still committed dated/latest files. Here the
decision is taken once, after every stage has reported, and a late FAILED
required stage must withhold everything.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from outlier_nba.pipeline import EXIT_OK, EXIT_WITHHELD, NbaPipeline, Stage
from outlier_nba.receipts import (
    ReceiptLog,
    gate_reason,
    receipt,
    run_status,
    validation_receipt,
)
from outlier_nba.run_context import make_run_context

TIP = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)
AS_OF = TIP - timedelta(hours=3)


def _ctx(**kw):
    return make_run_context(
        "2026-10-21",
        AS_OF,
        slate_events=[{"startTime": TIP.isoformat()}],
        run_started_utc=AS_OF,
        **kw,
    )


def _ok(name, required=True):
    def run(ctx, writer, state):
        writer.stage_text(f"{name}.txt", name, publish=[state["out"] / f"{name}_latest.txt"])
        return receipt(name, "OK", required=required, received=1)

    return Stage(name, required, run)


def _failed(name, required=True, status="FAILED"):
    def run(ctx, writer, state):
        return receipt(name, status, required=required, reason="boom")

    return Stage(name, required, run)


def _pipeline(tmp_path):
    out = tmp_path / "pub"
    return NbaPipeline(nba_dir=tmp_path / "nba", published_dir=out), out


def test_receipt_helpers_status_and_gate():
    ok = receipt("a", "OK")
    empty = receipt("b", "EMPTY")
    inc = receipt("c", "INCOMPLETE")
    bad = receipt("d", "FAILED")
    opt_bad = receipt("e", "FAILED", required=False)
    assert run_status([ok, empty, opt_bad]) == "OK"
    assert gate_reason([ok, empty, opt_bad]) is None
    assert run_status([ok, inc]) == "PARTIAL"
    assert run_status([ok, inc, bad]) == "FAILED"
    assert gate_reason([ok, inc, bad]) == "required_stage_not_ok: c=INCOMPLETE, d=FAILED"
    assert validation_receipt("v", []).status == "OK"
    assert validation_receipt("v", ["x"]).status == "INVALID"
    assert validation_receipt("v", [], empty=True).status == "EMPTY"


def test_receipt_log_cannot_be_appended_after_seal():
    log = ReceiptLog()
    log.append(receipt("a", "OK"))
    log.seal()
    with pytest.raises(RuntimeError):
        log.append(receipt("late", "FAILED"))


def test_all_required_ok_publishes_files_and_pointer(tmp_path):
    pipe, out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [_ok("schedule"), _ok("odds")], pointer=out / "run_latest.json")
    assert result.exit_code == EXIT_OK and result.published
    assert (out / "schedule_latest.txt").read_text() == "schedule"
    pointer = json.loads((out / "run_latest.json").read_text())
    assert pointer["run_id"] == result.run_id
    assert (tmp_path / "nba" / "runs" / result.run_id / "manifest.json").exists()


def test_late_failed_required_stage_withholds_everything(tmp_path):
    """The last stage fails after earlier stages already staged publishable files."""
    pipe, out = _pipeline(tmp_path)
    stages = [_ok("schedule"), _ok("odds"), _failed("rankings")]
    result = pipe.run(_ctx(), stages, pointer=out / "run_latest.json")
    assert result.exit_code == EXIT_WITHHELD and not result.published
    assert result.status == "FAILED"
    assert "rankings=FAILED" in (result.withheld_reason or "")
    assert not out.exists() or not any(out.iterdir())
    manifest = json.loads((tmp_path / "nba" / "runs" / result.run_id / "manifest.json").read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["published"] is False
    assert any(r["name"] == "rankings" and r["status"] == "FAILED" for r in manifest["receipts"])


@pytest.mark.parametrize("bad", ["FAILED", "INVALID", "INCOMPLETE", "UNSUPPORTED"])
def test_any_non_passing_required_status_withholds(tmp_path, bad):
    pipe, out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [_ok("a"), _failed("b", status=bad), _ok("c")])
    assert result.exit_code == EXIT_WITHHELD and not (out / "a_latest.txt").exists()


def test_failed_optional_stage_degrades_but_still_publishes(tmp_path):
    pipe, out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [_ok("schedule"), _failed("referees", required=False)])
    assert result.exit_code == EXIT_OK and result.published
    assert (out / "schedule_latest.txt").exists()


def test_stage_exception_becomes_failed_required_receipt(tmp_path):
    def explode(ctx, writer, state):
        raise ValueError("kaboom")

    pipe, out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [_ok("a"), Stage("explode", True, explode)])
    assert result.exit_code == EXIT_WITHHELD
    failed = [r for r in result.receipts if r.name == "explode"][0]
    assert failed.status == "FAILED" and "kaboom" in (failed.reason or "")
    assert not (out / "a_latest.txt").exists()


def test_stage_returning_no_receipt_fails_closed(tmp_path):
    pipe, _out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [Stage("silent", True, lambda ctx, writer, state: None)])
    assert result.exit_code == EXIT_WITHHELD


def test_stage_can_return_several_receipts_and_all_are_gated(tmp_path):
    def multi(ctx, writer, state):
        return [receipt("m1", "OK"), receipt("m2", "FAILED")]

    pipe, _out = _pipeline(tmp_path)
    result = pipe.run(_ctx(), [Stage("multi", True, multi)])
    assert result.exit_code == EXIT_WITHHELD
    assert {r.name for r in result.receipts} == {"m1", "m2"}


def test_retrospective_run_is_bundle_only_even_if_every_stage_passes(tmp_path):
    ctx = make_run_context(
        "2026-10-21",
        TIP + timedelta(minutes=5),
        slate_events=[{"startTime": TIP.isoformat()}],
    )
    pipe, out = _pipeline(tmp_path)
    result = pipe.run(ctx, [_ok("schedule")], pointer=out / "run_latest.json")
    assert result.exit_code == EXIT_OK and not result.published
    assert not out.exists() or not any(out.iterdir())
    assert (tmp_path / "nba" / "runs" / ctx.run_id / "manifest.json").exists()


def test_fixture_run_publishes(tmp_path):
    ctx = make_run_context("2026-10-21", TIP + timedelta(days=1), fixture=True)
    pipe, out = _pipeline(tmp_path)
    result = pipe.run(ctx, [_ok("schedule")])
    assert result.published and (out / "schedule_latest.txt").exists()


def test_rerun_of_same_run_id_is_refused(tmp_path):
    pipe, _out = _pipeline(tmp_path)
    pipe.run(_ctx(), [_ok("a")])
    with pytest.raises(FileExistsError):
        pipe.run(_ctx(), [_ok("a")])


def test_stages_see_a_state_dict_and_can_share_data(tmp_path):
    def producer(ctx, writer, state):
        state["rows"] = [1, 2, 3]
        return receipt("producer", "OK")

    def consumer(ctx, writer, state):
        return receipt("consumer", "OK", received=len(state["rows"]))

    pipe, _out = _pipeline(tmp_path)
    result = pipe.run(
        _ctx(), [Stage("producer", True, producer), Stage("consumer", True, consumer)]
    )
    assert result.exit_code == EXIT_OK
    assert [r.received for r in result.receipts if r.name == "consumer"] == [3]
