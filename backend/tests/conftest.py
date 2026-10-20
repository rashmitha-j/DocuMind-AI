import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from llama_index.core import Settings  # noqa: E402
from llama_index.core.embeddings import BaseEmbedding  # noqa: E402
from llama_index.core.llms import MockLLM  # noqa: E402

import database  # noqa: E402
import genai_service  # noqa: E402
import main  # noqa: E402
from security import limiter  # noqa: E402

VOCABULARY = [
    "solar", "panel", "energy", "battery", "storage", "invoice",
    "payment", "due", "contract", "warranty", "years", "refund",
]


class KeywordEmbedding(BaseEmbedding):
    """Deterministic offline embedding that counts vocabulary words.

    It also records how many texts were embedded, so tests can prove that
    unchanged documents are not embedded again.
    """

    embedded_texts: int = 0

    def _vector(self, text: str) -> list[float]:
        words = re.findall(r"[a-z]+", text.lower())
        vector = [float(words.count(term)) for term in VOCABULARY]
        return vector if any(vector) else [0.01] * len(VOCABULARY)

    def _get_text_embedding(self, text: str) -> list[float]:
        self.embedded_texts += 1
        return self._vector(text)

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)


def make_pdf(pages: list[str]) -> bytes:
    """Build a small valid PDF whose pages contain the given text lines."""

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None]
    kids = []

    for text in pages:
        page_id = len(objects) + 1
        content_id = page_id + 1
        kids.append(f"{page_id} 0 R")
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {{font}} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    font_id = len(objects) + 1
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    objects = [obj.replace(b"{font}", str(font_id).encode()) for obj in objects]

    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + obj + b"\nendobj\n"

    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(output)


@pytest.fixture
def embedder():
    return Settings.embed_model


@pytest.fixture(autouse=True)
def offline_app(monkeypatch, tmp_path):
    """Fresh SQLite database and fake Gemini models: no test can call the real API."""

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setattr(genai_service, "_configured", True)
    Settings.embed_model = KeywordEmbedding()
    Settings.llm = MockLLM(max_tokens=20)
    limiter.reset()
    yield
    if database.engine is not None:
        database.engine.dispose()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def register(client, email="alice@example.com", password="correct-horse-1"):
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def upload(client, headers, files):
    """files: list of (filename, bytes) or (filename, bytes, content_type)."""

    payload = [
        ("files", (item[0], item[1], item[2] if len(item) > 2 else "application/pdf"))
        for item in files
    ]
    return client.post("/documents", headers=headers, files=payload)
