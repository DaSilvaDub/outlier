import pytest
import anthropic

from outlier_scrapers import pack, claude_synthesis


# Copied from tests/test_claude_reasoning.py to keep this test file self-contained
# (repo style: no shared conftest for mocks across these runner tests)

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
