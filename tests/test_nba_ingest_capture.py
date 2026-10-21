from datetime import timedelta

import pytest

from outlier_nba.ingest.outlier import fetch_raw, normalize_slate, raw_receipts
from outlier_nba.track.capture import (
    build_shadow_capture,
    orientation,
    selection_text,
)
from tests import nba_fixtures as fx

NOW = fx.STARTED_TIP + timedelta(minutes=30)  # one game underway, two still open


def _slate(client=None):
    bundle = fetch_raw(client or fx.FakeClient(), clock=fx.clock_at(NOW))
    slate = normalize_slate(bundle, bundle.fetch_finished)
    return bundle, slate


def test_fetch_reads_only_open_events_and_only_game_market_types():
    client = fx.FakeClient()
    fetch_raw(client, clock=fx.clock_at(NOW))
    market_calls = [c for c in client.calls if c.startswith("markets:")]
    assert not any(fx.EV_STARTED in c for c in market_calls)
    assert {c.rsplit(":", 1)[1] for c in market_calls} == {"GAMELINE", "TEAM_PROP"}
    assert "props:NBA" in client.calls and "schedule:NBA" in client.calls


def test_slate_keeps_only_open_full_game_in_scope_outcomes():
    _bundle, slate = _slate()
    ids = {r.outcome_id for r in slate.records}
    assert "o-bam-over" not in ids  # started game
    assert "o-fan-over" not in ids  # fantasy score
    assert "o-h1-over" not in ids  # 1H total (periodLabel)
    assert {
        "o-pts-26.5-over",
        "o-sp-home",
        "o-tot-over",
        "o-ml-home",
        "o-tt-over",
        "o-td-over",
        "o-reb-over",
    } <= ids
    assert slate.skipped["prop_market_out_of_scope"] == 1
    assert slate.skipped["game_partial_period"] == 2
    kinds = {r.kind for r in slate.records}
    assert kinds == {"PROP", "GAME", "TEAM"}


def test_ledger_selection_strings_use_the_gradable_grammar():
    _bundle, slate = _slate()
    by_id = {r.outcome_id: r for r in slate.records}
    assert selection_text(by_id["o-pts-26.5-over"]) == "Jalen Brunson - PTS OVER 26.5"
    assert selection_text(by_id["o-tot-over"]) == "NYK @ BOS Total O/U OVER 221.5"
    assert selection_text(by_id["o-sp-away"]) == "NYK @ BOS Spread AWAY 4.5"
    assert selection_text(by_id["o-ml-home"]) == "NYK @ BOS Money Line HOME"
    assert selection_text(by_id["o-tt-under"]) == "BOS Team Total UNDER 112.5"
    assert selection_text(by_id["o-td-over"]) == "Stephen Curry - TD OVER 0.5"
    assert selection_text(by_id["o-tot-over-221"]) == "NYK @ BOS Total O/U OVER 221"


def test_orientation_pairs_spread_sides_on_one_threshold():
    _bundle, slate = _slate()
    by_id = {r.outcome_id: r for r in slate.records}
    assert orientation(by_id["o-sp-home"]) == (4.5, "over")
    assert orientation(by_id["o-sp-away"]) == (4.5, "under")
    assert orientation(by_id["o-tot-under"]) == (221.5, "under")
    assert orientation(by_id["o-ml-away"]) == (None, "under")


def _capture():
    _bundle, slate = _slate()
    captured_at = fx.clock_at(NOW)()
    return slate, build_shadow_capture(slate.records, captured_at=captured_at)


def _rows():
    _slate_, capture = _capture()
    return {r["outcome_id"]: r for r in capture.rows}, capture


def test_every_row_is_shadow_units_zero_never_actionable():
    rows, capture = _rows()
    assert capture.rows
    for row in rows.values():
        assert row["board"] == "SHADOW" and "SHADOW" in row["data_quality_flags"].split(";")
        assert row["actionable"] == "false" and row["recommended_units_pre_news"] == 0
        assert row["sport"] == "NBA"
        assert row["blend_market_weight"] == 1.0 and row["blend_model_weight"] == 0.0


def test_two_sided_prop_consensus_excludes_dfs_and_sums_to_one():
    rows, _ = _rows()
    over, under = rows["o-pts-26.5-over"], rows["o-pts-26.5-under"]
    po, pu = float(over["market_consensus_prob"]), float(under["market_consensus_prob"])
    assert po + pu == pytest.approx(1.0, abs=1e-5)
    assert 0.4 < po < 0.6
    assert "THIN_MARKET" not in over["data_quality_flags"]  # 4 priced books; PrizePicks excluded
    assert over["model_prob_source"] == "nba_consensus:shin"
    assert over["push_prob"] == "0.000000"  # half line


def test_best_price_book_is_recorded_and_edge_is_consensus_minus_implied():
    rows, _ = _rows()
    over = rows["o-pts-26.5-over"]
    assert (
        over["price"] == -105 and over["book"] == "Caesars"
    )  # PrizePicks +100 is not a priced book
    assert float(over["edge"]) == pytest.approx(
        float(over["market_consensus_prob"]) - 105 / 205, abs=1e-5
    )


