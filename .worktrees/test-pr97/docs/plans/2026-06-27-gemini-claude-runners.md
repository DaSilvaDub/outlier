# Gemini (Prompt B) & Claude (Prompts D/E) Runner Modules Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add three automated reasoning/research runners — Gemini Prompt B (wide-scan, web), Claude Prompt D (pack-only reasoning), Claude Prompt E (synthesis) — mirroring the existing OpenAI Prompt A runner, with the same hash-based idempotency, atomic writes, and CLI surface.

**Architecture:** Extract the truly-shared scaffolding (request-hash, atomic front-matter write, candidates validation, run-decision helpers) into a new `outlier_scrapers/runner_common.py`. Each runner is its own small module that reads its prompt + inputs, computes a request hash over `(model, effort/config, role_block, prompt, input hashes)`, skips the paid API call when an identical request already produced output, and writes `<provider>_<letter>.md` with YAML front matter. Model IDs come from the already-created `outlier_scrapers/models.py`. The existing `reasoning.py` (Prompt A) is left untouched; an optional final phase migrates it onto the shared module using its own test suite as the safety net.

**Tech Stack:** Python 3, `anthropic` SDK (Claude Opus 4.8, adaptive thinking + effort, streaming), `google-genai` SDK (Gemini 3.1 Pro Preview, Google Search grounding), `pytest` with mocked SDK clients (no live API calls in tests), `openai` (existing, untouched).

---

## Reference Material (read before starting)

- **Template to mirror:** `outlier_scrapers/reasoning.py` — copy its control-flow shape (`validate_pack_dir` → hash → skip/force/refresh → call → atomic write → front matter).
- **Test template to mirror:** `tests/test_reasoning.py` — copy its fixture + mock-client pattern exactly (mock the SDK *constructor*, assert call kwargs, assert output file + `request_sha256`, assert idempotency/force/refresh/failure-leaves-no-partial).
- **Shared data:** `outlier_scrapers/pack.py` provides `CANDIDATES_HEADER` and `ROLE_BLOCK`. Pass the **full** `pack.ROLE_BLOCK` as system context to every runner — it self-describes which rules apply to reasoning passes (A, D) vs research passes (B, C).
- **Model IDs:** `outlier_scrapers/models.py` — `GEMINI_MODEL = "gemini-3.1-pro-preview"`, `CLAUDE_MODEL = "claude-opus-4-8"`.
- **Prompt source text:** `AI-research-desk-runbook.md` §6 contains the exact Prompt B, D, E text inline. Copy it verbatim into the new prompt files; do not paraphrase.
- **Claude SDK specifics:** Opus 4.8 uses `thinking={"type": "adaptive"}` + `output_config={"effort": "high"}`. **Never** pass `budget_tokens`, `temperature`, `top_p` (all 400 on 4.8). Stream for large `max_tokens`. Check `stop_reason == "refusal"` before reading `content`.

---

## Per-runner spec summary

| Runner | Module | Prompt file | Input(s) | Web? | Output |
|---|---|---|---|---|---|
| Prompt B (Gemini wide-scan) | `gemini_research.py` | `prompts/B.md` | `briefing.md` | Yes (Google Search grounding) | `gemini_b.md` |
| Prompt D (Claude reasoning) | `claude_reasoning.py` | `prompts/D.md` | `candidates.csv` | No (pack-only) | `claude_d.md` |
| Prompt E (Claude synthesis) | `claude_synthesis.py` | `prompts/E.md` | `briefing.md` + `chatgpt_a.md` + `gemini_b.md` + `claude_d.md` (+ optional `chatgpt_c.md`) | No | `claude_e.md` |

---

## Phase 0 — Dependencies & shared scaffolding

### Task 1: Add SDK dependencies

**Files:**
- Modify: `pyproject.toml` (the `dependencies` array, currently `["openai>=2.43,<3"]`)

**Step 1: Add the two SDKs**

Edit the `dependencies` array to:

```toml
dependencies = [
    "openai>=2.43,<3",
    "anthropic",
    "google-genai",
]
```

**Step 2: Install and capture resolved versions**

Run: `pip install -e .`
Then: `pip show anthropic google-genai` and read each `Version:`.

**Step 3: Pin to the resolved versions**

Replace the unpinned entries with floor+ceiling pins using the versions you just read, e.g. (substitute the real numbers):

```toml
    "anthropic>=0.69,<1",
    "google-genai>=1.0,<2",
```

**Step 4: Verify imports work**

Run:
```bash
python -c "import anthropic, google.genai; from google.genai import types; print('ok')"
```
Expected: `ok`

**Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add anthropic and google-genai runner dependencies"
```

---

### Task 2: Create `runner_common.py` shared helpers (TDD)

**Files:**
- Create: `outlier_scrapers/runner_common.py`
- Test: `tests/test_runner_common.py`

**Step 1: Write the failing tests**

```python
import csv
import hashlib

import pytest

from outlier_scrapers import pack, runner_common


def test_sha256_bytes_matches_hashlib():
    data = b"hello"
    assert runner_common.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_compute_request_hash_is_order_independent():
    a = runner_common.compute_request_hash({"x": 1, "y": 2})
    b = runner_common.compute_request_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64


