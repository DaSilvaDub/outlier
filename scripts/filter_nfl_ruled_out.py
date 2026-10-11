"""CLI: strip ruled-out players from nfl_only.csv and companion pack artifacts.

Uses ``outlier_nfl.injuries`` (ESPN public injuries + optional nflverse tape).

Companions (optional):
  --best-bets-json  filter picks + regenerate .md via render_best_bets_markdown
  --alt-floors-csv  filter player_name rows (same CSV stripper)
  --matchup-json    strip prop_signals for ruled-out players
  --game-script-md  strip prop-signal table rows / calibrated bullets (repeatable)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from outlier_nfl.injuries import (  # noqa: E402
    filter_csv_file,
    filter_game_script_markdown,
    filter_matchup_scripts_payload,
    ruled_out_map_from_entries,
    write_filtered_best_bets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Input nfl_only.csv path")
    parser.add_argument(
        "--out",
        default=None,
        help="Output CSV (default: overwrite --csv)",
    )
    parser.add_argument(
        "--nfl-dir",
        default=str(REPO_ROOT / "data" / "NFL"),
        help="NFL data dir (for tape inactive merge)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        help="Optional JSON path to write injury/removal audit",
    )
    parser.add_argument(
        "--no-espn",
        action="store_true",
        help="Skip ESPN fetch; tape only",
    )
    parser.add_argument(
        "--no-tape",
        action="store_true",
        help="Skip tape inactive merge",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Optional path to write filter summary JSON",
    )
    parser.add_argument(
        "--best-bets-json",
        default=None,
        help="Optional nfl_best_bets_YYYY-MM-DD.json to strip + re-render md",
    )
    parser.add_argument(
        "--best-bets-md",
        default=None,
        help="Optional markdown output path (default: sibling .md of JSON)",
    )
    parser.add_argument(
        "--alt-floors-csv",
        default=None,
        help="Optional nfl_alt_floors CSV (uses player_name column)",
    )
    parser.add_argument(
        "--matchup-json",
        default=None,
        help="Optional nfl_matchup_scripts_YYYY-MM-DD.json",
    )
    parser.add_argument(
        "--game-script-md",
        action="append",
        default=[],
        help="Optional game script markdown path (repeatable)",
    )
    args = parser.parse_args()

    summary = filter_csv_file(
        Path(args.csv),
        out_path=Path(args.out) if args.out else None,
        nfl_dir=Path(args.nfl_dir),
        use_espn=not args.no_espn,
        use_tape=not args.no_tape,
        cache_path=Path(args.cache) if args.cache else None,
    )

    print(
        f"injury filter: {summary['before']} -> {summary['after']} "
        f"(removed {summary['removed']}; "
        f"ruled_out_universe={summary['ruled_out_players']}; "
        f"sources={summary['meta'].get('sources')})"
    )
    for rem in summary["removals"]:
        print(
            f"  - removed {rem.get('player')} ({rem.get('team')}) "
            f"{rem.get('market')} status={rem.get('status')} "
            f"source={rem.get('source')}"
        )
    if summary["meta"].get("espn_error"):
        print(f"WARNING: ESPN fetch failed: {summary['meta']['espn_error']}", file=sys.stderr)

    companions: dict = {}
    ruled = None
    need_companions = (
        args.best_bets_json
        or args.alt_floors_csv
        or args.matchup_json
        or args.game_script_md
    )
    if need_companions:
        if args.cache and Path(args.cache).is_file():
            cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))
            ruled = ruled_out_map_from_entries(cache.get("ruled_out") or [])
        else:
            # Fall back: rebuild from ESPN/tape (same flags as CSV pass).
            from outlier_nfl.injuries import load_injury_status, ruled_out_name_keys

            injury_map, _meta = load_injury_status(
                Path(args.nfl_dir),
                use_espn=not args.no_espn,
                use_tape=not args.no_tape,
            )
            ruled = ruled_out_name_keys(injury_map)

    if args.best_bets_json and ruled is not None:
        bb = write_filtered_best_bets(
            Path(args.best_bets_json),
            ruled,
            md_path=Path(args.best_bets_md) if args.best_bets_md else None,
        )
        companions["best_bets"] = bb
        print(
            f"best_bets filter: {bb['before']} -> {bb['after']} "
            f"(removed {bb['removed']}) -> {bb['md']}"
        )
        for rem in bb["removals"]:
            print(
                f"  - removed {rem.get('player')} ({rem.get('team')}) "
                f"{rem.get('market')} verdict={rem.get('verdict')} "
                f"status={rem.get('status')} source={rem.get('source')}"
            )

    if args.alt_floors_csv and Path(args.alt_floors_csv).is_file() and ruled is not None:
        import csv
        from outlier_nfl.injuries import filter_pack_rows

        alt_path = Path(args.alt_floors_csv)
        with open(alt_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        kept, removed = filter_pack_rows(rows, ruled)
        with open(alt_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(kept)
        alt_summary = {
            "before": len(rows),
            "after": len(kept),
            "removed": len(removed),
            "csv": str(alt_path),
            "removals": [
                {
                    "player": r.get("player") or r.get("player_name"),
                    "team": r.get("team"),
                    "market": r.get("market"),
                    "status": r.get("_ruled_out_status"),
                    "source": r.get("_ruled_out_source"),
                }
                for r in removed
            ],
        }
        companions["alt_floors"] = alt_summary
        print(
            f"alt_floors filter: {alt_summary['before']} -> {alt_summary['after']} "
            f"(removed {alt_summary['removed']})"
        )
        for rem in alt_summary["removals"]:
            print(
                f"  - removed {rem.get('player')} ({rem.get('team')}) "
                f"{rem.get('market')} status={rem.get('status')}"
            )

    if args.matchup_json and Path(args.matchup_json).is_file() and ruled is not None:
        path = Path(args.matchup_json)
        payload = json.loads(path.read_text(encoding="utf-8"))
        filtered, removed = filter_matchup_scripts_payload(payload, ruled)
        path.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
        companions["matchup"] = {
            "path": str(path),
            "removed": len(removed),
            "removals": [
                {
                    "player": r.get("player_name"),
                    "team": r.get("team"),
                    "market": r.get("market"),
                }
                for r in removed
            ],
        }
        print(f"matchup_scripts filter: removed {len(removed)} prop_signals")
        for rem in companions["matchup"]["removals"]:
            print(f"  - removed {rem.get('player')} ({rem.get('team')}) {rem.get('market')}")

    if args.game_script_md and ruled is not None:
        gs_summaries = []
        for gs in args.game_script_md:
            path = Path(gs)
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            filtered, removed = filter_game_script_markdown(original, ruled)
            path.write_text(filtered, encoding="utf-8")
            gs_summaries.append({"path": str(path), "removed": len(removed), "lines": removed})
            print(f"game_script filter: {path.name} removed {len(removed)} lines")
            for line in removed:
                print(f"  - {line[:120]}")
        companions["game_scripts"] = gs_summaries

    if args.summary_json:
        summary["companions"] = companions
        Path(args.summary_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
