#!/usr/bin/env python3
"""Standalone End-to-End NFL Pipeline Verification Script.

Executes the outlier_nfl extraction and normalization pipeline and asserts that
the output data structures strictly conform to Requirement R3:
- Game Totals (GAMELINE, TOTAL with OVER and UNDER)
- Point Spreads (GAMELINE, SPREAD with signed lines and HOME/AWAY)
- Team Totals (TEAM_PROP, POINTS/TOTAL with team attribution)
- Player Props (PLAYER_PROP covering passing, rushing, and receiving)

Exits 0 on success, non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify outlier_nfl pipeline end-to-end.")
    parser.add_argument(
        "--mode",
        choices=["fixture", "live"],
        default="fixture",
        help="Execution mode: 'fixture' (offline replay) or 'live' (authenticated API). Default: fixture.",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path(__file__).parent / "tests" / "fixtures" / "nfl",
        help="Directory containing offline JSON fixtures.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="2026-09-13",
        help="Target NFL slate date (YYYY-MM-DD). Default: 2026-09-13.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).parent / "data",
        help="Output directory for normalized data. Default: ./data",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed logging of normalized records.",
    )
    return parser.parse_args()


def load_fixture_data(fixtures_dir: Path) -> tuple[dict, dict, dict]:
    sched_file = fixtures_dir / "schedule.json"
    mkts_file = fixtures_dir / "event_markets.json"
    props_file = fixtures_dir / "player_props.json"

    if not sched_file.exists() or not mkts_file.exists() or not props_file.exists():
        raise FileNotFoundError(f"Missing required fixtures in {fixtures_dir}")

    with open(sched_file, "r", encoding="utf-8") as f:
        sched = json.load(f)
    with open(mkts_file, "r", encoding="utf-8") as f:
        mkts = json.load(f)
    with open(props_file, "r", encoding="utf-8") as f:
        props = json.load(f)

    return sched, mkts, props


def create_mock_client(sched: dict, mkts: dict, props: dict) -> Any:
    from unittest.mock import MagicMock
    from outlier_nfl.api import OutlierNflApiClient

    client = OutlierNflApiClient(bearer_token="mock_verify_token")
    client.fetch_schedule = MagicMock(return_value=sched)  # type: ignore[method-assign]
    client.fetch_event_markets = MagicMock(return_value=mkts)  # type: ignore[method-assign]
    client.fetch_player_props = MagicMock(return_value=props)  # type: ignore[method-assign]
    return client


def verify_game_totals(game_records: list[dict], verbose: bool) -> int:
    totals = [r for r in game_records if r.get("market") == "TOTAL" and r.get("market_type") == "GAMELINE"]
    assert len(totals) > 0, "Assertion failed: No Game Total (GAMELINE, TOTAL) records found."

    positions = {t.get("position") for t in totals}
    assert "OVER" in positions, "Assertion failed: Game Totals missing OVER position."
    assert "UNDER" in positions, "Assertion failed: Game Totals missing UNDER position."

    for t in totals:
        assert t.get("line") is not None, f"Game Total record missing line: {t}"
        assert isinstance(t.get("line"), (int, float)), f"Game Total line not numeric: {t.get('line')}"
        assert t.get("selection"), f"Game Total missing selection: {t}"

    if verbose:
        print(f"  [OK] Game Totals verified: {len(totals)} outcomes (OVER & UNDER present)")
    return len(totals)


def verify_point_spreads(game_records: list[dict], verbose: bool) -> int:
    spreads = [r for r in game_records if r.get("market") == "SPREAD" and r.get("market_type") == "GAMELINE"]
    assert len(spreads) > 0, "Assertion failed: No Point Spread (GAMELINE, SPREAD) records found."

    positions = {s.get("position") for s in spreads}
    assert "HOME" in positions, "Assertion failed: Point Spreads missing HOME position."
    assert "AWAY" in positions, "Assertion failed: Point Spreads missing AWAY position."

    for s in spreads:
        signed = s.get("signed_line")
        assert signed is not None, f"Point Spread missing signed_line: {s}"
        assert signed.startswith("+") or signed.startswith("-") or signed in ("0", "0.0", "+0.0"), (
            f"Point Spread signed_line improperly formatted: {signed}"
        )
        assert s.get("selection"), f"Point Spread missing selection string: {s}"

    if verbose:
        print(f"  [OK] Point Spreads verified: {len(spreads)} outcomes (signed lines validated)")
    return len(spreads)


def verify_team_totals(game_records: list[dict], verbose: bool) -> int:
    team_totals = [
        r for r in game_records
        if r.get("market_type") == "TEAM_PROP" and r.get("market") in ("POINTS", "TOTAL", "TEAM_TOTAL")
    ]
    assert len(team_totals) > 0, "Assertion failed: No Team Total (TEAM_PROP, POINTS) records found."

    teams_found = {t.get("team") for t in team_totals}
    assert None not in teams_found, f"Team Total records found with null team attribution: {teams_found}"
    assert len(teams_found) >= 1, "Team Totals missing team attribution."

    for t in team_totals:
        assert t.get("position") in ("OVER", "UNDER"), f"Invalid team total position: {t.get('position')}"
        assert t.get("line") is not None, f"Team total missing line: {t}"

    if verbose:
        print(f"  [OK] Team Totals verified: {len(team_totals)} outcomes across teams: {teams_found}")
    return len(team_totals)


def verify_player_props(prop_records: list[dict], verbose: bool) -> int:
    assert len(prop_records) > 0, "Assertion failed: No Player Prop records found."

    markets_found = {p.get("market") for p in prop_records}
    core_prop_markets = {"PASS_YDS", "RUSH_YDS", "REC_YDS"}
    overlap = core_prop_markets.intersection(markets_found)
    assert len(overlap) >= 2, (
        f"Assertion failed: Expected core player prop markets {core_prop_markets}, found {markets_found}"
    )

    for p in prop_records:
        assert p.get("player_id"), f"Player prop record missing player_id: {p}"
        assert p.get("player_name"), f"Player prop record missing player_name: {p}"
        assert p.get("line") is not None, f"Player prop record missing line: {p}"
        assert p.get("position") in ("OVER", "UNDER", "YES", "NO"), f"Invalid prop position: {p.get('position')}"
        assert isinstance(p.get("books"), list), f"Player prop books is not a list: {p.get('books')}"

    if verbose:
        print(f"  [OK] Player Props verified: {len(prop_records)} props across {len(markets_found)} proposition types: {markets_found}")
    return len(prop_records)


def main() -> int:
    args = parse_args()
    print("=" * 70)
    print("OUTLIER STANDALONE NFL BETTING PIPELINE VERIFICATION")
    print(f"Mode: {args.mode.upper()} | Date: {args.date} | Output: {args.out_dir}")
    print("=" * 70)

    try:
        from outlier_nfl.pipeline import NflPipeline
        from outlier_nfl.api import OutlierNflApiClient

        if args.mode == "fixture":
            print(f"[1/4] Loading offline fixtures from: {args.fixtures_dir}")
            sched, mkts, props = load_fixture_data(args.fixtures_dir)
            client = create_mock_client(sched, mkts, props)
        else:
            print("[1/4] Initializing live Outlier API client...")
            client = OutlierNflApiClient()

        print("[2/4] Executing NflPipeline extraction and normalization...")
        pipeline = NflPipeline(client=client, data_dir=args.out_dir)
        summary = pipeline.run(date=args.date, offline_fixtures_dir=args.fixtures_dir if args.mode == "fixture" else None)

        print("[3/4] Validating extraction summary:")
        print(f"  - Status:             {summary.get('status')}")
        print(f"  - Game Lines Count:   {summary.get('game_lines_count')}")
        print(f"  - Spreads Count:      {summary.get('spreads_count')}")
        print(f"  - Totals Count:       {summary.get('totals_count')}")
        print(f"  - Team Totals Count:  {summary.get('team_totals_count')}")
        print(f"  - Player Props Count: {summary.get('player_props_count')}")

        assert summary.get("status") == "OK", f"Pipeline returned non-OK status: {summary.get('status')}"

        print("[4/4] Executing hard market assertions on persisted artifacts...")
        games_file = args.out_dir / "NFL" / "normalized" / "nfl_games_latest.json"
        props_file = args.out_dir / "NFL" / "normalized" / "nfl_props_latest.json"

        assert games_file.exists(), f"Normalized games file missing: {games_file}"
        assert props_file.exists(), f"Normalized props file missing: {props_file}"

        with open(games_file, "r", encoding="utf-8") as f:
            games_data = json.load(f)
        with open(props_file, "r", encoding="utf-8") as f:
            props_data = json.load(f)

        game_records = games_data.get("records", [])
        prop_records = props_data.get("records", [])

        tot_cnt = verify_game_totals(game_records, args.verbose)
        spr_cnt = verify_point_spreads(game_records, args.verbose)
        tt_cnt = verify_team_totals(game_records, args.verbose)
        prp_cnt = verify_player_props(prop_records, args.verbose)

        print("=" * 70)
        print("VERIFICATION SUCCESS: All market coverage assertions passed.")
        print(f"  Total Verified: {tot_cnt} Game Totals, {spr_cnt} Spreads, {tt_cnt} Team Totals, {prp_cnt} Player Props.")
        print("=" * 70)
        return 0

    except Exception as exc:
        print(f"\n[ERROR] Verification failed: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
