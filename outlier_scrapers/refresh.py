from __future__ import annotations

import argparse
import sys

from .api import OutlierApiClient
from .cards import export_cards_for_league
from .discover import summarize_league, write_discovery_report
from .insights import export_insights_for_league
from .line_movement import export_line_movement_for_league
from .props import export_props_for_league
from .registry import supported_leagues


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh standalone Outlier sports data")
    parser.add_argument("--league", action="append", choices=supported_leagues(), required=True)
    parser.add_argument("--all", action="store_true", help="Run props, insights, line-movement, and cards")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--props", action="store_true")
    parser.add_argument("--insights", action="store_true")
    parser.add_argument("--line-movement", action="store_true")
    parser.add_argument("--cards", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    
    if args.all:
        args.props = True
        args.insights = True
        args.line_movement = True
        args.cards = True

    if not args.discover and not args.props and not args.insights and not args.line_movement and not args.cards:
        print("Nothing requested. Use --all, or explicit flags like --props, --line-movement, --cards.")
        return 2

    # Cards are built from local *_latest.json files and need no API session.
    # Only construct the client when a feed that actually fetches is requested,
    # so `refresh --cards` can rebuild the board offline with a stale session.
    needs_api = args.discover or args.props or args.insights or args.line_movement
    client = None
    if needs_api:
        try:
            client = OutlierApiClient()
        except Exception as exc:
            print(f"auth_required: {exc}")
            return 1

    exit_code = 0
    for league in args.league:
        props_failed = False
        line_movement_failed = False
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
                props_failed = True
                exit_code = 1
        if args.insights:
            try:
                status = export_insights_for_league(client, league)
                print(f"{league.upper()} insights: exported {status['record_count']} insights")
            except Exception as exc:
                print(f"{league.upper()} insights: failed ({str(exc)[:200]})")
                exit_code = 1
        if args.line_movement:
            if args.props and props_failed:
                print(f"{league.upper()} line movement: skipped (props failed)")
                line_movement_failed = True
                exit_code = 1
                continue
            try:
                status = export_line_movement_for_league(client, league)
                print(
                    f"{league.upper()} line movement: exported {status['record_count']} "
                    f"records from {status['markets_fetched']}/{status['markets_requested']} markets"
                )
            except Exception as exc:
                print(f"{league.upper()} line movement: failed ({str(exc)[:200]})")
                line_movement_failed = True
                exit_code = 1
        if args.cards:
            if (args.props and props_failed) or (args.line_movement and line_movement_failed):
                print(f"{league.upper()} cards: skipped (upstream feeds failed)")
                exit_code = 1
                continue
            try:
                status = export_cards_for_league(league)
                print(
                    f"{league.upper()} cards: exported {status['coverage']['cards_total']} cards "
                    f"(Board A={status['coverage']['board_a_cards']}, Board B={status['coverage']['board_b_cards']})"
                )
            except Exception as exc:
                print(f"{league.upper()} cards: failed ({str(exc)[:200]})")
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
