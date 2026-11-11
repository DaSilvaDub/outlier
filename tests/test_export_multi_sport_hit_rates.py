"""Offline tests for scripts/export_multi_sport_hit_rates.py (no network, fake client)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_multi_sport_hit_rates as ehr  # noqa: E402

SLATE = "2026-11-10"
TONIGHT = "2026-11-11T00:30:00Z"  # 7:30 PM ET on 2026-11-10
NEXT_WEEK = "2026-11-20T00:30:00Z"


def _event(eid: str, when: str = TONIGHT) -> dict[str, Any]:
    return {
        "eventId": eid,
        "scheduledTime": when,
        "home": {"teamId": f"{eid}-h", "alias": "BOS"},
        "away": {"teamId": f"{eid}-a", "alias": "PHI"},
    }


def _prop(
    label: str,
    *,
    side: str = "OVER",
    l5: Any = 1.0,
    l10: Any = 1.0,
    eid: str = "e1",
    l10_results: list[bool] | None = None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {"l5": l5, "l10": l10, "curSeason": 0.9}
    if l10_results is not None:
        stats["l10Results"] = l10_results
    return {
        "outcome": {
            "marketLabel": label,
            "position": side,
            "line": 4.5,
            "eventId": eid,
            "teamId": f"{eid}-h",
            "bestOdds": -150,
            "books": ["FANDUEL", "DRAFTKINGS"],
        },
        "stats": stats,
        "orf": "FAVORABLE",
    }


class FakeClient:
    def __init__(self, events=None, props=None, *, fail: Exception | None = None):
        self.events = events if events is not None else [_event("e1")]
        self.props = props or []
        self.fail = fail

    def fetch_schedule(self, league: str) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        return {"events": self.events}

    def fetch_player_props(self, league: str) -> dict[str, Any]:
        return {"props": self.props}


def _extract(props, events=None, sport="NBA", **kw):
    return ehr.extract_hit_props_for_sport(FakeClient(events, props), sport, SLATE, **kw)


def test_requires_both_l5_and_l10_at_100_by_default():
    rows = _extract(
        [
            _prop("A Player - Points", l5=1.0, l10=1.0),
            _prop("B Player - Points", l5=1.0, l10=0.9),
            _prop("C Player - Points", l5=1.0, l10=None),
            _prop("D Player - Points", l5=0.8, l10=1.0),
        ]
    )
    assert [r["player"] for r in rows] == ["A Player"]


def test_accepts_percent_scale_and_string_stats():
    rows = _extract([_prop("A Player - Points", l5="100", l10=100.0)])
    assert rows and rows[0]["l5_hit_rate"] == "100%" and rows[0]["l10_hit_rate"] == "100%"


def test_unparseable_stats_are_skipped_not_fatal():
    rows = _extract([_prop("A Player - Points", l5="n/a"), _prop("B Player - Points")])
    assert [r["player"] for r in rows] == ["B Player"]


def test_sort_is_numeric_descending():
    rows = _extract(
        [
            _prop("Low Player - Points", l10=0.9),
            _prop("Top Player - Points", l10=1.0),
        ],
        min_l10=80.0,
    )
    assert [r["player"] for r in rows] == ["Top Player", "Low Player"]


@pytest.mark.parametrize(
    "market",
    ["Touchdowns", "Anytime Touchdown", "Rushing TDs", "Home Runs", "To Record 10+ Assists"],
)
def test_milestone_unders_are_dropped(market):
    assert _extract([_prop(f"X Player - {market}", side="UNDER")], sport="NFL") == []


def test_milestone_overs_are_kept():
    rows = _extract([_prop("X Player - Touchdowns", side="OVER")], sport="NFL")
    assert len(rows) == 1


def test_props_without_matching_event_are_dropped_when_dated():
    rows = _extract([_prop("A Player - Points", eid="missing"), _prop("B Player - Points")])
    assert [r["player"] for r in rows] == ["B Player"]


def test_unparseable_event_time_is_dropped():
    rows = _extract([_prop("A Player - Points")], events=[_event("e1", when="not-a-time")])
    assert rows == []


def test_nba_is_same_day_only():
    rows = _extract([_prop("A Player - Points")], events=[_event("e1", when=NEXT_WEEK)])
    assert rows == []


def test_nfl_allows_the_coming_week():
    rows = _extract(
        [_prop("A Player - Rushing Yards")],
        events=[_event("e1", when="2026-11-15T18:00:00Z")],
        sport="NFL",
    )
    assert len(rows) == 1


def test_min_games_uses_results_array_when_present():
    short = _prop("Short Player - Points", l10_results=[True, True, True])
    full = _prop("Full Player - Points", l10_results=[True] * 10)
    rows = _extract([short, full])
    assert [r["player"] for r in rows] == ["Full Player"]
    assert rows[0]["sample_n"] == "10"


def test_rows_carry_slate_date_and_matchup():
    (row,) = _extract([_prop("A Player - Points")])
    assert row["slate_date"] == SLATE
    assert row["matchup"] == "PHI @ BOS" and row["team"] == "BOS"
    assert row["books"] == "FANDUEL;DRAFTKINGS"


def test_fetch_failure_propagates():
    client = FakeClient(fail=RuntimeError("HTTP 500"))
    with pytest.raises(RuntimeError):
        ehr.extract_hit_props_for_sport(client, "NBA", SLATE)


def test_export_writes_only_to_out_dir(tmp_path):
    client = FakeClient(props=[_prop("A Player - Points")])
    path = ehr.export_hit_rates_for_sport(client, "NBA", SLATE, out_dir=tmp_path)
    assert path == tmp_path / "NBA_100_hit_rate.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["player"] for r in rows] == ["A Player"]
    assert list(rows[0]) == ehr.FIELDNAMES


def test_failed_fetch_does_not_clobber_previous_csv(tmp_path):
    previous = tmp_path / "NBA_100_hit_rate.csv"
    previous.write_text("player\nkeep me\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        ehr.export_hit_rates_for_sport(
            FakeClient(fail=RuntimeError("boom")), "NBA", SLATE, out_dir=tmp_path
        )
    assert previous.read_text(encoding="utf-8") == "player\nkeep me\n"


def test_default_out_dir_is_league_exports():
    assert ehr.default_out_dir("NCAA") == REPO / "data" / "NCAAFB" / "exports"
    assert ehr.default_out_dir("NBA") == REPO / "data" / "NBA" / "exports"


def test_main_exits_nonzero_when_a_sport_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ehr, "make_client", lambda: FakeClient(fail=RuntimeError("down")))
    rc = ehr.main(["--sport", "NBA", "--date", SLATE, "--out-dir", str(tmp_path)])
    assert rc == 1
    assert not (tmp_path / "NBA_100_hit_rate.csv").exists()


def test_main_success(tmp_path, monkeypatch):
    monkeypatch.setattr(ehr, "make_client", lambda: FakeClient(props=[_prop("A Player - Points")]))
    rc = ehr.main(["--sport", "NBA", "--date", SLATE, "--out-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "NBA_100_hit_rate.csv").is_file()


def test_main_rejects_bad_date():
    with pytest.raises(SystemExit):
        ehr.main(["--sport", "NBA", "--date", "11/10/2026"])
