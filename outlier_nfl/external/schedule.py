"""Schedule adapter stub.

Provides a ``fetch`` function matching the signature used by ``load_external_metrics``.
Returns an empty dict placeholder so the pipeline can run without requiring real
data sources. In production this would fetch schedule metadata (game times, venues,
lines at open, weather conditions) from an external provider (e.g. nflverse schedule).
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
