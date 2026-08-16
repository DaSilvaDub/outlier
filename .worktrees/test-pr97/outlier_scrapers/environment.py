import os
from . import paths


def load_environment():
    # Read ``paths.PROJECT_ROOT`` at call time (not an import-time copy) so tests
    # that monkeypatch ``paths.PROJECT_ROOT`` — as the rest of the suite does —
    # correctly redirect .env loading instead of leaking the real project .env.
    env_path = paths.PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
