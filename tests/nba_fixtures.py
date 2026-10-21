"""Synthetic Outlier NBA payloads (shapes copied from stored WNBA/MLB raw feeds)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

SLATE_DATE = "2026-10-21"
TIP1 = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)  # NYK @ BOS
TIP2 = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)  # GS @ LAL  (ESPN: GS / LAL)
STARTED_TIP = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)  # already underway at capture time
EV1, EV2, EV_STARTED = "ev-nyk-bos", "ev-gs-lal", "ev-started"
BOOKS = ["DRAFTKINGS", "FANDUEL", "CAESARS", "BETMGM"]


def _event(event_id: str, tip: datetime, away: tuple[str, str], home: tuple[str, str], **kw):
    return {
        "leagueId": "NBA",
        "eventId": event_id,
        "scheduledTime": tip.isoformat(),
        "live": False,
        "status": "pregame",
        "away": {"teamId": f"t-{away[0]}", "name": away[1], "alias": away[0], "market": ""},
        "home": {"teamId": f"t-{home[0]}", "name": home[1], "alias": home[0], "market": ""},
        **kw,
    }


def schedule() -> dict[str, Any]:
    return {
        "events": [
            _event(EV1, TIP1, ("NY", "Knicks"), ("BOS", "Celtics")),
            _event(EV2, TIP2, ("GS", "Warriors"), ("LAL", "Lakers")),
            _event(EV_STARTED, STARTED_TIP, ("MIA", "Heat"), ("ORL", "Magic")),
        ]
    }


def _book_odds(prices: dict[str, int]) -> dict[str, Any]:
    return {b: {"odds": f"{p:+d}", "decimal": 2.0} for b, p in prices.items()}


def prop(
    event_id: str,
    market_id: str,
    outcome_id: str,
    player: str,
    proposition: str,
    line: float,
    side: str,
    prices: dict[str, int],
    *,
    primary: bool = True,
    team: str = "t-NY",
    opp: str = "t-BOS",
    label: str | None = None,
) -> dict[str, Any]:
    best = max(prices.values())
    return {
        "outcome": {
            "leagueId": "NBA",
            "eventId": event_id,
            "playerId": f"p-{player}",
            "teamId": team,
            "oppTeamId": opp,
            "marketId": market_id,
            "outcomeId": outcome_id,
            "marketLabel": label or f"{player} - {proposition.title()}",
            "proposition": proposition,
            "line": line,
            "position": side,
            "bestOdds": f"{best:+d}",
            "primary": primary,
            "active": True,
            "books": list(prices),
            "bookOdds": _book_odds(prices),
        },
        "stats": {},
        "groupId": "OFFENSIVE_PROPS",
    }


def player_props() -> dict[str, Any]:
    brunson = "Jalen Brunson"
    pts = lambda oid, line, side, prices, **kw: prop(  # noqa: E731
        EV1, "m-brunson-pts", oid, brunson, "POINTS", line, side, prices, **kw
    )
    two_way_over = {
        "DRAFTKINGS": -115,
        "FANDUEL": -110,
        "CAESARS": -105,
        "BETMGM": -120,
        "PRIZEPICKS": 100,
    }
    two_way_under = {
        "DRAFTKINGS": -105,
        "FANDUEL": -110,
        "CAESARS": -115,
        "BETMGM": +100,
        "PRIZEPICKS": 100,
    }
    props = [
        pts("o-pts-26.5-over", 26.5, "OVER", two_way_over),
        pts("o-pts-26.5-under", 26.5, "UNDER", two_way_under),
        # alt ladder: OVER-only rungs, one an integer line
        pts("o-pts-24-over", 24.0, "OVER", {"DRAFTKINGS": -240, "FANDUEL": -250}, primary=False),
        pts(
            "o-pts-29.5-over",
            29.5,
            "OVER",
            {"DRAFTKINGS": +180, "FANDUEL": +170, "CAESARS": +190},
            primary=False,
        ),
        # thin two-way market: only two books
        prop(
            EV1,
            "m-towns-reb",
            "o-reb-over",
            "Karl-Anthony Towns",
            "REBOUNDS",
            11.5,
            "OVER",
            {"DRAFTKINGS": -110, "FANDUEL": -112},
        ),
        prop(
            EV1,
            "m-towns-reb",
            "o-reb-under",
            "Karl-Anthony Towns",
            "REBOUNDS",
            11.5,
            "UNDER",
            {"DRAFTKINGS": -110, "FANDUEL": -108},
        ),
        # triple-double (OVER only by construction)
        prop(
            EV2,
            "m-curry-td",
            "o-td-over",
            "Stephen Curry",
            "TRIPLE_DOUBLE",
            0.5,
            "OVER",
            {"DRAFTKINGS": +1800, "FANDUEL": +2000},
            team="t-GS",
            opp="t-LAL",
        ),
        # fantasy score: not a box-score over/under -> out of scope
        prop(
            EV1,
            "m-fan",
            "o-fan-over",
            brunson,
            "FANTASY_SCORE_PP",
            40.5,
            "OVER",
            {"PRIZEPICKS": 100},
        ),
        # the started game's props must never be captured
        prop(
            EV_STARTED,
            "m-bam-pts",
            "o-bam-over",
            "Bam Adebayo",
            "POINTS",
            18.5,
            "OVER",
            {"DRAFTKINGS": -110, "FANDUEL": -110, "CAESARS": -110},
            team="t-MIA",
            opp="t-ORL",
        ),
    ]
    return {
        "props": props,
        "_page": {},
        "_page_summary": {
            "pages_reported": 1,
            "total_reported": len(props),
            "pages_fetched": 1,
            "merged_records": len(props),
            "merged_props": len(props),
            "stopped_reason": "single_page",
        },
    }


def _game_outcome(oid: str, position: str, line: float | None, prices: dict[str, int]) -> dict:
    return {
        "outcomeId": oid,
        "position": position,
        "line": line,
        "label": position,
        "odds": [{"american": f"{p:+d}", "decimal": 2.0, "book": b} for b, p in prices.items()],
        "books": list(prices),
    }


def _market(
    event_id: str, market_id: str, mtype: str, proposition: str, outcomes: list[dict], **kw
) -> dict:
    return {
        "marketId": market_id,
        "eventId": event_id,
        "leagueId": "NBA",
        "marketType": mtype,
        "proposition": proposition,
        "includeOvertime": True,
        "outcomes": outcomes,
        **kw,
    }


def event_markets() -> dict[str, list[dict[str, Any]]]:
    sym = {b: -110 for b in BOOKS}
    ev1 = [
        _market(
            EV1,
            "m-spread",
            "GAMELINE",
            "SPREAD",
            [
                _game_outcome("o-sp-home", "HOME", -4.5, sym),
                _game_outcome("o-sp-away", "AWAY", 4.5, sym),
                # integer spread rung, two-sided with half-point neighbours
                _game_outcome("o-sp-home-4", "HOME", -4.0, {b: -125 for b in BOOKS}),
                _game_outcome("o-sp-away-4", "AWAY", 4.0, {b: +105 for b in BOOKS}),
            ],
        ),
        _market(
            EV1,
            "m-total",
            "GAMELINE",
            "TOTAL",
            [
                _game_outcome("o-tot-over", "OVER", 221.5, sym),
                _game_outcome("o-tot-under", "UNDER", 221.5, sym),
                # integer rung bracketed by two-sided half-lines: push is derivable
                _game_outcome("o-tot-over-220.5", "OVER", 220.5, {b: -150 for b in BOOKS}),
                _game_outcome("o-tot-under-220.5", "UNDER", 220.5, {b: +130 for b in BOOKS}),
                _game_outcome("o-tot-over-221", "OVER", 221.0, {b: -125 for b in BOOKS}),
                _game_outcome("o-tot-under-221", "UNDER", 221.0, {b: +105 for b in BOOKS}),
            ],
        ),
        _market(
            EV1,
            "m-ml",
            "GAMELINE",
            "MONEYLINE",
            [
                _game_outcome("o-ml-home", "HOME", None, {b: -190 for b in BOOKS}),
                _game_outcome("o-ml-away", "AWAY", None, {b: +160 for b in BOOKS}),
            ],
        ),
        _market(
            EV1,
            "m-h1-total",
            "GAMELINE",
            "TOTAL",
            [
                _game_outcome("o-h1-over", "OVER", 111.5, sym),
                _game_outcome("o-h1-under", "UNDER", 111.5, sym),
            ],
            periodLabel="1H",
            includeOvertime=False,
        ),
        _market(
            EV1,
            "m-bos-tt",
            "TEAM_PROP",
            "POINTS",
            [
                _game_outcome("o-tt-over", "OVER", 112.5, sym),
                _game_outcome("o-tt-under", "UNDER", 112.5, sym),
            ],
            teamId="t-BOS",
        ),
    ]
    ev2 = [
        _market(
            EV2,
            "m2-total",
            "GAMELINE",
            "TOTAL",
            [
                _game_outcome("o2-tot-over", "OVER", 229.5, sym),
                _game_outcome("o2-tot-under", "UNDER", 229.5, sym),
            ],
        ),
    ]
    return {EV1: ev1, EV2: ev2}


class FakeClient:
    def __init__(
        self,
        *,
        fail_markets_for: set[str] | None = None,
        props_payload: dict | None = None,
        sched: dict | None = None,
    ) -> None:
        self.fail = fail_markets_for or set()
        self.props_payload = props_payload
        self.sched = sched
        self.calls: list[str] = []

    def fetch_schedule(self, league: str) -> dict[str, Any]:
        self.calls.append(f"schedule:{league}")
        return self.sched if self.sched is not None else schedule()

    def fetch_player_props(self, league: str) -> dict[str, Any]:
        self.calls.append(f"props:{league}")
        return self.props_payload if self.props_payload is not None else player_props()

    def fetch_event_markets(self, event_id: str, market_type: str) -> dict[str, Any]:
        self.calls.append(f"markets:{event_id}:{market_type}")
        if event_id in self.fail:
            raise RuntimeError("boom")
        markets = [m for m in event_markets().get(event_id, []) if m["marketType"] == market_type]
        return {"markets": markets}


def clock_at(moment: datetime):
    """A clock returning ``moment`` then ``moment + 20s`` (fetch start, fetch end)."""
    ticks = iter([moment, moment + timedelta(seconds=20)] + [moment + timedelta(seconds=20)] * 50)
    return lambda: next(ticks)
