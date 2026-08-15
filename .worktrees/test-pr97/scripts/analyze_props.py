"""Summarize normalized props for market/team reconciliation.

Read-only: reads data/<LEAGUE>/normalized/*_props_latest.json and prints a
scope/market/team summary. No auth, no network, no writes.

Run from the project root:  python scripts/analyze_props.py
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

# scripts/ is one level below the project root.
DATA = Path(__file__).resolve().parent.parent / "data"


def load(league: str) -> dict:
    p = DATA / league / "normalized" / f"{league.lower()}_props_latest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def summarize(league: str) -> None:
    recs = load(league)["records"]
    full = [r for r in recs if (r.get("sport_context") or {}).get("scope") == "full_game"]
    scopes = collections.Counter((r.get("sport_context") or {}).get("scope") for r in recs)

    true_gaps = collections.Counter()
    for r in full:
        if r.get("market") is None:
            true_gaps[((r.get("sport_context") or {}).get("proposition"), r.get("market_raw"))] += 1

    team_none = collections.Counter(str(r.get("team_raw")) for r in recs if r.get("team") is None)
    opp_none = collections.Counter(str(r.get("opponent_raw")) for r in recs if r.get("opponent") is None)
    full_mapped = sum(1 for r in full if r.get("market"))

    print("=" * 64)
    print(f"{league}: total={len(recs)}  full_game={len(full)} "
          f"(mapped={full_mapped}, gap={len(full) - full_mapped})")
    print(f"  scope breakdown: {dict(scopes)}")
    print(f"  TRUE full-game gaps (distinct={len(true_gaps)}, rows={sum(true_gaps.values())}):")
    for (prop, mr), c in sorted(true_gaps.items(), key=lambda x: -x[1]):
        print(f"     prop={prop!r:30} market_raw={mr!r:32} ({c})")
    print(f"  team=None rows={sum(team_none.values())} distinct_raw={dict(team_none)}")
    print(f"  opp=None  rows={sum(opp_none.values())} distinct_raw={dict(opp_none)}")


if __name__ == "__main__":
    for lg in ("MLB", "WNBA"):
        summarize(lg)
