"""NBA master pipeline (design of record: docs/plans/2026-10-20-nba-master-pipeline.md).

Published rows stay SHADOW (units 0, never actionable). The slate report sizes units.
This package never imports ``outlier_nfl``; the run harness is re-implemented.
"""
