"""Fetch and normalize the Outlier NBA slate (plan M1/M2).

Reuses ``outlier_scrapers.api.OutlierApiClient`` and ``outlier_scrapers.normalizer``;
nothing here is NBA-model logic. Output is a flat list of :class:`OutcomeRecord`
(one per outcome, per-book prices attached) for the market layer to pair and
devig. Only full-game, two-way markets that the ledger can later grade are kept;
everything else is counted in ``Slate.skipped`` rather than dropped silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from outlier_nba.identity import resolve_team
from outlier_nba.receipts import StageReceipt, receipt
from outlier_nba.run_context import event_tip_utc, try_parse_utc
from outlier_scrapers.normalizer import normalize_games, normalize_player_props
from outlier_scrapers.registry import get_sport_config

NBA = get_sport_config("NBA", allow_disabled=True)

# Canonical codes (registry.BASKETBALL_MARKET_ALIASES) with a numeric over/under
# the results collector can grade. Fantasy-score, "most X" and first-basket
# markets are not binary over/under on a box-score stat and stay out.
PROP_MARKETS = frozenset(
    {"PTS", "REB", "AST", "3PTS", "PR", "PA", "RA", "PRA", "S+B", "STL", "BLK", "TO", "DD", "TD"}
)
GAME_MARKETS = frozenset({"ML", "SPREAD", "TOTAL"})
EVENT_MARKET_TYPES = ("GAMELINE", "TEAM_PROP")
# periodLabel values Outlier could plausibly use for a full-game market. Any other
# non-empty label is treated as a partial period (fail closed): a first-quarter
# line stamped as full-game would be graded against the final score.
FULL_GAME_LABELS = frozenset({"", "FG", "GAME", "FULL", "FULLGAME", "FULL GAME", "F"})
COMPLETE_PAGINATION = frozenset({"single_page", "exhausted"})


class NbaClient(Protocol):
    def fetch_schedule(self, league: str) -> dict[str, Any]: ...
    def fetch_player_props(self, league: str) -> dict[str, Any]: ...
    def fetch_event_markets(self, event_id: str, market_type: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OutcomeRecord:
    kind: str  # "GAME" | "TEAM" | "PROP"
    event_id: str
    market_id: str
    outcome_id: str
    market: str  # canonical code, or ML / SPREAD / TOTAL
    side: str  # OVER / UNDER / HOME / AWAY
    line: float | None
    quotes: tuple[tuple[str, int], ...]  # (book label, american odds)
    tip: datetime
    away: str | None
    home: str | None
    player: str | None = None
    player_id: str | None = None
    team: str | None = None
    primary: bool | None = None


@dataclass(frozen=True)
class RawBundle:
    schedule: dict[str, Any]
    props: dict[str, Any]
    event_markets: dict[str, list[dict[str, Any]]]
    event_errors: dict[str, str]
    fetch_started: datetime
    fetch_finished: datetime


@dataclass
class Slate:
    events: list[dict[str, Any]]
    records: list[OutcomeRecord]
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def open_events(schedule: Mapping[str, Any], as_of: datetime) -> list[dict[str, Any]]:
    """Scheduled games that have not started as of ``as_of`` (pre-tip only)."""
    out = []
    for event in schedule.get("events") or []:
        if not isinstance(event, dict) or event.get("live"):
            continue
        status = str(event.get("status") or "").lower()
        if status in {"final", "closed", "complete", "completed", "inprogress", "live"}:
            continue
        tip = event_tip_utc(event)
        if tip is not None and tip > as_of:
            out.append(event)
    return out


def fetch_raw(
    client: NbaClient, *, clock: Any, as_of: datetime | None = None, include_props: bool = True
) -> RawBundle:
    """Read-only fetch of schedule, player props (with alt ladders) and event markets.

    ``include_props=False`` skips the (large) player-prop pull for callers that price only
    team markets; ``props`` is then an empty payload rather than missing.
    """
    started = clock()
    schedule = client.fetch_schedule("NBA")
    props = client.fetch_player_props("NBA") if include_props else {"props": []}
    event_markets: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for event in open_events(schedule, as_of or started):
        event_id = str(event.get("eventId") or event.get("id") or "")
        if not event_id:
            continue
        markets: list[dict[str, Any]] = []
        for market_type in EVENT_MARKET_TYPES:
            try:
                markets.extend(
                    client.fetch_event_markets(event_id, market_type).get("markets") or []
                )
            except Exception as exc:  # recorded; the receipt decides whether it is fatal
                errors[f"{event_id}:{market_type}"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        event_markets[event_id] = markets
    return RawBundle(schedule, props, event_markets, errors, started, clock())


def _quotes(row: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    out = []
    for book in row.get("books") or []:
        odds = book.get("odds")
        if book.get("book") and isinstance(odds, int):
            out.append((str(book["book"]), odds))
    return tuple(out)


def _is_full_game(row: Mapping[str, Any]) -> bool:
    if row.get("scope") not in (None, "full_game"):
        return False
    label = str(row.get("period_label") or "").strip().upper()
    return label in FULL_GAME_LABELS and not row.get("periods")


def normalize_slate(bundle: RawBundle, as_of: datetime) -> Slate:
    """Flatten the raw bundle into pre-tip, full-game ``OutcomeRecord``s."""
    schedule = bundle.schedule
    events = open_events(schedule, as_of)
    open_ids = {str(e.get("eventId") or e.get("id") or "") for e in events}
    slate = Slate(events=events, records=[])
    primary_by_outcome = {
        str((p.get("outcome") or {}).get("outcomeId") or ""): (p.get("outcome") or {}).get(
            "primary"
        )
        for p in bundle.props.get("props") or []
        if isinstance(p, dict)
    }

    for row in normalize_player_props(bundle.props, schedule, NBA):
        if row["event_id"] not in open_ids:
            slate.skip("prop_event_not_open")
            continue
        scope = (row.get("sport_context") or {}).get("scope")
        if scope != "full_game":
            slate.skip("prop_partial_scope")
            continue
        if row.get("market") not in PROP_MARKETS:
            slate.skip("prop_market_out_of_scope")
            continue
        tip = try_parse_utc((row.get("sport_context") or {}).get("event_starts_at"))
        if tip is None:
            slate.skip("prop_no_tip")
            continue
        away, home = _teams_from_matchup(row.get("matchup"))
        slate.records.append(
            OutcomeRecord(
                kind="PROP",
                event_id=row["event_id"],
                market_id=str(row["market_id"]),
                outcome_id=row["outcome_id"],
                market=row["market"],
                side=row["side"],
                line=row["line"],
                quotes=_quotes(row),
                tip=tip,
                away=away,
                home=home,
                player=row["player_raw"],
                player_id=row.get("player_id"),
                team=row.get("team"),
                primary=primary_by_outcome.get(row["outcome_id"]),
            )
        )

    events_payloads = [
        {"eventId": event_id, "markets": markets}
        for event_id, markets in bundle.event_markets.items()
    ]
    games = normalize_games(
        config=NBA, schedule_payload=schedule, events_payloads=events_payloads, source_url="api"
    )
    for row in games["records"]:
        if row["event_id"] not in open_ids or row.get("is_active") is False:
            slate.skip("game_event_not_open_or_inactive")
            continue
        if not _is_full_game(row):
            slate.skip("game_partial_period")
            continue
        record = _game_record(row)
        if record is None:
            slate.skip("game_market_out_of_scope")
            continue
        slate.records.append(record)
    return slate


def _teams_from_matchup(matchup: Any) -> tuple[str | None, str | None]:
    if not matchup or " @ " not in str(matchup):
        return None, None
    away, home = str(matchup).split(" @ ", 1)
    return resolve_team(away), resolve_team(home)


def _game_record(row: Mapping[str, Any]) -> OutcomeRecord | None:
    market_type, market = row.get("market_type"), row.get("market")
    side = str(row.get("position") or "").upper()
    tip = try_parse_utc(row.get("event_starts_at"))
    away, home = _teams_from_matchup(row.get("matchup"))
    if tip is None or not row.get("outcome_id") or not row.get("market_id"):
        return None
    if away is None or home is None:  # an unresolved team would write an ungradeable "? @ ?"
        return None
    if market_type == "GAMELINE" and market in GAME_MARKETS:
        expected = {"ML": {"HOME", "AWAY"}, "SPREAD": {"HOME", "AWAY"}, "TOTAL": {"OVER", "UNDER"}}
        if side not in expected[str(market)]:
            return None
        line = row.get("line")
        if market == "ML":
            line = None
        elif line is None:
            return None
        return OutcomeRecord(
            "GAME",
            row["event_id"],
            row["market_id"],
            row["outcome_id"],
            str(market),
            side,
            line,
            _quotes(row),
            tip,
            away,
            home,
            team=row.get("team"),
        )
    if market_type == "TEAM_PROP" and market == "PTS" and side in {"OVER", "UNDER"}:
        if row.get("team") is None or row.get("line") is None:
            return None
        return OutcomeRecord(
            "TEAM",
            row["event_id"],
            row["market_id"],
            row["outcome_id"],
            "TEAM_TOTAL",
            side,
            row["line"],
            _quotes(row),
            tip,
            away,
            home,
            team=row["team"],
        )
    return None


def raw_receipts(bundle: RawBundle, slate: Slate) -> list[StageReceipt]:
    """Stage receipts for the three required feeds (schedule, player props, game markets)."""
    out: list[StageReceipt] = []
    events = bundle.schedule.get("events")
    if not isinstance(events, list):
        out.append(receipt("schedule", "INVALID", reason="schedule payload has no events list"))
    else:
        out.append(receipt("schedule", "OK" if events else "EMPTY", received=len(events)))

    summary = bundle.props.get("_page_summary") or {}
    props = bundle.props.get("props")
    reason = str(summary.get("stopped_reason") or "single_page")
    reported, merged = summary.get("total_reported"), summary.get("merged_props")
    if not isinstance(props, list):
        out.append(receipt("player_props", "INVALID", reason="payload has no props list"))
    elif reason not in COMPLETE_PAGINATION or (
        isinstance(reported, int) and isinstance(merged, int) and merged < reported
    ):
        out.append(
            receipt(
                "player_props",
                "INCOMPLETE",
                reason=f"pagination stopped: {reason}; merged {merged} of {reported}",
                expected=reported if isinstance(reported, int) else None,
                received=len(props),
            )
        )
    elif not props and slate.events:
        out.append(
            receipt(
                "player_props",
                "INCOMPLETE",
                reason="0 props for a slate with open events "
                "(not posted yet, or a feed glitch); rerun later",
                expected=len(slate.events),
                received=0,
            )
        )
    else:
        out.append(receipt("player_props", "OK" if props else "EMPTY", received=len(props)))

    wanted = len(slate.events) * len(EVENT_MARKET_TYPES)
    if bundle.event_errors and not any(bundle.event_markets.values()):
        out.append(
            receipt(
                "game_markets",
                "FAILED",
                reason="every event-market request failed",
                expected=wanted,
                errors=list(bundle.event_errors.values()),
            )
        )
    elif bundle.event_errors:
        out.append(
            receipt(
                "game_markets",
                "INCOMPLETE",
                reason=f"{len(bundle.event_errors)} request(s) failed",
                expected=wanted,
                received=wanted - len(bundle.event_errors),
                errors=list(bundle.event_errors.values()),
            )
        )
    elif slate.events and not any(bundle.event_markets.get(e_id) for e_id in _open_ids(slate)):
        out.append(
            receipt(
                "game_markets",
                "INCOMPLETE",
                reason="requests succeeded but returned no markets for any open event",
                expected=wanted,
                received=0,
            )
        )
    else:
        out.append(
            receipt(
                "game_markets", "OK" if slate.events else "EMPTY", expected=wanted, received=wanted
            )
        )
    return out


def _open_ids(slate: Slate) -> list[str]:
    return [str(e.get("eventId") or e.get("id") or "") for e in slate.events]


def count_by_kind(records: Sequence[OutcomeRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    return counts
