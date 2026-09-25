"""NGS (Next Gen Stats) adapter stub.

Provides a ``fetch`` function matching the signature used by ``load_external_metrics``.
It returns an empty dict placeholder so the pipeline can run without requiring real
data sources. In production this would query the actual NGS API.
"""

def fetch(client, season: int, kind: str, through_week: int = 22):
    """Return a stubbed empty record set.

    Args:
        client: a ``Client`` instance (unused in the stub).
        season: NFL season year.
        kind: one of ``"passing"``, ``"rushing"``, ``"receiving"``.
        through_week: week cutoff (ignored).
    Returns:
        dict: ``{"records": []}`` to satisfy the caller.
    """
    return {"records": []}
