from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from .api import OutlierApiClient
from .cards import export_cards_for_league, export_game_cards_for_league
from .discover import summarize_league, write_discovery_report
from .games import export_games_for_league
from .insights import export_insights_for_league
from .line_movement import export_line_movement_for_league
from .paths import league_paths
from .probable_pitchers import export_probable_pitchers
from .props import export_props_for_league
from .registry import supported_leagues
from .slate_strategy import export_slate_strategy_for_league


_PRODUCER_STATUS_FILES = {
    "props": "props_export_status_latest.json",
    "insights": "insights_status_latest.json",
    "line_movement": "line_movement_status_latest.json",
    "games": "games_status_latest.json",
    "game_line_movement": "games_line_movement_status_latest.json",
    "cards": "cards_status_latest.json",
    "game_cards": "games_cards_status_latest.json",
    "slate_strategy": "slate_strategy_status_latest.json",
}


def _atomic_write_failure_status(league: str, producer: str, exc: Exception) -> None:
    """Replace a producer's prior status so a failed refresh cannot look healthy."""
    status_path = league_paths(league).ensure().reports / _PRODUCER_STATUS_FILES[producer]
    payload = {
        "league": league.strip().upper(),
        "status": "error",
        "generated_at": datetime.now().astimezone().isoformat(),
        "error": str(exc)[:300],
    }
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=status_path.parent,
            prefix=f".{status_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, status_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh standalone Outlier sports data")
    parser.add_argument("--league", action="append", choices=supported_leagues(), required=True)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run props, insights, line-movement, cards, games, game-line-movement, game-cards, and slate-strategy",
    )
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--props", action="store_true")
    parser.add_argument("--insights", action="store_true")
    parser.add_argument("--line-movement", action="store_true")
    parser.add_argument("--cards", action="store_true")
    parser.add_argument("--game-cards", action="store_true")
    parser.add_argument("--games", action="store_true")
    parser.add_argument(
        "--game-line-movement",
        "--games-with-detail",
        action="store_true",
        dest="game_line_movement",
    )
    parser.add_argument("--probable-pitchers", action="store_true", dest="probable_pitchers")
    parser.add_argument("--slate-strategy", action="store_true", dest="slate_strategy")
    parser.add_argument(
        "--date",
        help="Pin games scrape to YYYY-MM-DD and disable today-to-tomorrow auto-advance.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.all:
        args.props = True
        args.insights = True
        args.line_movement = True
        args.games = True
        args.game_line_movement = True
        args.cards = True
        args.game_cards = True
        args.slate_strategy = True

    if (
        not args.discover
        and not args.props
        and not args.insights
        and not args.line_movement
        and not args.cards
        and not args.game_cards
        and not args.games
        and not args.game_line_movement
        and not args.probable_pitchers
        and not args.slate_strategy
    ):
        print(
            "Nothing requested. Use --all, or explicit flags like --props, --line-movement, --cards, --game-cards, --games, --slate-strategy."
        )
        return 2

    # Cards are built from local *_latest.json files and need no API session.
    # Only construct the client when a feed that actually fetches is requested,
    # so `refresh --cards` can rebuild the board offline with a stale session.
    needs_api = (
        args.discover
        or args.props
        or args.insights
        or args.line_movement
        or args.games
        or args.game_line_movement
    )
    client = None
    if needs_api:
        try:
            client = OutlierApiClient()
        except Exception as exc:
            requested_api_producers = (
                ("props", args.props),
                ("insights", args.insights),
                ("line_movement", args.line_movement),
                ("games", args.games),
                ("game_line_movement", args.game_line_movement),
                ("cards", args.cards),
                ("game_cards", args.game_cards),
            )
            for league in args.league:
                for producer, requested in requested_api_producers:
                    if requested:
                        _atomic_write_failure_status(league, producer, exc)
            print(f"auth_required: {exc}")
            return 1

    target_date: date | None = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD.")
            return 2

    exit_code = 0
    for league in args.league:
        props_failed = False
        line_movement_failed = False
        games_failed = False
        game_line_movement_failed = False

        if args.discover:
            report = summarize_league(client, league)
            write_discovery_report(league, report)
            print(f"{league.upper()} discovery: {report['status']}")
            if report["status"] != "ok":
                exit_code = 1

        if args.props:
            try:
                status = export_props_for_league(client, league)
                print(f"{league.upper()} props: exported {status['record_count']} records")
            except Exception as exc:
                print(f"{league.upper()} props: failed ({str(exc)[:200]})")
                _atomic_write_failure_status(league, "props", exc)
                props_failed = True
                exit_code = 1

        if args.insights:
            try:
                status = export_insights_for_league(client, league)
                print(f"{league.upper()} insights: exported {status['record_count']} insights")
            except Exception as exc:
                print(f"{league.upper()} insights: failed ({str(exc)[:200]})")
                _atomic_write_failure_status(league, "insights", exc)
                exit_code = 1

        if args.games:
            try:
                status = export_games_for_league(client, league, target_date=target_date)
                if status.get("status") == "error":
                    print(
                        f"{league.upper()} games: error "
                        f"({status.get('fetch_error_count', 0)} fetch errors, {status['record_count']} records); "
                    )
                    games_failed = True
                    exit_code = 1
                else:
                    suffix = " [partial]" if status.get("status") == "partial" else ""
                    print(
                        f"{league.upper()} games: exported {status['record_count']} records{suffix}"
                    )
            except Exception as exc:
                print(f"{league.upper()} games: failed ({str(exc)[:200]})")
                _atomic_write_failure_status(league, "games", exc)
                games_failed = True
                exit_code = 1

        if args.probable_pitchers:
            try:
                status = export_probable_pitchers(league)
                if status["status"] == "skipped":
                    print(f"{league.upper()} probable pitchers: skipped ({status['reason']})")
                else:
                    print(
                        f"{league.upper()} probable pitchers: exported {status['record_count']} records"
                    )
                    if status["status"] == "error":
                        exit_code = 1
            except Exception as exc:
                print(f"{league.upper()} probable pitchers: failed ({str(exc)[:200]})")
                exit_code = 1

        if args.line_movement:
            if args.props and props_failed:
                print(f"{league.upper()} line movement: skipped (props failed)")
                _atomic_write_failure_status(
                    league,
                    "line_movement",
                    RuntimeError("skipped because props refresh failed"),
                )
                line_movement_failed = True
                exit_code = 1
            else:
                try:
                    status = export_line_movement_for_league(client, league)
                    print(
                        f"{league.upper()} line movement: exported {status['record_count']} "
                        f"records from {status['markets_fetched']}/{status['markets_requested']} markets"
                    )
                except Exception as exc:
                    print(f"{league.upper()} line movement: failed ({str(exc)[:200]})")
                    _atomic_write_failure_status(league, "line_movement", exc)
                    line_movement_failed = True
                    exit_code = 1

        if args.game_line_movement:
            if args.games and games_failed:
                print(f"{league.upper()} game line movement: skipped (games failed)")
                _atomic_write_failure_status(
                    league,
                    "game_line_movement",
                    RuntimeError("skipped because games refresh failed"),
                )
                exit_code = 1
                game_line_movement_failed = True
            else:
                try:
                    status = export_line_movement_for_league(client, league, source="games")
                    print(
                        f"{league.upper()} game line movement: exported {status['record_count']} records"
                    )
                except FileNotFoundError as exc:
                    print(
                        f"{league.upper()} game line movement: missing input ({exc}). Run with --games first."
                    )
                    _atomic_write_failure_status(league, "game_line_movement", exc)
                    exit_code = 1
                    game_line_movement_failed = True
                except Exception as exc:
                    print(f"{league.upper()} game line movement: failed ({str(exc)[:200]})")
                    _atomic_write_failure_status(league, "game_line_movement", exc)
                    exit_code = 1
                    game_line_movement_failed = True

        if args.slate_strategy:
            try:
                status = export_slate_strategy_for_league(league)
                event_count = len(status.get("events") or [])
                if status.get("status") == "skipped":
                    print(
                        f"{league.upper()} slate strategy: skipped ({status.get('reason') or 'unsupported'})"
                    )
                elif status.get("status") == "error":
                    print(
                        f"{league.upper()} slate strategy: failed ({status.get('reason') or 'error'})"
                    )
                    _atomic_write_failure_status(
                        league,
                        "slate_strategy",
                        RuntimeError(str(status.get("reason") or "error")),
                    )
                    exit_code = 1
                else:
                    print(f"{league.upper()} slate strategy: exported {event_count} events")
            except Exception as exc:
                print(f"{league.upper()} slate strategy: failed ({str(exc)[:200]})")
                _atomic_write_failure_status(league, "slate_strategy", exc)
                exit_code = 1

        if args.cards:
            # Cards rebuild standard boards here
            if (args.props and props_failed) or (args.line_movement and line_movement_failed):
                print(f"{league.upper()} cards: skipped (upstream feeds failed)")
                _atomic_write_failure_status(
                    league,
                    "cards",
                    RuntimeError("skipped because an upstream player feed failed"),
                )
                exit_code = 1
            else:
                try:
                    status = export_cards_for_league(league)
                    print(
                        f"{league.upper()} cards: exported {status['coverage']['cards_total']} cards "
                        f"(Board A={status['coverage']['board_a_cards']}, Board B={status['coverage']['board_b_cards']})"
                    )
                except Exception as exc:
                    print(f"{league.upper()} cards: failed ({str(exc)[:200]})")
                    _atomic_write_failure_status(league, "cards", exc)
                    exit_code = 1

        if args.game_cards:
            # Rebuild game cards whenever game cards are requested
            if not games_failed and not game_line_movement_failed:
                try:
                    g_status = export_game_cards_for_league(league)
                    print(
                        f"{league.upper()} game cards: exported {g_status['coverage']['cards_total']} cards"
                    )
                except Exception as exc:
                    print(f"{league.upper()} game cards: failed ({str(exc)[:200]})")
                    _atomic_write_failure_status(league, "game_cards", exc)
                    exit_code = 1
            else:
                print(f"{league.upper()} game cards: skipped (upstream feeds failed)")
                _atomic_write_failure_status(
                    league,
                    "game_cards",
                    RuntimeError("skipped because an upstream game feed failed"),
                )
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
