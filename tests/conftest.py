import os
from pathlib import Path

import pytest
from outlier_scrapers.portfolio import PortfolioPolicy

# Resolved at import, before any test monkeypatches paths.PROJECT_ROOT.
_REAL_PACKS_DIR = Path(__file__).resolve().parents[1] / "packs"


def _pack_entries() -> set[str]:
    try:
        return {entry.name for entry in os.scandir(_REAL_PACKS_DIR)}
    except FileNotFoundError:
        return set()


@pytest.fixture(autouse=True)
def _no_writes_to_real_packs_dir():
    """Fail any test that creates an entry under the repo's real ``packs/``.

    A test that ran ``pack.main()`` without redirecting ``paths.PROJECT_ROOT``
    left a fixture pack at ``packs/2099-07-07``; organize_today_run2.py then
    exported that fake slate instead of today's. Tests must write packs under
    ``tmp_path``. This only fails -- it never deletes, because a real daily run
    may legitimately create today's pack while pytest is running.
    """
    before = _pack_entries()
    yield
    leaked = sorted(_pack_entries() - before)
    if leaked:
        pytest.fail(
            f"test created {leaked} under the real {_REAL_PACKS_DIR}; "
            "write packs under tmp_path (monkeypatch paths.PROJECT_ROOT). "
            "Remove the stray dir(s) by hand.",
            pytrace=False,
        )


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
    monkeypatch.setattr("outlier_nfl.pipeline.load_usage", lambda *args, **kwargs: {})
