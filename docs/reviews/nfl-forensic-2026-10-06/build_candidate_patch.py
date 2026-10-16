"""Build a reviewable candidate diff; never modify the audited product sources."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path
import shutil


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("candidate.patch"))
    parser.add_argument("--overlay-root", type=Path, help="Optional isolated copy for verification")
    args = parser.parse_args()
    edits = {
        "outlier_nfl/best_bets.py": [
            ("    if edge <= 0:\n", "    if edge <= 0 or ev <= 0:\n"),
            ('pillar.notes.append("no edge at the best available price")',
             'pillar.notes.append("no positive expected return at the available price")'),
        ],
        "outlier_nfl/projection.py": [
            ('    if rate <= 0:\n',
             '    if threshold < 0:\n        return 1.0\n    if rate <= 0:\n'),
            ('            + _f(row, "passing_tds")\n', ""),
            ('            model_p = 1.0 - p_over\n        else:\n            return None\n        return ProjectionResult(',
             '            model_p = 1.0 - _poisson_sf_gt(mean, math.ceil(float(line)) - 1)\n'
             '        else:\n            return None\n        return ProjectionResult('),
            ('        if record.get("model_p") is not None and record.get("model_p_source") == (\n'
             '            MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE\n        ):',
             '        if record.get("model_p") is not None and record.get("model_p_source") in {\n'
             '            MODEL_P_SOURCE_PROJECTION_NFLVERSE_RATE,\n'
             '            MODEL_P_SOURCE_PROJECTION_NFLVERSE_GAUSSIAN,\n'
             '            MODEL_P_SOURCE_PROJECTION_NFLVERSE_POISSON,\n        }:'),
            ('        record.pop("model_p_n_games", None)\n',
             '        record.pop("model_p_n_games", None)\n        record.pop("model_p_method", None)\n'),
        ],
        "outlier_nfl/config.py": [
            ('    "LONGESTCOMPLETION": PROP_LONG_PASS,\n',
             '    "LONGESTCOMPLETION": PROP_LONG_PASS,\n    "LONGESTPASSINGCOMPLETION": PROP_LONG_PASS,\n'),
            ('    "PASSCOMPLETIONS": PROP_PASS_COMP,\n',
             '    "PASSCOMPLETIONS": PROP_PASS_COMP,\n    "PASSINGCOMPLETIONS": PROP_PASS_COMP,\n'),
            ('    "PASSINGINTERCEPTIONS": PROP_INT,\n',
             '    "PASSINGINTERCEPTIONS": PROP_INT,\n    "INTERCEPTIONSTHROWN": PROP_INT,\n'),
            ('    "FIELDGOALSMADE": PROP_FGM,\n',
             '    "FIELDGOALSMADE": PROP_FGM,\n    "MADEFIELDGOALS": PROP_FGM,\n'),
        ],
        "outlier_nfl/boxscore.py": [
            ('    if norm in {"ANYTIMETD", "FIRSTTD"}:\n', '    if norm == "ANYTIMETD":\n'),
            ('    side = str(position or "").strip().upper()\n',
             '    if not math.isfinite(actual) or not math.isfinite(line):\n'
             '        raise BoxScoreError("Cannot grade nonfinite actual or line")\n'
             '    side = str(position or "").strip().upper()\n'),
        ],
        "outlier_nfl/boxscore_nflverse.py": [
            ('from outlier_nfl.boxscore import BoxScoreError, NflBoxScoreEvent, _number, _token\n',
             'from outlier_nfl.boxscore import BoxScoreError, NflBoxScoreEvent, _number, _token\n'
             'from outlier_nfl.utils import nfl_season_for_date\n'),
            ('    season_year = season or event_date.year\n'
             '    # NFL season year equals the calendar year of Week 1 (Sep); Sep/Oct/Nov/Dec use that year.\n',
             '    season_year = season if season is not None else nfl_season_for_date(event_date.isoformat())\n'
             '    if season_year is None:\n        raise BoxScoreError("Invalid NFL slate date")\n'),
        ],
        "outlier_nfl/settle.py": [
            ('                season=args.season or slate.year,\n', '                season=args.season,\n'),
        ],
    }
    if args.overlay_root:
        if args.overlay_root.resolve() == args.repo_root.resolve():
            raise ValueError("Overlay must be separate from the audited source checkout")
        args.overlay_root.mkdir(parents=True, exist_ok=True)
        for package in ("outlier_nfl", "outlier_scrapers"):
            shutil.copytree(args.repo_root / package, args.overlay_root / package,
                            ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    patches = []
    for filename, replacements in edits.items():
        old = (args.repo_root / filename).read_text(encoding="utf-8")
        new = old
        for before, after in replacements:
            if new.count(before) != 1:
                raise ValueError(f"Expected exactly one source match in {filename}: {before!r}")
            new = new.replace(before, after, 1)
        patches.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           fromfile=f"a/{filename}", tofile=f"b/{filename}"))
        if args.overlay_root:
            (args.overlay_root / filename).write_text(new, encoding="utf-8")
    args.output.write_text("".join(patches), encoding="utf-8")
    print(f"Candidate diff: {args.output}; {len(edits)} product files, sources left unchanged")


if __name__ == "__main__":
    main()
