"""Refresh a versioned, source-attested NBA registry from ESPN (no AI calls)."""
import argparse
import sys
from pathlib import Path

from nba_roster_registry import DEFAULT_PATH, RegistryError, current_date, refresh


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    try:
        registry = refresh(args.out, args.date or current_date())
    except (RegistryError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Roster refresh FAILED: {exc}", file=sys.stderr)
        return 2
    players = [p for t in registry["teams"].values() for p in t["roster"]]
    print(f"Roster refreshed: teams={len(registry['teams'])} players={len(players)} out={sum(p['status'] in {'OUT', 'INACTIVE', 'SUSPENDED', 'IR'} for p in players)} as_of={registry['as_of_utc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
