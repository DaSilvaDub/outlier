import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Calibration lock. This must run before any outlier_scrapers module is imported:
# the DEFAULT_*_PATH constants are computed from OUTLIER_CALIBRATION_DIR at import.
# Tests get a temporary calibration dir, and every write under the real
# ``calibration/`` of this checkout or of the main checkout is refused *before* it
# happens (outlier_scrapers.calibration_store), instead of detected afterwards.
_REPO_ROOT = Path(__file__).resolve().parents[1]
# conftest can be imported twice (as ``conftest`` and ``tests.conftest``); the second
# import must reuse the first one's directory instead of replacing it.
_TEST_CALIBRATION_DIR = Path(
    os.environ.get("_OUTLIER_TEST_CALIBRATION_DIR")
    or tempfile.mkdtemp(prefix="outlier-test-calibration-")
)
os.environ["_OUTLIER_TEST_CALIBRATION_DIR"] = str(_TEST_CALIBRATION_DIR)


def _main_checkout_root(repo_root: Path) -> Path:
    dot_git = repo_root / ".git"
    if dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (repo_root / gitdir).resolve()
            if gitdir.parent.name == "worktrees":
                return gitdir.parent.parent.parent
    return repo_root


REAL_CALIBRATION_DIRS = tuple(
    dict.fromkeys(
        [
            (_REPO_ROOT / "calibration").resolve(),
            (_main_checkout_root(_REPO_ROOT) / "calibration").resolve(),
        ]
    )
)
os.environ["OUTLIER_CALIBRATION_DIR"] = str(_TEST_CALIBRATION_DIR)
os.environ["OUTLIER_PROTECTED_CALIBRATION_DIRS"] = os.pathsep.join(
    str(p) for p in REAL_CALIBRATION_DIRS
)

import pytest  # noqa: E402
from outlier_scrapers import paths  # noqa: E402
from outlier_scrapers.portfolio import PortfolioPolicy  # noqa: E402
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
def _clean_test_calibration_dir():
    """The shared temporary calibration dir starts empty for every test."""
    yield
    for child in _TEST_CALIBRATION_DIR.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


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


# The resolved production ledger (paths.FEEDBACK_DB_PATH, which honours
# OUTLIER_FEEDBACK_DB), the local-data ledger, the canonical checkout's
# pre-migration ledger, and this checkout's own copy. No test may open any of them.
REAL_LEDGER_PATHS = tuple(
    dict.fromkeys(
        [
            paths.FEEDBACK_DB_PATH,
            *(
                [paths.local_calibration_dir() / paths.FEEDBACK_DB_NAME]
                if paths.local_calibration_dir() is not None
                else []
            ),
            paths.main_checkout_root() / "calibration" / "feedback.sqlite3",
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