def test_extract_yaml_request_hash_reads_front_matter():
    content = "---\nmodel: m\nrequest_sha256: abc123\n---\n\nbody"
    assert runner_common.extract_yaml_request_hash(content) == "abc123"


def test_extract_yaml_request_hash_none_when_no_front_matter():
    assert runner_common.extract_yaml_request_hash("no front matter") is None


def test_validate_candidates_returns_bytes_and_hash(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(pack.CANDIDATES_HEADER)
        w.writerow(["data", "row"])
    raw, digest = runner_common.validate_candidates(tmp_path)
    assert digest == hashlib.sha256(raw).hexdigest()
    assert b"data,row" in raw


def test_validate_candidates_rejects_bad_header(tmp_path):
    f = tmp_path / "candidates.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["bad", "header"])
        w.writerow(["x", "y"])
    with pytest.raises(runner_common.RunnerError):
        runner_common.validate_candidates(tmp_path)


def test_atomic_write_writes_front_matter_then_body_and_leaves_no_tmp(tmp_path):
    runner_common.atomic_write(tmp_path, "out.md", "---\nk: v\n---\n\n", "BODY")
    out = tmp_path / "out.md"
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert text.endswith("BODY")
    assert list(tmp_path.glob("out_tmp_*")) == []
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner_common.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlier_scrapers.runner_common'`

**Step 3: Write minimal implementation**

```python
"""Shared scaffolding for the AI research-desk reasoning/research runners.

Holds the provider-agnostic mechanics — request-hash, candidates validation,
atomic front-matter write — so the per-prompt runner modules stay thin. Keeps
``reasoning.py`` (Prompt A) untouched; new runners (B, D, E) build on this.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

from outlier_scrapers import pack


class RunnerError(Exception):
    """Raised for any recoverable runner failure (-> exit code 1)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def compute_request_hash(request_data: dict) -> str:
    """Canonical, order-independent hash of the full request definition."""
    canonical = json.dumps(request_data, sort_keys=True).encode("utf-8")
    return sha256_bytes(canonical)


def extract_yaml_request_hash(content: str) -> str | None:
    """Trivial reader for the ``request_sha256`` key in YAML front matter."""
    if not content.startswith("---\n"):
        return None
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None
    for line in content[4:end_idx].splitlines():
        if line.startswith("request_sha256:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def validate_candidates(pack_dir: Path) -> tuple[bytes, str]:
    """Validate candidates.csv exists, has the canonical header and >=1 row."""
    candidates_file = pack_dir / "candidates.csv"
    if not candidates_file.exists():
        raise RunnerError(f"Candidates file {candidates_file} does not exist.")
    raw_bytes = candidates_file.read_bytes()
    with open(candidates_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != pack.CANDIDATES_HEADER:
            raise RunnerError("candidates.csv header does not match pack.CANDIDATES_HEADER")
        if len(list(reader)) == 0:
            raise RunnerError("candidates.csv has no data rows.")
    return raw_bytes, sha256_bytes(raw_bytes)


def read_required_text(path: Path, label: str) -> str:
    if not path.exists():
        raise RunnerError(f"{label} {path} missing.")
    return path.read_text(encoding="utf-8")


def atomic_write(pack_dir: Path, out_name: str, front_matter: str, body: str) -> None:
    """Write front_matter + body to pack_dir/out_name atomically (tmp + replace)."""
    stem = out_name.rsplit(".", 1)[0]
    fd, tmp_path = tempfile.mkstemp(dir=str(pack_dir), prefix=f"{stem}_tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(front_matter)
            f.write(body)
        os.replace(tmp_path, pack_dir / out_name)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RunnerError(f"Failed to write output: {type(e).__name__}") from e
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner_common.py -v`
Expected: PASS (7 passed)

**Step 5: Commit**

```bash
git add outlier_scrapers/runner_common.py tests/test_runner_common.py
git commit -m "feat: add shared runner scaffolding (hash, validate, atomic write)"
```

---

## Phase 1 — Prompt files

### Task 3: Extract Prompt B, D, E text into files

**Files:**
- Create: `prompts/B.md`, `prompts/D.md`, `prompts/E.md`

**Step 1: Copy verbatim from the runbook**

Open `AI-research-desk-runbook.md` §6. Copy the exact body of each prompt (everything inside its block, excluding the `### Prompt X — ...` heading) into the matching file:
- Prompt B (Gemini wide-scan Deep Research) → `prompts/B.md`
- Prompt D (Claude reasoning pass) → `prompts/D.md`
- Prompt E (Claude synthesis) → `prompts/E.md`

Do not paraphrase or summarize — the prompt is the product. Match `prompts/A.md` formatting conventions.

**Step 2: Verify all three exist and are non-empty**

Run:
```bash
for p in B D E; do test -s "prompts/$p.md" && echo "prompts/$p.md ok" || echo "prompts/$p.md MISSING/EMPTY"; done
```
Expected: three `ok` lines.

**Step 3: Commit**

```bash
git add prompts/B.md prompts/D.md prompts/E.md
git commit -m "docs: add Prompt B/D/E text files for automated runners"
```

---

## Phase 2 — Claude Prompt D runner (reference implementation)

> This is the closest analog to `reasoning.py` (pack-only, `candidates.csv`). Build it first and most thoroughly; B and E reuse the same shape.

### Task 4: Write the Claude D runner tests (TDD)

**Files:**
- Test: `tests/test_claude_reasoning.py`

**Step 1: Write the failing tests** (mirrors `tests/test_reasoning.py` structure with an Anthropic mock)

```python
import csv

import pytest
import anthropic

from outlier_scrapers import pack, claude_reasoning


@pytest.fixture
def claude_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_reasoning.paths, "PROJECT_ROOT", tmp_path)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "D.md").write_text("Prompt D body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)

    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pack.CANDIDATES_HEADER)
        w.writerow(["data", "row"])

    return tmp_path, date_str, pack_dir


class MockTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class MockMessage:
    def __init__(self, text="mocked claude output", stop_reason="end_turn"):
        self.content = [MockTextBlock(text)] if text else []
        self.stop_reason = stop_reason
        self.stop_details = None


class MockStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class MockMessages:
    def __init__(self):
        self.called = False
        self.kwargs = {}
        self.message = MockMessage()

    def stream(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return MockStream(self.message)


class MockAnthropic:
    def __init__(self, *args, **kwargs):
        self.client_kwargs = kwargs
        self.messages = MockMessages()


@pytest.fixture
def mock_anthropic(monkeypatch):
    clients = []

    def mock_init(*args, **kwargs):
        client = MockAnthropic(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(anthropic, "Anthropic", mock_init)
    return clients


def test_validation_missing_candidates(claude_env):
    _, date_str, pack_dir = claude_env
    (pack_dir / "candidates.csv").unlink()
    assert claude_reasoning.main(["--date", date_str]) == 1


def test_success_writes_file_and_asserts_api(claude_env, mock_anthropic):
    _, date_str, pack_dir = claude_env
    assert claude_reasoning.main(["--date", date_str]) == 0
    assert len(mock_anthropic) == 1

    api = mock_anthropic[0].messages
    assert api.called
    assert api.kwargs["model"] == "claude-opus-4-8"
    assert api.kwargs["thinking"] == {"type": "adaptive"}
    assert api.kwargs["output_config"] == {"effort": "high"}
    assert api.kwargs["system"] == "\n".join(pack.ROLE_BLOCK)
    assert "budget_tokens" not in str(api.kwargs.get("thinking"))
    assert "temperature" not in api.kwargs

    user_msg = api.kwargs["messages"][0]
    assert user_msg["role"] == "user"
    assert "Prompt D body" in user_msg["content"]
    assert "data,row" in user_msg["content"]

    out = pack_dir / "claude_d.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "request_sha256:" in text
    assert "mocked claude output" in text


def test_missing_key_on_cache_miss(monkeypatch, claude_env):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, date_str, _ = claude_env
    assert claude_reasoning.main(["--date", date_str]) == 1


def test_noop_when_output_exists_no_key_required(monkeypatch, claude_env, mock_anthropic):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, date_str, pack_dir = claude_env
    out = pack_dir / "claude_d.md"
    out.write_text("existing", encoding="utf-8")
    assert claude_reasoning.main(["--date", date_str]) == 0
    assert len(mock_anthropic) == 0
    assert out.read_text(encoding="utf-8") == "existing"


def test_force_overrides_existing(claude_env, mock_anthropic):
    _, date_str, pack_dir = claude_env
    out = pack_dir / "claude_d.md"
    out.write_text("existing", encoding="utf-8")
    assert claude_reasoning.main(["--date", date_str, "--force"]) == 0
    assert len(mock_anthropic) == 1
    assert out.read_text(encoding="utf-8") != "existing"


def test_refresh_if_stale_skips_on_hash_match_no_key(monkeypatch, claude_env, mock_anthropic):
    _, date_str, pack_dir = claude_env
    assert claude_reasoning.main(["--date", date_str]) == 0
    assert len(mock_anthropic) == 1
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert claude_reasoning.run_claude_d(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_anthropic) == 1


def test_refresh_if_stale_reruns_on_candidates_change(claude_env, mock_anthropic):
    _, date_str, pack_dir = claude_env
    assert claude_reasoning.main(["--date", date_str]) == 0
    with open(pack_dir / "candidates.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["another", "row"])
    assert claude_reasoning.run_claude_d(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_anthropic) == 2


def test_refresh_if_stale_reruns_on_model_change(claude_env, mock_anthropic, monkeypatch):
    _, date_str, pack_dir = claude_env
    assert claude_reasoning.main(["--date", date_str]) == 0
    monkeypatch.setattr(claude_reasoning, "MODEL", "claude-test-9")
    assert claude_reasoning.run_claude_d(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_anthropic) == 2


def test_refusal_rejected_no_partial_file(claude_env, monkeypatch):
    _, date_str, pack_dir = claude_env

    def mock_init(*args, **kwargs):
        c = MockAnthropic(*args, **kwargs)
        c.messages.message = MockMessage(text="", stop_reason="refusal")
        return c

    monkeypatch.setattr(anthropic, "Anthropic", mock_init)
    assert claude_reasoning.main(["--date", date_str]) == 1
    assert not (pack_dir / "claude_d.md").exists()


def test_empty_response_rejected_no_partial_file(claude_env, monkeypatch):
    _, date_str, pack_dir = claude_env

    def mock_init(*args, **kwargs):
        c = MockAnthropic(*args, **kwargs)
        c.messages.message = MockMessage(text="")
        return c

    monkeypatch.setattr(anthropic, "Anthropic", mock_init)
    assert claude_reasoning.main(["--date", date_str]) == 1
    assert not (pack_dir / "claude_d.md").exists()


def test_api_failure_leaves_no_partial(claude_env, monkeypatch):
    _, date_str, pack_dir = claude_env

    def mock_init(*args, **kwargs):
        c = MockAnthropic(*args, **kwargs)
        c.messages.stream = lambda **k: (_ for _ in ()).throw(ValueError("boom"))
        return c

    monkeypatch.setattr(anthropic, "Anthropic", mock_init)
    assert claude_reasoning.main(["--date", date_str]) == 1
    assert not (pack_dir / "claude_d.md").exists()
    assert list(pack_dir.glob("claude_d_tmp_*")) == []
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_claude_reasoning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlier_scrapers.claude_reasoning'`

**Step 3: Commit the failing tests**

```bash
git add tests/test_claude_reasoning.py
git commit -m "test: add Claude Prompt D runner tests (red)"
```

---

### Task 5: Implement the Claude D runner

**Files:**
- Create: `outlier_scrapers/claude_reasoning.py`

**Step 1: Write the implementation**

```python
"""Claude Prompt D runner — pack-only reasoning pass (input: candidates.csv).

Mirrors ``reasoning.py`` (Prompt A) but targets Claude Opus 4.8 via the
Anthropic SDK with adaptive thinking. Pack-only: no web tools. Output is
``claude_d.md`` with hash-based idempotency so the daily job can re-invoke it
without re-billing an unchanged request.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import anthropic

from outlier_scrapers import paths, pack
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import CLAUDE_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = CLAUDE_MODEL
EFFORT = "high"
MAX_TOKENS = 32_000
OUT_NAME = "claude_d.md"
PROMPT_FILE = "D.md"


def call_claude(prompt_text: str, role_block: list[str], raw_csv_bytes: bytes, client=None) -> str:
    if client is None:
        load_environment()
        if not __import__("os").getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(timeout=600.0, max_retries=1)

    full_prompt = prompt_text + "\n\nData:\n" + raw_csv_bytes.decode("utf-8")
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            system="\n".join(role_block),
            messages=[{"role": "user", "content": full_prompt}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise rc.RunnerError(
            f"API call failed: type={type(e).__name__} "
            f"status={getattr(e, 'status_code', None)} request_id={getattr(e, 'request_id', None)}"
        )
    except Exception as e:
        raise rc.RunnerError(f"API call failed: type={type(e).__name__}")

    if getattr(message, "stop_reason", None) == "refusal":
        raise rc.RunnerError("Claude refused the request (stop_reason=refusal).")
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise rc.RunnerError("Received empty or whitespace-only response from API")
    return text


def run_claude_d(
    pack_dir: Path, *, force: bool = False, refresh_if_stale: bool = False, client=None
) -> int:
    try:
        out_file = pack_dir / OUT_NAME
        if out_file.exists():
            if force:
                out_file.unlink()
            elif not refresh_if_stale:
                logger.info("Output exists. Clean no-op.")
                return 0

        raw_bytes, candidates_sha256 = rc.validate_candidates(pack_dir)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "effort": EFFORT,
                "thinking": "adaptive",
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "candidates_hash": candidates_sha256,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Claude (Prompt D)...")
        output_text = call_claude(prompt_text, pack.ROLE_BLOCK, raw_bytes, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"effort: {EFFORT}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"candidates_sha256: {candidates_sha256}\n"
            f"request_sha256: {request_sha256}\n"
            "---\n\n"
        )
        rc.atomic_write(pack_dir, OUT_NAME, front_matter, output_text)
        logger.info("Successfully wrote %s", OUT_NAME)
        return 0
    except rc.RunnerError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error("Unexpected runner error: %s", type(e).__name__)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude Prompt D reasoning runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_claude_d(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
```

> Note: the `__import__("os")` guard exists so tests can delete the env var after the client is constructed; if you prefer, add `import os` at the top and use `os.getenv` (match whichever the team's lint prefers — `reasoning.py` uses a top-level `import os`). Prefer the top-level import for readability.

**Step 2: Run tests to verify they pass**

Run: `pytest tests/test_claude_reasoning.py -v`
Expected: PASS (all tests green)

**Step 3: Run the full suite to confirm no regressions**

Run: `pytest -q`
Expected: existing `test_reasoning.py` + new tests all pass.

**Step 4: Commit**

```bash
git add outlier_scrapers/claude_reasoning.py
git commit -m "feat: add Claude Prompt D pack-only reasoning runner"
```

---

## Phase 3 — Gemini Prompt B runner (web-grounded, briefing input)

### Task 6: Write the Gemini B runner tests (TDD)

**Files:**
- Test: `tests/test_gemini_research.py`

**Step 1: Write the failing tests**

Key differences from D: input is `briefing.md` (not candidates.csv); web grounding tool must be present in the request; output is `gemini_b.md`; SDK is `google.genai`.

```python
import pytest
from google import genai

from outlier_scrapers import pack, gemini_research


@pytest.fixture
def gemini_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_research.paths, "PROJECT_ROOT", tmp_path)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "B.md").write_text("Prompt B body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("SLATE: 2026-06-27\nbriefing content", encoding="utf-8")
    return tmp_path, date_str, pack_dir


class MockResponse:
    def __init__(self, text="mocked gemini output"):
        self.text = text


class MockModels:
    def __init__(self):
        self.called = False
        self.kwargs = {}
        self.response = MockResponse()

    def generate_content(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return self.response


class MockGenaiClient:
    def __init__(self, *args, **kwargs):
        self.client_kwargs = kwargs
        self.models = MockModels()


@pytest.fixture
def mock_genai(monkeypatch):
    clients = []

    def mock_init(*args, **kwargs):
        c = MockGenaiClient(*args, **kwargs)
        clients.append(c)
        return c

    monkeypatch.setattr(genai, "Client", mock_init)
    return clients


def test_validation_missing_briefing(gemini_env):
    _, date_str, pack_dir = gemini_env
    (pack_dir / "briefing.md").unlink()
    assert gemini_research.main(["--date", date_str]) == 1


def test_success_writes_file_and_grounding_enabled(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    assert gemini_research.main(["--date", date_str]) == 0
    assert len(mock_genai) == 1

    models = mock_genai[0].models
    assert models.called
    assert models.kwargs["model"] == "gemini-3.1-pro-preview"
    config = models.kwargs["config"]
    # Google Search grounding tool must be present (web allowed for Prompt B).
    assert config.tools, "grounding tool must be configured"
    assert "Prompt B body" in models.kwargs["contents"]
    assert "briefing content" in models.kwargs["contents"]

    out = pack_dir / "gemini_b.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "request_sha256:" in text
    assert "mocked gemini output" in text


def test_missing_key_on_cache_miss(monkeypatch, gemini_env):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _, date_str, _ = gemini_env
    assert gemini_research.main(["--date", date_str]) == 1


def test_noop_when_output_exists_no_key(monkeypatch, gemini_env, mock_genai):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _, date_str, pack_dir = gemini_env
    (pack_dir / "gemini_b.md").write_text("existing", encoding="utf-8")
    assert gemini_research.main(["--date", date_str]) == 0
    assert len(mock_genai) == 0


def test_force_overrides_existing(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    out = pack_dir / "gemini_b.md"
    out.write_text("existing", encoding="utf-8")
    assert gemini_research.main(["--date", date_str, "--force"]) == 0
    assert out.read_text(encoding="utf-8") != "existing"


def test_refresh_reruns_on_briefing_change(gemini_env, mock_genai):
    _, date_str, pack_dir = gemini_env
    assert gemini_research.main(["--date", date_str]) == 0
    (pack_dir / "briefing.md").write_text("CHANGED briefing", encoding="utf-8")
    assert gemini_research.run_gemini_b(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_genai) == 2


def test_empty_response_rejected_no_partial(gemini_env, monkeypatch):
    _, date_str, pack_dir = gemini_env

    def mock_init(*args, **kwargs):
        c = MockGenaiClient(*args, **kwargs)
        c.models.response = MockResponse(text="")
        return c

    monkeypatch.setattr(genai, "Client", mock_init)
    assert gemini_research.main(["--date", date_str]) == 1
    assert not (pack_dir / "gemini_b.md").exists()
```

**Step 2: Run to verify it fails**

Run: `pytest tests/test_gemini_research.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlier_scrapers.gemini_research'`

**Step 3: Commit (red)**

```bash
git add tests/test_gemini_research.py
git commit -m "test: add Gemini Prompt B runner tests (red)"
```

---

### Task 7: Implement the Gemini B runner

**Files:**
- Create: `outlier_scrapers/gemini_research.py`

**Step 1: Verify the google-genai grounding API shape**

Before coding, confirm the current call signature (the SDK evolves). Use the documentation-lookup skill / Context7 for `google-genai`, or run:
```bash
python -c "from google.genai import types; print(hasattr(types, 'GoogleSearch'), hasattr(types, 'Tool'))"
```
Expected: `True True`. If the grounding type name differs in the installed version, adjust the `types.Tool(...)` construction accordingly and note it in the commit.

**Step 2: Write the implementation**

```python
"""Gemini Prompt B runner — wide-scan research pass (input: briefing.md).

Targets Gemini 3.1 Pro Preview via the google-genai SDK with Google Search
grounding (Prompt B is web-allowed). This automates a single grounded pass; it
is NOT the full Gemini Deep Research UI product (see plan caveats). Output is
``gemini_b.md`` with the same hash-based idempotency as the other runners.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from google import genai
from google.genai import types

from outlier_scrapers import paths, pack
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import GEMINI_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = GEMINI_MODEL
MAX_TOKENS = 32_000
GROUNDING = "google_search"
OUT_NAME = "gemini_b.md"
PROMPT_FILE = "B.md"


def call_gemini(prompt_text: str, role_block: list[str], briefing_text: str, client=None) -> str:
    if client is None:
        load_environment()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise rc.RunnerError("GEMINI_API_KEY is not set.")
        client = genai.Client(api_key=api_key)

    full_prompt = prompt_text + "\n\nBriefing:\n" + briefing_text
    config = types.GenerateContentConfig(
        system_instruction="\n".join(role_block),
        tools=[types.Tool(google_search=types.GoogleSearch())],
        max_output_tokens=MAX_TOKENS,
    )
    try:
        response = client.models.generate_content(
            model=MODEL, contents=full_prompt, config=config
        )
    except Exception as e:
        raise rc.RunnerError(f"API call failed: type={type(e).__name__}")

    text = getattr(response, "text", None) or ""
    if not text.strip():
        raise rc.RunnerError("Received empty or whitespace-only response from API")
    return text


def run_gemini_b(
    pack_dir: Path, *, force: bool = False, refresh_if_stale: bool = False, client=None
) -> int:
    try:
        out_file = pack_dir / OUT_NAME
        if out_file.exists():
            if force:
                out_file.unlink()
            elif not refresh_if_stale:
                logger.info("Output exists. Clean no-op.")
                return 0

        briefing_text = rc.read_required_text(pack_dir / "briefing.md", "Briefing")
        briefing_sha256 = rc.sha256_text(briefing_text)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "grounding": GROUNDING,
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "briefing_hash": briefing_sha256,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Gemini (Prompt B)...")
        output_text = call_gemini(prompt_text, pack.ROLE_BLOCK, briefing_text, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"grounding: {GROUNDING}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"briefing_sha256: {briefing_sha256}\n"
            f"request_sha256: {request_sha256}\n"
            "---\n\n"
        )
        rc.atomic_write(pack_dir, OUT_NAME, front_matter, output_text)
        logger.info("Successfully wrote %s", OUT_NAME)
        return 0
    except rc.RunnerError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error("Unexpected runner error: %s", type(e).__name__)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini Prompt B research runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_gemini_b(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
```

**Step 3: Run tests to verify they pass**

Run: `pytest tests/test_gemini_research.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add outlier_scrapers/gemini_research.py
git commit -m "feat: add Gemini Prompt B web-grounded research runner"
```

---

## Phase 4 — Claude Prompt E synthesis runner (multi-input)

> Prompt E synthesizes the four structured outputs (A, B, C, D) + briefing into the final guide. C (`chatgpt_c.md`) is still a manual Deep Research paste — treat it as **optional**; A, B, D, and briefing are **required**.

### Task 8: Write the Claude E synthesis tests (TDD)

**Files:**
- Test: `tests/test_claude_synthesis.py`

**Step 1: Write the failing tests** (reuse the `MockAnthropic` stack from Task 4 — copy the mock classes in, or import them from a shared test helper if you prefer; keep tests self-contained to match the existing repo style)

```python
import pytest
import anthropic

from outlier_scrapers import pack, claude_synthesis

# (Copy MockTextBlock / MockMessage / MockStream / MockMessages / MockAnthropic
#  and the mock_anthropic fixture from tests/test_claude_reasoning.py.)


@pytest.fixture
def synth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(claude_synthesis.paths, "PROJECT_ROOT", tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "E.md").write_text("Prompt E body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.md").write_text("briefing", encoding="utf-8")
    (pack_dir / "chatgpt_a.md").write_text("A output", encoding="utf-8")
    (pack_dir / "gemini_b.md").write_text("B output", encoding="utf-8")
    (pack_dir / "claude_d.md").write_text("D output", encoding="utf-8")
    return tmp_path, date_str, pack_dir


def test_missing_required_input_fails(synth_env):
    _, date_str, pack_dir = synth_env
    (pack_dir / "gemini_b.md").unlink()
    assert claude_synthesis.main(["--date", date_str]) == 1


def test_success_includes_all_inputs(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    assert claude_synthesis.main(["--date", date_str]) == 0
    content = mock_anthropic[0].messages.kwargs["messages"][0]["content"]
    assert "Prompt E body" in content
    for token in ("A output", "B output", "D output", "briefing"):
        assert token in content
    out = pack_dir / "claude_e.md"
    assert out.exists()
    assert "request_sha256:" in out.read_text(encoding="utf-8")


def test_optional_c_included_when_present(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    (pack_dir / "chatgpt_c.md").write_text("C output", encoding="utf-8")
    assert claude_synthesis.main(["--date", date_str]) == 0
    content = mock_anthropic[0].messages.kwargs["messages"][0]["content"]
    assert "C output" in content


def test_refresh_reruns_when_an_input_changes(synth_env, mock_anthropic):
    _, date_str, pack_dir = synth_env
    assert claude_synthesis.main(["--date", date_str]) == 0
    (pack_dir / "claude_d.md").write_text("D output CHANGED", encoding="utf-8")
    assert claude_synthesis.run_claude_e(pack_dir, refresh_if_stale=True) == 0
    assert len(mock_anthropic) == 2


def test_noop_when_output_exists_no_key(monkeypatch, synth_env, mock_anthropic):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, date_str, pack_dir = synth_env
    (pack_dir / "claude_e.md").write_text("existing", encoding="utf-8")
    assert claude_synthesis.main(["--date", date_str]) == 0
    assert len(mock_anthropic) == 0
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_claude_synthesis.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Commit (red)**

```bash
git add tests/test_claude_synthesis.py
git commit -m "test: add Claude Prompt E synthesis runner tests (red)"
```

---

### Task 9: Implement the Claude E synthesis runner

**Files:**
- Create: `outlier_scrapers/claude_synthesis.py`

**Step 1: Write the implementation**

```python
"""Claude Prompt E runner — synthesis pass (inputs: briefing + A/B/D outputs).

Combines the prior structured outputs into the final guide via Claude Opus 4.8.
A (chatgpt_a.md), B (gemini_b.md), D (claude_d.md), and briefing.md are
required; C (chatgpt_c.md, manual Deep Research) is included when present.
Output is ``claude_e.md`` with hash-based idempotency over every input.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import anthropic

from outlier_scrapers import paths, pack
from outlier_scrapers.environment import load_environment
from outlier_scrapers.models import CLAUDE_MODEL
from outlier_scrapers import runner_common as rc

logger = logging.getLogger(__name__)

MODEL = CLAUDE_MODEL
EFFORT = "high"
MAX_TOKENS = 32_000
OUT_NAME = "claude_e.md"
PROMPT_FILE = "E.md"

REQUIRED_INPUTS = {
    "briefing": "briefing.md",
    "prompt_a": "chatgpt_a.md",
    "prompt_b": "gemini_b.md",
    "prompt_d": "claude_d.md",
}
OPTIONAL_INPUTS = {"prompt_c": "chatgpt_c.md"}


def gather_inputs(pack_dir: Path) -> dict[str, str]:
    """Return {label: text} for all required (and any present optional) inputs."""
    collected: dict[str, str] = {}
    for label, fname in REQUIRED_INPUTS.items():
        collected[label] = rc.read_required_text(pack_dir / fname, f"Required input '{label}'")
    for label, fname in OPTIONAL_INPUTS.items():
        path = pack_dir / fname
        if path.exists():
            collected[label] = path.read_text(encoding="utf-8")
    return collected


def build_user_content(prompt_text: str, inputs: dict[str, str]) -> str:
    sections = [prompt_text]
    for label, text in inputs.items():
        sections.append(f"\n\n===== {label.upper()} =====\n{text}")
    return "".join(sections)


def call_claude(user_content: str, role_block: list[str], client=None) -> str:
    if client is None:
        load_environment()
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise rc.RunnerError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(timeout=600.0, max_retries=1)
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            system="\n".join(role_block),
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.APIError as e:
        raise rc.RunnerError(
            f"API call failed: type={type(e).__name__} "
            f"status={getattr(e, 'status_code', None)} request_id={getattr(e, 'request_id', None)}"
        )
    except Exception as e:
        raise rc.RunnerError(f"API call failed: type={type(e).__name__}")

    if getattr(message, "stop_reason", None) == "refusal":
        raise rc.RunnerError("Claude refused the request (stop_reason=refusal).")
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if not text.strip():
        raise rc.RunnerError("Received empty or whitespace-only response from API")
    return text


def run_claude_e(
    pack_dir: Path, *, force: bool = False, refresh_if_stale: bool = False, client=None
) -> int:
    try:
        out_file = pack_dir / OUT_NAME
        if out_file.exists():
            if force:
                out_file.unlink()
            elif not refresh_if_stale:
                logger.info("Output exists. Clean no-op.")
                return 0

        inputs = gather_inputs(pack_dir)
        prompt_text = rc.read_required_text(
            paths.PROJECT_ROOT / "prompts" / PROMPT_FILE, "Prompt file"
        )

        input_hashes = {label: rc.sha256_text(text) for label, text in inputs.items()}
        request_sha256 = rc.compute_request_hash(
            {
                "model": MODEL,
                "effort": EFFORT,
                "thinking": "adaptive",
                "role_block": pack.ROLE_BLOCK,
                "prompt": prompt_text,
                "input_hashes": input_hashes,
            }
        )

        if out_file.exists() and refresh_if_stale:
            existing = rc.extract_yaml_request_hash(out_file.read_text(encoding="utf-8"))
            if existing == request_sha256:
                logger.info("Output exists and matches hash. Skipping.")
                return 0
            out_file.unlink()

        logger.info("Calling Claude (Prompt E synthesis)...")
        user_content = build_user_content(prompt_text, inputs)
        output_text = call_claude(user_content, pack.ROLE_BLOCK, client=client)

        front_matter = (
            "---\n"
            f"model: {MODEL}\n"
            f"effort: {EFFORT}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
            f"inputs: {','.join(sorted(inputs))}\n"
            f"request_sha256: {request_sha256}\n"
            "---\n\n"
        )
        rc.atomic_write(pack_dir, OUT_NAME, front_matter, output_text)
        logger.info("Successfully wrote %s", OUT_NAME)
        return 0
    except rc.RunnerError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error("Unexpected runner error: %s", type(e).__name__)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude Prompt E synthesis runner.")
    parser.add_argument("--date", default=datetime.now().astimezone().strftime("%Y-%m-%d"))
    parser.add_argument("--force", action="store_true", help="Force replace output.")
    args = parser.parse_args(argv)
    pack_dir = paths.PROJECT_ROOT / "packs" / args.date
    return run_claude_e(pack_dir, force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
```

**Step 2: Run tests to verify they pass**

Run: `pytest tests/test_claude_synthesis.py -v`
Expected: PASS

**Step 3: Run the whole suite**

Run: `pytest -q`
Expected: all green.

**Step 4: Commit**

```bash
git add outlier_scrapers/claude_synthesis.py
git commit -m "feat: add Claude Prompt E synthesis runner"
```

---

## Phase 5 — Documentation & optional integration

### Task 10: Document the new runners

**Files:**
- Modify: `README.md` (near the existing `--run-reasoning` section)
- Modify: `AI-research-desk-runbook.md` (under the Prompt B/D/E headings, mirroring the Prompt A "Run it independently with …" note)

**Step 1: Add a usage block** describing the three standalone commands and that they require the matching key (`ANTHROPIC_API_KEY` / `GEMINI_API_KEY`) and consume paid API quota:

```powershell
python -m outlier_scrapers.gemini_research --date YYYY-MM-DD
python -m outlier_scrapers.claude_reasoning --date YYYY-MM-DD
python -m outlier_scrapers.claude_synthesis --date YYYY-MM-DD
```

Note the dependency order: E requires A/B/D outputs to exist first. Mention Prompt B is single-pass grounded generation (not the Deep Research UI), and Prompt C remains a manual paste.

**Step 2: Commit**

```bash
git add README.md AI-research-desk-runbook.md
git commit -m "docs: document Gemini B and Claude D/E standalone runners"
```

---

### Task 11 (OPTIONAL): Wire runners into the daily job

**Files:**
- Read first: `outlier_scrapers/daily_job.py` (see how `--run-reasoning` calls `reasoning.run_reasoning`)
- Modify: `outlier_scrapers/daily_job.py`
- Test: `tests/test_daily_job.py` (extend existing if present)

**Step 1:** Add opt-in flags mirroring `--run-reasoning`: `--run-prompt-d`, `--run-prompt-b`, `--run-synthesis` (or a combined `--run-claude`/`--run-gemini`). Each calls the corresponding `run_*` with `refresh_if_stale=True` after the pack is built, in dependency order (B and D can run in parallel conceptually but call sequentially; E last, only if A/B/D outputs exist). A failing paid runner should set a nonzero exit code without deleting the pack — match the existing `--run-reasoning` contract exactly.

**Step 2:** TDD the flag wiring with the daily job's existing mock patterns. Run `pytest tests/test_daily_job.py -v`.

**Step 3: Commit**

```bash
git add outlier_scrapers/daily_job.py tests/test_daily_job.py
git commit -m "feat: wire Gemini/Claude runners into daily job (opt-in flags)"
```

---

### Task 12 (OPTIONAL): Migrate `reasoning.py` onto `runner_common`

**Files:**
- Modify: `outlier_scrapers/reasoning.py`

Replace the duplicated `extract_yaml_request_hash`, `validate_pack_dir`, request-hash, and atomic-write logic with calls into `runner_common` (use `validate_candidates`, `compute_request_hash`, `extract_yaml_request_hash`, `atomic_write`). **The full existing `tests/test_reasoning.py` is the safety net — it must stay green with zero changes.**

**Step 1:** Refactor in small steps, running `pytest tests/test_reasoning.py -v` after each.
**Step 2:** Run `pytest -q` for the full suite.
**Step 3: Commit**

```bash
git add outlier_scrapers/reasoning.py
git commit -m "refactor: migrate Prompt A runner onto shared runner_common"
```

---

## Design decisions & open questions

1. **Shared module, not a base class.** B, D, and E have genuinely different inputs (briefing vs candidates vs four-output synthesis), so a forced base class would leak. We DRY the *mechanics* (hash, validate, atomic write) and keep each runner's thin control flow explicit (KISS/YAGNI). Each `run_*` is ~40 lines and reads top-to-bottom.

2. **Streaming for Claude, non-streaming for Gemini.** The Anthropic SDK raises on non-streaming requests with large `max_tokens`, so D/E stream and use `get_final_message()`. The synthesis (E) final guide can be long; streaming protects against HTTP timeouts. Gemini's single grounded pass uses plain `generate_content`.

3. **Idempotency parity.** Every runner reproduces `reasoning.py`'s request-hash contract so the daily job can re-invoke with `refresh_if_stale=True` and pay nothing for an unchanged request. Hash inputs per runner: D = candidates; B = briefing; E = all input-file hashes.

4. **Key checked only on cache miss.** Like `reasoning.py`, the API key is validated inside `call_*`, so a clean no-op (output already exists) needs no key — keeps the daily job resilient.

## Caveats to call out to the user during execution

- **Prompt B ≠ Deep Research.** The google-genai runner is a single Google-Search-grounded generation, not the multi-step Gemini Deep Research UI product the runbook's manual flow uses. It approximates B for automation; flag this so expectations are set.
- **Prompt C stays manual.** ChatGPT Deep Research (C) has no API runner here; E includes it only if `chatgpt_c.md` is present.
- **SDK version drift.** `google-genai` grounding type names (`types.GoogleSearch`) and `anthropic` thinking/effort surface evolve. Task 1 pins resolved versions; Task 7 Step 1 re-verifies the grounding API before coding.
- **Cost.** All three are paid, `effort: "high"` / `xhigh`-class calls. They are opt-in and idempotent, but a `--force` or changed input re-bills.

---

## Execution Handoff

Build order: **Phase 0 → 1 → 2 → 3 → 4** are required and sequential (Phase 2's Claude D is the reference implementation; Phases 3–4 reuse its shape). **Phase 5** is optional (docs are recommended; daily-job wiring and the `reasoning.py` refactor are nice-to-haves). Each task is TDD: red test → minimal implementation → green → commit.
