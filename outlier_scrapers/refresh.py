from __future__ import annotations

import argparse
import sys

from .api import OutlierApiClient
from .discover import summarize_league, write_discovery_report
from .line_movement import export_line_movement_for_league
from .props import export_props_for_league
from .registry import supported_leagues


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh standalone Outlier sports data")
    parser.add_argument("--league", action="append", choices=supported_leagues(), required=True)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--props", action="store_true")
    parser.add_argument("--line-movement", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.discover and not args.props and not args.line_movement:
        print("Nothing requested. Use --discover, --props, and/or --line-movement.")
        return 2

    try:
        client = OutlierApiClient()
    except Exception as exc:
        print(f"auth_required: {exc}")
        return 1

    exit_code = 0
    for league in args.league:
        props_failed = False
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
        if args.line_movement:
            if args.props and props_failed:
                print(f"{league.upper()} line movement: skipped (props failed)")
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
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
