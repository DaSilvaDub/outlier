from outlier_scrapers.api import OutlierApiClient, OutlierApiError
from outlier_scrapers.discover import summarize_league

SCHEDULE = {
    "events": [
        {"eventId": "e1", "away": {"alias": "NYY", "teamId": "10"}, "home": {"alias": "BOS", "teamId": "20"}}
    ]
}
PROPS = {
    "props": [
        {"outcome": {"marketId": "m1", "eventId": "e1", "proposition": "HITS"}},
        {"outcome": {"marketId": "m2", "eventId": "e1", "proposition": "RUNS"}},
    ],
    "_page": {"pages": 1, "total": 2, "pageNumber": 1},
}
MARKET_DETAIL = {
    "marketId": "m1",
    "openLine": 1.5,
    "currentLine": 2.5,
    "history": [{"line": 1.5, "timestamp": "t0"}, {"line": 2.5, "timestamp": "t1"}],
    "lineDelta": 1.0,
}


def make_client(routes):
    client = OutlierApiClient(storage_state={})

    def fake(path):
        for substring, response in routes.items():
            if substring in path:
                if isinstance(response, Exception):
                    raise response
                return response
        raise OutlierApiError(f"HTTP 404 for {path}: body_len=0")

    client.fetch_json = fake  # type: ignore[assignment]
    return client


def test_deep_probe_samples_market_detail_and_candidates():
    routes = {
        "schedule": SCHEDULE,
        "playerProps": PROPS,
        "/markets/m1": MARKET_DETAIL,
        "/markets/m2": MARKET_DETAIL,
        # Exactly one insights candidate responds; the rest 404.
        "/insights/leagues/MLB": {"insights": [{"id": "i1"}]},
    }
    report = summarize_league(make_client(routes), "MLB", deep=True)
    deep = report["deep"]

    assert deep["sampled_market_ids"] == ["m1", "m2"]
    by_id = {d["market_id"]: d for d in deep["market_detail"]}
    assert by_id["m1"]["status"] == "ok"
    assert any("history" in k.lower() for k in by_id["m1"]["movement_signal_keys"])

    insights = deep["insights_candidates"]
    assert any(v["status"] == "ok" for v in insights.values())
    assert any(v["status"] == "error" for v in insights.values())

    # No EV/arb route is wired, so all candidates report a non-ok status.
    assert all(v["status"] in {"error", "auth_required"} for v in deep["ev_arb_candidates"].values())


def test_non_deep_run_has_no_deep_section():
    report = summarize_league(make_client({"schedule": SCHEDULE, "playerProps": PROPS}), "MLB")
    assert "deep" not in report
