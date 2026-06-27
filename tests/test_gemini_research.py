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
