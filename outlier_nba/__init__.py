"""NBA master pipeline (design of record: docs/plans/2026-10-20-nba-master-pipeline.md).

Market-anchored: nothing here moves a stake. Phases 0-1 only capture and settle
SHADOW rows (units 0, never actionable) so the season's market data accumulates.
This package never imports ``outlier_nfl``; the run harness is re-implemented.
"""
