from types import SimpleNamespace

from google.genai import errors

import genai_service
import main
from conftest import make_pdf

# Captured at import, before the autouse fixture swaps in an offline fake.
REAL_COMPLETE = genai_service.complete


def test_extract_pages_keeps_page_order():
    pages = main.extract_pages(make_pdf(["First page", "", "Third page"]))
    assert pages == ["First page", "", "Third page"]


def test_clean_text_normalises_whitespace():
    assert main.clean_text("a \t b\x00\n\n\n\nc  ") == "a b\n\nc"


def test_safe_filename_strips_paths_and_control_characters():
    assert main.safe_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert main.safe_filename("C:\\Users\\x\\report\x07.pdf") == "report.pdf"
    assert main.safe_filename(None) == "document.pdf"


def test_split_pages_uses_one_based_pages_and_skips_empty(monkeypatch):
    chunks = genai_service.split_pages(["one", "  ", "three"])
    assert [chunk["page"] for chunk in chunks] == [1, 3]


def test_chunking_overlaps_and_keeps_page(monkeypatch):
    monkeypatch.setattr(genai_service, "CHUNK_SIZE", 60)
    monkeypatch.setattr(genai_service, "CHUNK_OVERLAP", 10)
    text = " ".join(f"Sentence number {i} about solar energy." for i in range(40))
    chunks = genai_service.split_pages(["intro", text])

    assert len(chunks) > 2
    assert {chunk["page"] for chunk in chunks} == {1, 2}
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))


def test_classify_errors():
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "x", "status": "RESOURCE_EXHAUSTED"}})
    denied = errors.ClientError(403, {"error": {"code": 403, "message": "x", "status": "PERMISSION_DENIED"}})
    missing = errors.ClientError(404, {"error": {"code": 404, "message": "model is not found", "status": "NOT_FOUND"}})

    assert genai_service.classify_ai_error(quota)[0] == 429
    assert genai_service.classify_ai_error(denied)[0] == 502
    assert "GEMINI_MODEL" in genai_service.classify_ai_error(missing)[1]
    assert genai_service.classify_ai_error(genai_service.AIConfigurationError())[0] == 503
    assert genai_service.classify_ai_error(TimeoutError("timed out"))[0] == 504
    assert genai_service.classify_ai_error(ConnectionError("network down"))[0] == 503
    assert genai_service.classify_ai_error(ValueError("other"))[0] == 502


def test_gemini_answer_settings_are_bounded():
    """Gemini 3 models must not get a temperature, and answers are capped."""

    config = genai_service.generation_config()
    assert config.temperature is None
    assert config.max_output_tokens == genai_service.ANSWER_MAX_TOKENS
    assert config.thinking_config.thinking_level.value == "LOW"
    assert config.http_options.timeout == genai_service.LLM_TIMEOUT_SECONDS * 1000
    assert genai_service.generation_config(60).http_options.timeout == 60_000


def test_gemini_client_has_timeout(monkeypatch):
    import google.genai as genai_module

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(genai_module, "Client", FakeClient)
    monkeypatch.setattr(genai_service, "_client", None)

    genai_service.genai_client()
    assert captured["http_options"].timeout == max(genai_service.LLM_TIMEOUT_SECONDS, genai_service.FALLBACK_TIMEOUT_SECONDS) * 1000
    assert captured["api_key"] == "test-key-not-real"


def test_complete_rejects_empty_answer(monkeypatch):
    class FakeModels:
        def generate_content(self, **kwargs):
            assert "temperature" not in str(kwargs["config"].model_dump(exclude_none=True))
            return SimpleNamespace(text=None)

    monkeypatch.setattr(genai_service, "_client", SimpleNamespace(models=FakeModels()))
    try:
        REAL_COMPLETE("gemini-test", "prompt")
        raise AssertionError("expected EmptyAnswerError")
    except genai_service.EmptyAnswerError:
        pass


def test_embedding_configuration(monkeypatch):
    import llama_index.embeddings.google_genai as embeddings_module

    captured = {}

    class FakeEmbedding:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(embeddings_module, "GoogleGenAIEmbedding", FakeEmbedding)
    monkeypatch.setattr(genai_service, "_configured", False)
    monkeypatch.setattr(genai_service, "Settings", SimpleNamespace())

    genai_service.configure_gemini()
    assert captured["embedding_config"] == {"output_dimensionality": 768}
    assert captured["timeout"] == 60


def test_with_deadline_stops_waiting_for_slow_calls():
    import time

    start = time.monotonic()
    try:
        genai_service.with_deadline(0.2, time.sleep, 2)
        raise AssertionError("expected AITimeoutError")
    except genai_service.AITimeoutError as exc:
        assert "timed out" in str(exc)
    assert time.monotonic() - start < 1.5
    assert genai_service.classify_ai_error(genai_service.AITimeoutError("timed out"))[0] == 504


def test_complete_enforces_wall_clock_deadline(monkeypatch):
    import time

    class SlowModels:
        def generate_content(self, **kwargs):
            time.sleep(2)  # e.g. a response that trickles bytes slowly
            return SimpleNamespace(text="late")

    monkeypatch.setattr(genai_service, "_client", SimpleNamespace(models=SlowModels()))
    start = time.monotonic()
    try:
        REAL_COMPLETE("gemini-test", "prompt", 0.2)
        raise AssertionError("expected AITimeoutError")
    except genai_service.AITimeoutError:
        pass
    assert time.monotonic() - start < 1.5
    assert genai_service.should_fall_back(genai_service.AITimeoutError("timed out"))


def test_embeddings_have_deadline(monkeypatch):
    import time

    class SlowEmbed:
        def get_text_embedding_batch(self, texts):
            time.sleep(2)

    monkeypatch.setattr(genai_service, "Settings", SimpleNamespace(embed_model=SlowEmbed()))
    monkeypatch.setattr(genai_service, "EMBED_TIMEOUT_SECONDS", 0.2)
    try:
        genai_service.embed_texts(["x"])
        raise AssertionError("expected AITimeoutError")
    except genai_service.AITimeoutError:
        pass
