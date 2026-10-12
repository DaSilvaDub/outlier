"""Read-only offline archive audit, exact settlement, and shadow floor calibration."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outlier_nfl.floor_calibration import run_offline_build
from outlier_nfl.run_context import parse_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, required=True, help="Immutable NFL runs directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Research output directory outside runs")
    parser.add_argument("--outcomes", type=Path, help="Explicit verified-final outcomes JSON; no live fetch")
    parser.add_argument("--as-of", type=parse_utc, default=None, help="Aware fit cutoff (default current UTC)")
    args = parser.parse_args(argv)
    artifact = run_offline_build(args.runs_dir, args.output_dir, args.outcomes, as_of=args.as_of or datetime.now(UTC))
    print(f"{artifact['status']} — SHADOW_ONLY; approved_for_betting=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
