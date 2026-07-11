import csv

import pytest
import openai

from outlier_scrapers import pack, reasoning


@pytest.fixture
def reasoning_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(reasoning.paths, "PROJECT_ROOT", tmp_path)

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "A.md").write_text("Prompt body", encoding="utf-8")

    date_str = "2026-06-27"
    pack_dir = tmp_path / "packs" / date_str
    pack_dir.mkdir(parents=True)

    candidates_file = pack_dir / "candidates.csv"
    with open(candidates_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pack.CANDIDATES_HEADER)
        w.writeheader()
        row = {k: "" for k in pack.CANDIDATES_HEADER}
        row.update({
            "sport": "MLB",
            "event_id": "e1",
            "market_id": "m1",
            "selection": "test",
            "team_name": "A",
            "opp_name": "B",
            "matchup": "A @ B",
            "_event_starts_at": "2099-01-01T12:00:00Z",
        })
        w.writerow(row)

    return tmp_path, date_str, pack_dir


def _append_candidate(pack_dir, **overrides):
    """Append one candidate row (defaults to a pregame future start)."""
    row = {k: "" for k in pack.CANDIDATES_HEADER}
    row.update({"_event_starts_at": "2099-01-01T12:00:00Z"})
    row.update(overrides)
    with open(pack_dir / "candidates.csv", "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=pack.CANDIDATES_HEADER).writerow(row)


class MockResponse:
    def __init__(self, text="mocked output"):
        self.output_text = text


class MockResponsesAPI:
    def __init__(self):
        self.called = False
        self.kwargs = {}
        self.mock_text = "mocked output"

    def create(self, *args, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return MockResponse(self.mock_text)


class MockClient:
    def __init__(self, *args, **kwargs):
        self.client_kwargs = kwargs
        self.responses = MockResponsesAPI()


@pytest.fixture
def mock_openai(monkeypatch):
    clients = []

    def mock_init(*args, **kwargs):
        client = MockClient(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(openai, "OpenAI", mock_init)
    return clients


def test_validation_missing_candidates(reasoning_env):
    _, date_str, pack_dir = reasoning_env
    (pack_dir / "candidates.csv").unlink()
    # It returns 1 when run through main
    assert reasoning.main(["--date", date_str]) == 1


def test_validation_bad_candidates_header(reasoning_env):
    _, date_str, pack_dir = reasoning_env
    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["bad", "header"])
        writer.writerow(["data", "row"])

    assert reasoning.main(["--date", date_str]) == 1


def test_reasoning_success_writes_file_and_asserts_api(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env

    exit_code = reasoning.main(["--date", date_str])
    assert exit_code == 0
    assert len(mock_openai) == 1

    client = mock_openai[0]
    # Check client timeout and retries
    assert client.client_kwargs.get("timeout") == 600.0
    assert client.client_kwargs.get("max_retries") == 10

    api = client.responses
    assert api.called
    assert api.kwargs["model"] == "gpt-5.5"
    assert api.kwargs["reasoning"] == {"effort": "xhigh"}
    assert api.kwargs["max_output_tokens"] == 32_000
    assert api.kwargs["store"] is False
    assert "tools" not in api.kwargs
    assert api.kwargs["instructions"] == "\n".join(pack.ROLE_BLOCK)

    user_input = api.kwargs["input"][0]
    assert user_input["role"] == "user"
    assert "Prompt body" in user_input["content"]
    assert "candidates.csv:" in user_input["content"]
    assert "m1" in user_input["content"]

    out_file = pack_dir / "chatgpt_a.md"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "request_sha256:" in content
    assert "game_totals_sha256:" in content
    assert "mocked output" in content


def test_reasoning_time_lock_filter_drops_started_events(reasoning_env, mock_openai):
    """Fix A must be re-applied for Prompt A: an event that has already started
    (past _event_starts_at) must not reach the OpenAI prompt, while the pregame
    event is retained. Regression guard for F1."""
    _, date_str, pack_dir = reasoning_env
    _append_candidate(
        pack_dir,
        sport="MLB",
        event_id="locked",
        market_id="LOCKED_MKT",
        selection="already started",
        matchup="X @ Y",
        _event_starts_at="2000-01-01T00:00:00Z",
    )

    assert reasoning.main(["--date", date_str]) == 0
    content = mock_openai[-1].responses.kwargs["input"][0]["content"]
    assert "LOCKED_MKT" not in content  # started event filtered out at reasoning time
    assert "m1" in content              # pregame event retained


def test_missing_key_on_cache_miss(monkeypatch, reasoning_env):
    # Tests that key is checked only when an API request is actually required
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, date_str, _ = reasoning_env
    assert reasoning.main(["--date", date_str]) == 1


def test_standalone_noop_when_output_exists_no_key_required(
    monkeypatch, reasoning_env, mock_openai
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _, date_str, pack_dir = reasoning_env
    out_file = pack_dir / "chatgpt_a.md"
    out_file.write_text("existing output", encoding="utf-8")

    # Should not fail on missing key because it no-ops
    exit_code = reasoning.main(["--date", date_str])

    assert exit_code == 0
    assert len(mock_openai) == 0
    assert out_file.read_text(encoding="utf-8") == "existing output"


def test_refresh_if_stale_skips_when_hash_matches_no_key_required(
    monkeypatch, reasoning_env, mock_openai
):
    # 1. Run normally to generate a valid hash (with a key)
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    # 2. Delete key, run with refresh_if_stale flag
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    # No new client created
    assert len(mock_openai) == 1


def test_refresh_if_stale_reruns_when_hash_mismatches(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env

    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    # Add a second pregame candidate to invalidate the hash. Must be a valid
    # (kept) row: the reasoning-time lock filter now hashes the filtered set, so
    # a row that gets dropped would leave the hash unchanged.
    _append_candidate(pack_dir, sport="MLB", event_id="e2", market_id="m2", selection="test2", matchup="C @ D")

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)

    assert exit_code == 0
    assert len(mock_openai) == 2


def test_force_overrides_existing_output(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env
    out_file = pack_dir / "chatgpt_a.md"
    out_file.write_text("existing output", encoding="utf-8")

    exit_code = reasoning.main(["--date", date_str, "--force"])

    assert exit_code == 0
    assert len(mock_openai) == 1
    assert out_file.read_text(encoding="utf-8") != "existing output"


def test_api_failure_leaves_no_partial_file(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env

    # Modify the mock to raise exception
    def mock_failing_create(*args, **kwargs):
        raise ValueError("API error")

    # Next client will raise error
    def mock_init(*args, **kwargs):
        client = MockClient(*args, **kwargs)
        client.responses.create = mock_failing_create
        mock_openai.append(client)
        return client

    # We must patch again directly inside test for this specific behavior
    import openai

    with pytest.MonkeyPatch.context() as m:
        m.setattr(openai, "OpenAI", mock_init)
        exit_code = reasoning.main(["--date", date_str])

    assert exit_code == 1
    assert not (pack_dir / "chatgpt_a.md").exists()
    assert len(list(pack_dir.glob("chatgpt_a_tmp_*"))) == 0


def test_empty_response_is_rejected(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env

    def mock_init(*args, **kwargs):
        client = MockClient(*args, **kwargs)
        client.responses.mock_text = ""
        mock_openai.append(client)
        return client

    import openai

    with pytest.MonkeyPatch.context() as m:
        m.setattr(openai, "OpenAI", mock_init)
        exit_code = reasoning.main(["--date", date_str])

    assert exit_code == 1
    assert not (pack_dir / "chatgpt_a.md").exists()


def test_failed_forced_replacement_deletes_old_state(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env
    out_file = pack_dir / "chatgpt_a.md"
    out_file.write_text("existing output", encoding="utf-8")

    def mock_init(*args, **kwargs):
        client = MockClient(*args, **kwargs)
        client.responses.create = lambda *a, **k: (_ for _ in ()).throw(ValueError("API error"))
        mock_openai.append(client)
        return client

    import openai

    with pytest.MonkeyPatch.context() as m:
        m.setattr(openai, "OpenAI", mock_init)
        exit_code = reasoning.main(["--date", date_str, "--force"])

    assert exit_code == 1
    # Output file should be deleted by force, and no temp file should be left
    assert not out_file.exists()
    assert len(list(pack_dir.glob("chatgpt_a_tmp_*"))) == 0


def test_refresh_if_stale_reruns_when_prompt_mismatches(reasoning_env, mock_openai, tmp_path):
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    prompts_dir = tmp_path / "prompts"
    (prompts_dir / "A.md").write_text("New Prompt body", encoding="utf-8")

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    assert len(mock_openai) == 2


def test_refresh_if_stale_reruns_when_role_block_mismatches(
    reasoning_env, mock_openai, monkeypatch
):
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    monkeypatch.setattr(pack, "ROLE_BLOCK", ["Changed role"])

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    assert len(mock_openai) == 2


def test_refresh_if_stale_reruns_when_model_mismatches(reasoning_env, mock_openai, monkeypatch):
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    monkeypatch.setattr(reasoning, "MODEL", "gpt-9.9")

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    assert len(mock_openai) == 2


def test_reasoning_injects_game_totals_context(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env
    (pack_dir / "game_totals.csv").write_text(
        "sport,market_id,selection\nMLB,gm1,Total O/U OVER 8.5\n", encoding="utf-8"
    )
    assert reasoning.main(["--date", date_str]) == 0
    api = mock_openai[-1].responses
    content = api.kwargs["input"][0]["content"]
    assert "GAME_TOTALS.CSV" in content
    assert "gm1" in content


def test_refresh_if_stale_reruns_when_game_totals_mismatches(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    (pack_dir / "game_totals.csv").write_text("sport,market_id\nMLB,gm1\n", encoding="utf-8")
    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    assert len(mock_openai) == 2


def test_refresh_if_stale_reruns_when_effort_mismatches(reasoning_env, mock_openai, monkeypatch):
    _, date_str, pack_dir = reasoning_env
    assert reasoning.main(["--date", date_str]) == 0
    assert len(mock_openai) == 1

    monkeypatch.setattr(reasoning, "EFFORT", "low")

    exit_code = reasoning.run_reasoning(pack_dir, refresh_if_stale=True)
    assert exit_code == 0
    assert len(mock_openai) == 2


def test_whitespace_response_is_rejected(reasoning_env, mock_openai):
    _, date_str, pack_dir = reasoning_env

    def mock_init(*args, **kwargs):
        client = MockClient(*args, **kwargs)
        client.responses.mock_text = "   \n  \t  "
        mock_openai.append(client)
        return client

    import openai

    with pytest.MonkeyPatch.context() as m:
        m.setattr(openai, "OpenAI", mock_init)
        exit_code = reasoning.main(["--date", date_str])
    assert exit_code == 1
    assert not (pack_dir / "chatgpt_a.md").exists()


def test_failed_forced_replacement_deletes_old_state_on_preflight_failure(reasoning_env):
    _, date_str, pack_dir = reasoning_env
    out_file = pack_dir / "chatgpt_a.md"
    out_file.write_text("existing output", encoding="utf-8")

    (pack_dir / "candidates.csv").unlink()

    exit_code = reasoning.main(["--date", date_str, "--force"])

    assert exit_code == 1
    assert not out_file.exists()
