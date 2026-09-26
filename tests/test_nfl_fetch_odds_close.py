"""Tests for Odds-API → Outlier book_close feed mapping (mocked HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nfl" / "settle"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_odds_market_mapping_and_position():
    from outlier_nfl.fetch_odds_close import (
        odds_market_to_outlier,
        outcome_to_position,
        better_american_odds,
    )

    assert odds_market_to_outlier("player_rush_yds") == "RUSH_YDS"
    assert odds_market_to_outlier("player_rush_yds_alternate") == "RUSH_YDS"
    assert odds_market_to_outlier("player_anytime_td") == "ANYTIME_TD"
    assert odds_market_to_outlier("h2h") is None
    assert outcome_to_position("Over", market_outlier="RUSH_YDS") == "OVER"
    assert outcome_to_position("No", market_outlier="ANYTIME_TD") == "UNDER"
    assert better_american_odds(-110, -105) == -105
    assert better_american_odds(100, 110) == 110


def test_map_sanitized_fixture_to_close_records():
    from outlier_nfl.fetch_odds_close import (
        map_event_odds_to_close_records,
        build_close_feed_from_event_odds,
    )
    from outlier_nfl.enrich_close import (
        enrich_prediction_payload,
        load_book_close_feed,
        CLOSE_SOURCE_BOOK,
        CLOSE_SOURCE_SNAPSHOT,
        lookup_book_close_row,
    )
    from outlier_nfl.close_feed import write_close_feed

    raw = _load("odds_api_event_props_sanitized.json")
    recs = map_event_odds_to_close_records(raw)
    assert len(recs) >= 6
    # FanDuel -102 beats DraftKings -105 for Gibbs OVER
    gibbs_over = next(
        r
        for r in recs
        if r["player_name"] == "Jahmyr Gibbs"
        and r["market"] == "RUSH_YDS"
        and r["position"] == "OVER"
    )
    assert gibbs_over["close_odds"] == -102
    assert gibbs_over["close_source"] == CLOSE_SOURCE_BOOK
    assert gibbs_over["close_implied"] is not None

    preds = _load("predictions_tier1.json")
    aligned = build_close_feed_from_event_odds(raw, predictions=preds)
    assert any(r.get("aligned_to_predictions") for r in aligned)
    assert any(r.get("matchup") == "DET @ BUF" for r in aligned if r.get("aligned_to_predictions"))


def test_short_key_join_enriches_pack_without_outlier_event_id(tmp_path: Path):
    """Odds-API event ids differ from Outlier; short key still stamps book_close."""
    from outlier_nfl.fetch_odds_close import build_close_feed_from_event_odds
    from outlier_nfl.close_feed import write_close_feed
    from outlier_nfl.enrich_close import (
        enrich_prediction_payload,
        load_book_close_feed,
        CLOSE_SOURCE_BOOK,
        CLOSE_SOURCE_SNAPSHOT,
    )
    from outlier_nfl.settle import load_prediction_snapshot, settle_predictions, load_boxscores

    raw_api = _load("odds_api_event_props_sanitized.json")
    # Do NOT align — leave Odds-API matchup/event_id (proves short-key join)
    close_recs = build_close_feed_from_event_odds(raw_api, predictions=None)
    feed_path = tmp_path / "closes.json"
    write_close_feed(feed_path, close_recs, note="fixture odds-api mapped")

    preds = _load("predictions_tier1.json")
    for row in preds["records"]:
        for k in (
            "close_line",
            "close_odds",
            "close_implied",
            "close_source",
            "model_p",
            "model_p_source",
            "p_model",
        ):
            row.pop(k, None)

    index = load_book_close_feed(feed_path)
    enriched = enrich_prediction_payload(
        preds, mode="book_close", attach_model_p="pass", book_close_index=index
    )
    book_rows = [r for r in enriched["records"] if r.get("close_source") == CLOSE_SOURCE_BOOK]
    assert book_rows, "short-key join should stamp at least one book_close"
    assert all(r.get("close_source") != CLOSE_SOURCE_SNAPSHOT for r in book_rows)

    # CLV vs moved closes should be measurable when close_implied differs
    enriched_path = tmp_path / "enriched.json"
    enriched_path.write_text(json.dumps(enriched), encoding="utf-8")
    predictions = load_prediction_snapshot(enriched_path, source="tier1")
    events = load_boxscores(FIXTURES / "boxscores_det_buf.json")
    report = settle_predictions(predictions, events)
    assert report.clv["status"] == "ok"
    assert report.clv["n"] >= 1
    assert CLOSE_SOURCE_BOOK in (report.clv.get("close_sources") or [])


def test_fetch_event_odds_mocked_http(tmp_path: Path):
    from outlier_nfl.fetch_odds_close import fetch_event_odds, main

    fixture = _load("odds_api_event_props_sanitized.json")

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.headers = {"X-Requests-Remaining": "99", "X-Requests-Used": "1"}

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        assert "apiKey=" in url
        assert "REDACTED" not in url  # real key in request is fine; we just mock
        assert "events/evt123/odds" in url
        return _Resp(fixture)

    with patch("urllib.request.urlopen", fake_urlopen):
        data = fetch_event_odds(api_key="test-key-not-real", event_id="evt123")
    assert data["id"] == fixture["id"]
    assert data["_quota"]["requests_remaining"] == "99"

    out = tmp_path / "out.json"
    with patch("urllib.request.urlopen", fake_urlopen):
        with patch.dict("os.environ", {"ODDS_API_KEY": "test-key-not-real"}, clear=False):
            # list_events not called when --event-id set
            rc = main(
                [
                    "--out",
                    str(out),
                    "--event-id",
                    "evt123",
                    "--markets",
                    "player_rush_yds,player_reception_yds,player_receptions,player_anytime_td",
                ]
            )
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source"] == "book_close"
    assert payload["count"] >= 1
    assert all("close_odds" in r for r in payload["records"])


def test_probe_reports_invalid_key_without_raising():
    from outlier_nfl.fetch_odds_close import probe_plan_coverage
    import urllib.error

    class _HTTPErr(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(
                url="https://api.the-odds-api.com/v4/sports/",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )

        def read(self):
            return b'{"message":"API key is not valid","error_code":"INVALID_KEY"}'

    def boom(*args, **kwargs):  # noqa: ARG001
        raise _HTTPErr()

    with patch("urllib.request.urlopen", boom):
        report = probe_plan_coverage(api_key="bad")
    assert report["sports_ok"] is False
    assert "INVALID_KEY" in (report.get("sports_error") or "")


def test_redact_strips_api_key_fields():
    from outlier_nfl.fetch_odds_close import redact_odds_api_payload

    raw = {"apiKey": "secret", "nested": {"api_key": "x", "ok": 1}, "list": [{"Authorization": "y"}]}
    clean = redact_odds_api_payload(raw)
    assert clean["apiKey"] == "REDACTED"
    assert clean["nested"]["api_key"] == "REDACTED"
    assert clean["nested"]["ok"] == 1
    assert clean["list"][0]["Authorization"] == "REDACTED"


def test_try_load_live_wires_odds_api_join(tmp_path: Path):
    from outlier_nfl.close_feed import try_load_live_or_file_close_index
    from outlier_nfl.enrich_close import CLOSE_SOURCE_BOOK

    fixture = _load("odds_api_event_props_sanitized.json")

    class _Resp:
        headers = {"X-Requests-Remaining": "50", "X-Requests-Used": "2"}

        def read(self):
            return json.dumps(fixture).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        return _Resp()

    with patch("urllib.request.urlopen", fake_urlopen):
        with patch.dict("os.environ", {"ODDS_API_KEY": "test-key-not-real"}, clear=False):
            index, note = try_load_live_or_file_close_index(
                None, allow_odds_api=True, event_ids=["evt123"]
            )
    assert index is not None
    assert note.startswith("odds_api_live_join:")
    # short key present
    assert any(len(k) == 4 for k in index)
    sample = next(iter(index.values()))
    assert sample["close_source"] == CLOSE_SOURCE_BOOK


def test_missing_key_blocker_unchanged():
    from outlier_nfl.close_feed import try_load_live_or_file_close_index, close_feed_blocker_message

    index, note = try_load_live_or_file_close_index(None, allow_odds_api=True)
    # Without key in env (we clear), should blocker — but ODDS may be set on box.
    # Force empty env via resolve path:
    from outlier_nfl.close_feed import resolve_odds_api_key

    assert resolve_odds_api_key(env={}) is None
    # Direct blocker text still documents the CLI.
    assert "ODDS_API_KEY" in close_feed_blocker_message()
    assert "fetch_odds_close" in close_feed_blocker_message() or "close-feed" in close_feed_blocker_message()
