import pytest
from outlier_scrapers.portfolio import PortfolioPolicy


@pytest.fixture(autouse=True)
def _shadow_portfolio_policy(request, monkeypatch):
    """Force shadow mode in tests so the 14-day enforce window check is skipped.

    Tests that explicitly test enforce mode should use the ``uses_enforce_mode``
    marker to opt out::

        @pytest.mark.uses_enforce_mode
        def test_enforce_something(tmp_path): ...
    """
    if "uses_enforce_mode" in {m.name for m in request.node.iter_markers()}:
        return
    monkeypatch.setattr(
        "outlier_scrapers.portfolio.load_portfolio_policy",
        lambda path=None: PortfolioPolicy(mode="shadow"),
    )


@pytest.fixture(autouse=True)
def _no_live_nfl_external_metrics(monkeypatch):
    """Keep NFL pipeline tests offline: no nflverse downloads, no weather forecasts."""
    monkeypatch.setattr(
        "outlier_nfl.pipeline.load_external_metrics", lambda *args, **kwargs: []
    )
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", lambda *args, **kwargs: {})
