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
            "_event_starts_at": "2099-01-01T12:00:00Z"
        })
        w.writerow(row)


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
    assert api.kwargs["model"] == "claude-3-5-sonnet-20241022"
    assert api.kwargs["system"] == "\n".join(pack.ROLE_BLOCK)
    assert "budget_tokens" not in str(api.kwargs.get("thinking"))
    assert "temperature" not in api.kwargs

    user_msg = api.kwargs["messages"][0]
    assert user_msg["role"] == "user"
    assert "Prompt D body" in user_msg["content"]
    assert "test" in user_msg["content"]

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
        w = csv.DictWriter(f, fieldnames=pack.CANDIDATES_HEADER)
        row = {k: "" for k in pack.CANDIDATES_HEADER}
        row.update({
            "sport": "MLB",
            "event_id": "e2",
            "market_id": "m2",
            "selection": "test2",
            "team_name": "C",
            "opp_name": "D",
            "matchup": "C @ D",
            "_event_starts_at": "2099-01-01T12:00:00Z"
        })
        w.writerow(row)
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
