from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Run in a subprocess: database.py builds its engine at import time, so the
# fallback can only be exercised by a fresh interpreter, and a second process
# is also the only honest way to prove the fallback is shared rather than
# private to one connection.
_PROBE = """
import json, sys
from outlier_scrapers.database import ExtractionPayload, SessionLocal, engine

action = sys.argv[1]
if action == "write":
    with SessionLocal.begin() as db:
        db.add(ExtractionPayload(league="MLB", data_type="props", date="latest", payload={"n": 7}))
with SessionLocal() as db:
    found = db.query(ExtractionPayload).filter_by(league="MLB").count()
print(json.dumps({"url": str(engine.url), "rows": found}))
"""


def _run(action: str, tmp_path: Path) -> dict:
    import json
    import os

    env = dict(os.environ)
    # An address nothing listens on, so the Postgres probe always fails.
    env["OUTLIER_DATABASE_URL"] = "postgresql://postgres:postgres@127.0.0.1:1/outlier"
    env["OUTLIER_SQLITE_FALLBACK_PATH"] = str(tmp_path / "fallback.sqlite3")
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, action],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_sqlite_fallback_is_file_backed_and_shared_across_processes(tmp_path):
    written = _run("write", tmp_path)
    assert ":memory:" not in written["url"], (
        "an in-memory fallback gives every connection its own empty database, "
        "so concurrent pipeline workers silently lose each other's writes"
    )
    assert written["rows"] == 1

    reread = _run("read", tmp_path)
    assert reread["rows"] == 1, "a second process could not see the first process's write"
