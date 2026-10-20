from google.genai import errors

import genai_service
import main
from conftest import make_pdf


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