def test_one_sided_alt_rungs_get_no_consensus_and_are_flagged():
    rows, _ = _rows()
    for oid in ("o-pts-24-over", "o-pts-29.5-over", "o-td-over"):
        row = rows[oid]
        assert row["market_consensus_prob"] == ""
        assert "ONE_SIDED_NO_DEVIG" in row["data_quality_flags"]
        assert row["edge"] == ""
    assert "ALT_LINE" in rows["o-pts-24-over"]["data_quality_flags"]
    assert "PUSH_CAPABLE_NO_PROB" in rows["o-pts-24-over"]["data_quality_flags"]


def test_thin_two_way_market_is_flagged_but_priced():
    rows, _ = _rows()
    assert "THIN_MARKET" in rows["o-reb-over"]["data_quality_flags"]
    assert rows["o-reb-over"]["market_consensus_prob"] != ""


def test_integer_total_rung_derives_push_from_adjacent_half_lines():
    rows, _ = _rows()
    row = rows["o-tot-over-221"]
    p_over_2205 = float(rows["o-tot-over-220.5"]["market_consensus_prob"])
    p_over_2215 = float(rows["o-tot-over"]["market_consensus_prob"])
    assert float(row["push_prob"]) == pytest.approx(p_over_2205 - p_over_2215, abs=1e-5)
    assert float(row["push_prob"]) > 0
    # the UNDER at the same integer line shares the same push probability
    assert rows["o-tot-under-221"]["push_prob"] == row["push_prob"]
    assert "PUSH_CAPABLE_NO_PROB" not in row["data_quality_flags"]


def test_integer_spread_without_neighbours_is_push_capable_no_prob():
    rows, _ = _rows()
    assert "PUSH_CAPABLE_NO_PROB" in rows["o-sp-home-4"]["data_quality_flags"]
    assert rows["o-sp-home-4"]["push_prob"] == ""


def test_moneyline_pair_is_devigged_without_push():
    rows, _ = _rows()
    home, away = rows["o-ml-home"], rows["o-ml-away"]
    assert float(home["market_consensus_prob"]) + float(away["market_consensus_prob"]) == (
        pytest.approx(1.0, abs=1e-5)
    )
    assert float(home["market_consensus_prob"]) > 0.6
    assert home["line"] == "0" and home["push_prob"] == ""


def test_method_probabilities_are_kept_for_later_out_of_sample_selection():
    _rows_, capture = _rows()
    probs = capture.method_probs["o-pts-26.5-over"]
    assert set(probs) == {"multiplicative", "shin", "power"}


def test_raw_receipts_ok_for_a_complete_slate():
    bundle, slate = _slate()
    receipts = {r.name: r for r in raw_receipts(bundle, slate)}
    assert {n: r.status for n, r in receipts.items()} == {
        "schedule": "OK",
        "player_props": "OK",
        "game_markets": "OK",
    }


def test_failed_event_market_fetch_is_incomplete_and_all_failing_is_failed():
    bundle, slate = _slate(fx.FakeClient(fail_markets_for={fx.EV2}))
    status = {r.name: r.status for r in raw_receipts(bundle, slate)}
    assert status["game_markets"] == "INCOMPLETE"
    bundle, slate = _slate(fx.FakeClient(fail_markets_for={fx.EV1, fx.EV2}))
    status = {r.name: r.status for r in raw_receipts(bundle, slate)}
    assert status["game_markets"] == "FAILED"


def test_truncated_pagination_is_incomplete():
    payload = fx.player_props()
    payload["_page_summary"].update(stopped_reason="no_progress", total_reported=500)
    bundle, slate = _slate(fx.FakeClient(props_payload=payload))
    receipt = {r.name: r for r in raw_receipts(bundle, slate)}["player_props"]
    assert receipt.status == "INCOMPLETE" and "no_progress" in (receipt.reason or "")


def test_zero_props_for_a_slate_with_open_events_is_incomplete():
    payload = {"props": [], "_page_summary": {"stopped_reason": "single_page"}}
    bundle, slate = _slate(fx.FakeClient(props_payload=payload))
    assert {r.name: r.status for r in raw_receipts(bundle, slate)}["player_props"] == "INCOMPLETE"


def test_empty_schedule_is_an_empty_receipt():
    bundle, slate = _slate(fx.FakeClient(sched={"events": []}))
    assert {r.name: r.status for r in raw_receipts(bundle, slate)}["schedule"] == "EMPTY"


def test_requests_that_succeed_but_return_no_markets_are_incomplete():
    class NoMarkets(fx.FakeClient):
        def fetch_event_markets(self, event_id, market_type):
            return {"markets": []}

    bundle, slate = _slate(NoMarkets())
    assert {r.name: r.status for r in raw_receipts(bundle, slate)}["game_markets"] == "INCOMPLETE"


def test_game_rows_with_an_unresolved_team_are_skipped_not_written_as_question_marks():
    sched = fx.schedule()
    sched["events"][0]["home"]["alias"] = "ZZZ"
    sched["events"][0]["home"]["name"] = "Nowhere"
    _bundle, slate = _slate(fx.FakeClient(sched=sched))
    assert not any("?" in selection_text(r) for r in slate.records)
    assert slate.skipped["game_market_out_of_scope"] >= 1


def test_unorientable_outcomes_are_counted_in_the_capture_summary():
    import dataclasses

    _bundle, slate = _slate()
    odd = dataclasses.replace(slate.records[0], side="YES")
    capture = build_shadow_capture([odd, *slate.records[1:]], captured_at=NOW)
    assert capture.summary["unorientable"] == 1
