import os
import sqlite3
from urllib.parse import unquote, urlsplit

import pytest
from outlier_scrapers import paths
from outlier_scrapers.portfolio import PortfolioPolicy

# The resolved production ledger (paths.FEEDBACK_DB_PATH, which honours
# OUTLIER_FEEDBACK_DB), the canonical checkout's ledger, and this checkout's own
# copy. No test may open any of them.
REAL_LEDGER_PATHS = tuple(
    dict.fromkeys(
        [
            paths.FEEDBACK_DB_PATH,
            paths.CANONICAL_CALIBRATION_DIR / "feedback.sqlite3",
            paths.PROJECT_ROOT / "calibration" / "feedback.sqlite3",
        ]
    )
)

# Module attributes that default to a real ledger, read at call time.
_LEDGER_DEFAULT_ATTRS = (
    ("outlier_scrapers.paths", "FEEDBACK_DB_PATH"),
    ("outlier_scrapers.feedback_db", "DEFAULT_DB_PATH"),
    ("outlier_scrapers.feedback", "DEFAULT_DB_PATH"),
    ("outlier_scrapers.so_eval", "DEFAULT_DB"),
    ("outlier_scrapers.so_promotion", "DEFAULT_DB"),
)

# Module attributes that default to the real feedback report directory.
_REPORT_DIR_DEFAULT_ATTRS = (
    ("outlier_scrapers.paths", "FEEDBACK_REPORT_DIR"),
    ("outlier_scrapers.feedback_reporting", "DEFAULT_REPORT_DIR"),
    ("outlier_scrapers.feedback", "DEFAULT_REPORT_DIR"),
)


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(path))


_REAL_LEDGER_NORMS = frozenset(_norm(p) for p in REAL_LEDGER_PATHS)


def _connect_target(database, uri: bool) -> str | None:
    """Return the filesystem path a sqlite3.connect() call would open, if any."""
    if not isinstance(database, (str, bytes, os.PathLike)):
        return None
    target = os.fsdecode(database)
    if uri or target.startswith("file:"):
        if not target.startswith("file:"):
            return None
        raw = unquote(urlsplit(target).path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]  # file:///C:/... on Windows
        target = raw
    if not target or target == ":memory:":
        return None
    return target


@pytest.fixture(autouse=True)
def _no_real_feedback_ledger(request, monkeypatch, tmp_path):
    """Point ledger defaults at tmp_path and refuse any connection to the real ledger.

    ``paths.FEEDBACK_DB_PATH`` resolves to the canonical checkout's production ledger
    on local runs, so without this every worktree's tests would read (and could
    write) live decisions and settlements. Function defaults bound at import time
    (``db_path: Path = DEFAULT_DB_PATH``) are not reached by the attribute redirect,
    which is why ``sqlite3.connect`` also gets a tripwire.

    Tests that genuinely need the real ledger opt out with::

        @pytest.mark.uses_real_ledger
    """
    if "uses_real_ledger" in {m.name for m in request.node.iter_markers()}:
        return
    monkeypatch.delenv("OUTLIER_FEEDBACK_DB", raising=False)
    monkeypatch.delenv("OUTLIER_FEEDBACK_REPORT_DIR", raising=False)
    fake_ledger = tmp_path / "ledger" / "feedback.sqlite3"
    for module, attr in _LEDGER_DEFAULT_ATTRS:
        monkeypatch.setattr(f"{module}.{attr}", fake_ledger)
    fake_report_dir = tmp_path / "ledger" / "reports"
    for module, attr in _REPORT_DIR_DEFAULT_ATTRS:
        monkeypatch.setattr(f"{module}.{attr}", fake_report_dir)

    real_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        target = _connect_target(database, bool(kwargs.get("uri")))
        if target is not None and _norm(target) in _REAL_LEDGER_NORMS:
            raise RuntimeError(
                f"Test tried to open the real feedback ledger at {target}; pass a tmp_path "
                "db_path or mark the test with @pytest.mark.uses_real_ledger."
            )
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)


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
    from outlier_nfl.external import ExternalLoad

    monkeypatch.setattr(
        "outlier_nfl.pipeline.load_external_metrics", lambda *args, **kwargs: ExternalLoad()
    )
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", lambda *args, **kwargs: {})
    monkeypatch.setattr("outlier_nfl.pipeline.load_usage", lambda *args, **kwargs: {})
