import hashlib
import subprocess
from pathlib import Path

import pytest
from outlier_scrapers.portfolio import PortfolioPolicy

# Resolved at import, before any test monkeypatches paths.PROJECT_ROOT or chdirs.
_REPO_ROOT = Path(__file__).resolve().parents[1]
# Committed artifacts that production writers target by default.
_GUARDED_DIRS = ("calibration", "reports")


def _tracked_guarded_files() -> tuple[Path, ...]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", *_GUARDED_DIRS],
            cwd=_REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
        return tuple(_REPO_ROOT / n for n in names)
    except (OSError, subprocess.CalledProcessError):
        # No git (e.g. sdist): fall back to every file currently under the guarded dirs.
        return tuple(
            p
            for d in _GUARDED_DIRS
            if (_REPO_ROOT / d).is_dir()
            for p in (_REPO_ROOT / d).rglob("*")
            if p.is_file()
        )


_TRACKED_GUARDED_FILES = _tracked_guarded_files()
# path -> ((mtime_ns, size), sha1); a file is re-hashed only when its stat changes.
_DIGEST_CACHE: dict[Path, tuple[tuple[int, int], str]] = {}


def _guarded_digests() -> dict[Path, str | None]:
    digests: dict[Path, str | None] = {}
    for path in _TRACKED_GUARDED_FILES:
        try:
            st = path.stat()
        except FileNotFoundError:
            digests[path] = None
            continue
        key = (st.st_mtime_ns, st.st_size)
        cached = _DIGEST_CACHE.get(path)
        if cached is None or cached[0] != key:
            cached = (key, hashlib.sha1(path.read_bytes()).hexdigest())
            _DIGEST_CACHE[path] = cached
        digests[path] = cached[1]
    return digests


@pytest.fixture(autouse=True)
def _no_writes_to_real_tracked_artifacts():
    """Fail any test that modifies or deletes a tracked file under real ``calibration/`` or ``reports/``.

    Production defaults point at committed artifacts: ``daily_job.main()`` refits
    ``probability_blend.DEFAULT_WEIGHTS_PATH`` (``calibration/blend_weights.json``)
    from ``feedback.DEFAULT_DB_PATH``, and ``NflPipeline.run()`` publishes to the
    cwd-relative ``reports/NFL`` when ``reports_dir`` is omitted. A test that
    reaches either without a ``tmp_path`` override silently rewrites tracked files
    on every run. Content hashes are compared, so a no-op touch passes.
    This only fails -- it never restores, because a live daily or NFL run may
    legitimately update these files while pytest is running.
    """
    before = _guarded_digests()
    yield
    after = _guarded_digests()
    changed = sorted(
        path.relative_to(_REPO_ROOT).as_posix() for path in before if before[path] != after[path]
    )
    if changed:
        pytest.fail(
            f"test modified tracked files under real {'/ or '.join(_GUARDED_DIRS)}/: "
            f"{changed}. Point the writer at tmp_path (monkeypatch the module's "
            "DEFAULT_*_PATH / paths.PROJECT_ROOT, or pass an explicit path such as "
            "reports_dir=); then `git checkout -- <file>` to undo the damage.",
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
    from outlier_nfl.external import ExternalLoad

    monkeypatch.setattr(
        "outlier_nfl.pipeline.load_external_metrics", lambda *args, **kwargs: ExternalLoad()
    )
    monkeypatch.setattr("outlier_nfl.pipeline.load_slate_weather", lambda *args, **kwargs: {})
    monkeypatch.setattr("outlier_nfl.pipeline.load_usage", lambda *args, **kwargs: {})
