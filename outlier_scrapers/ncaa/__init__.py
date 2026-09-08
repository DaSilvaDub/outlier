"""Standalone NCAA college football analytics pipeline.

Three independent models, deliberately not one score:

- ``safety``  -- who is least likely to lose?
- ``value``   -- is the price worth paying?
- ``totals``  -- is the market's number wrong?

Conflating the first two is the most common way a betting model goes wrong: a
94% favorite priced at 97% is an excellent prediction and a poor wager, and a
single blended "rating" cannot express that. They are separate objects here and
are joined only at tier admission.

Stage order (see ``pipeline``)::

    raw inputs -> roster/injury -> efficiency ratings -> matchup features
      -> market data -> win probability -> calibration -> tier admission
      -> parlay optimizer -> totals model -> reporting / backtesting

Nothing in this package makes network calls or invokes a reasoning provider;
``sources`` defines adapter protocols and implementations live outside it.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# Submodules are imported explicitly by consumers rather than re-exported here:
# ``report`` imports ``pipeline`` which imports every model, so eagerly pulling
# the package graph into ``__init__`` would make importing one primitive cost
# the whole pipeline.
__all__ = ["SCHEMA_VERSION"]
