"""Play-by-play (PBP) adapter stub.

Provides a ``fetch`` function matching the signature used by ``load_external_metrics``.
Returns an empty dict placeholder so the pipeline can run without requiring real
data sources. In production this would aggregate EPA, WPA, and drive-level metrics
from a play-by-play data source (e.g. nflfastR / nflverse).
"""


def fetch(client, season: int, through_week: int = 22):
    """Return a stubbed empty record set.

    Args:
        client: a ``Client`` instance (unused in the stub).
        season: NFL season year.
        through_week: week cutoff (ignored).
    Returns:
        dict: ``{"records": []}`` to satisfy the caller.
    """
    return {"records": []}
