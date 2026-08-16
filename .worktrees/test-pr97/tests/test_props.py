from outlier_scrapers.props import enrich_schedule_for_props


def test_enrich_schedule_fetches_prop_events_missing_from_league_schedule():
    class Client:
        def __init__(self):
            self.calls = []

        def fetch_event(self, event_id):
            self.calls.append(event_id)
            return {
                "eventId": event_id,
                "scheduledTime": "2026-07-13T23:10:00+00:00",
                "home": {"teamId": "h", "alias": "NYY"},
                "away": {"teamId": "a", "alias": "BOS"},
            }

    client = Client()
    schedule = {"events": [{"eventId": "known", "scheduledTime": "2026-07-13T20:00:00Z"}]}
    props = {
        "props": [
            {"outcome": {"eventId": "known"}},
            {"outcome": {"eventId": "missing"}},
            {"outcome": {"eventId": "missing"}},
        ]
    }

    enriched, errors = enrich_schedule_for_props(client, schedule, props)

    assert client.calls == ["missing"]
    assert errors == []
    assert {event["eventId"] for event in enriched["events"]} == {"known", "missing"}
    assert schedule["events"] == [
        {"eventId": "known", "scheduledTime": "2026-07-13T20:00:00Z"}
    ]  # caller input is not mutated


def test_enrich_schedule_reports_event_detail_failure_without_dropping_known_events():
    class Client:
        def fetch_event(self, event_id):
            raise RuntimeError("detail unavailable")

    schedule = {"events": [{"eventId": "known"}]}
    props = {"props": [{"outcome": {"eventId": "missing"}}]}

    enriched, errors = enrich_schedule_for_props(Client(), schedule, props)

    assert enriched == schedule
    assert errors == [{"event_id": "missing", "error": "detail unavailable"}]


def test_enrich_schedule_accepts_events_wrapper_and_replaces_missing_time_context():
    class Client:
        def fetch_event(self, event_id):
            return {
                "events": [{"eventId": event_id, "scheduledTime": "2026-07-13T22:00:00Z"}]
            }

    schedule = {"events": [{"eventId": "missing"}]}
    props = {"props": [{"outcome": {"eventId": "missing"}}]}

    enriched, errors = enrich_schedule_for_props(Client(), schedule, props)

    assert errors == []
    assert enriched["events"][-1]["scheduledTime"] == "2026-07-13T22:00:00Z"
    assert len(enriched["events"]) == 1


def test_enrich_schedule_caps_missing_event_fanout():
    class Client:
        def __init__(self):
            self.calls = []

        def fetch_event(self, event_id):
            self.calls.append(event_id)
            return {"eventId": event_id, "scheduledTime": "2026-07-13T22:00:00Z"}

    props = {
        "props": [
            {"outcome": {"eventId": f"event-{index:02d}"}}
            for index in range(5)
        ]
    }
    client = Client()

    enriched, errors = enrich_schedule_for_props(
        client, {"events": []}, props, max_missing_events=3
    )

    assert client.calls == ["event-00", "event-01", "event-02"]
    assert len(enriched["events"]) == 3
    assert errors == [
        {"event_id": "event-03", "error": "event detail fetch cap exceeded"},
        {"event_id": "event-04", "error": "event detail fetch cap exceeded"},
    ]


def test_props_status_persists_schedule_event_fetch_denominator(tmp_path, monkeypatch):
    import json

    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.paths import league_paths
    from outlier_scrapers.props import export_props_for_league

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")

    class Client:
        def fetch_schedule(self, league_id):
            return {"events": []}

        def fetch_player_props(self, league_id):
            return {
                "props": [
                    {"outcome": {"eventId": "event-ok"}},
                    {"outcome": {"eventId": "event-error"}},
                    {"outcome": {"eventId": "event-ok"}},
                ]
            }

        def fetch_event(self, event_id):
            if event_id == "event-error":
                raise RuntimeError("detail unavailable")
            return {"eventId": event_id, "scheduledTime": "2026-07-13T22:00:00Z"}

        def url_for(self, path):
            return f"https://example.test{path}"

    status = export_props_for_league(Client(), "MLB")

    assert status["status"] == "partial"
    assert status["schedule_event_fetch_requested_count"] == 2
    assert status["schedule_event_fetch_error_count"] == 1
    assert status["schedule_event_fetch_errors"] == [
        {"event_id": "event-error", "error": "detail unavailable"}
    ]
    persisted = json.loads(
        (league_paths("MLB").reports / "props_export_status_latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted == status
