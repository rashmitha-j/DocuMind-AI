from fastapi.testclient import TestClient
from google.genai import errors

import database
import genai_service
import main
from conftest import make_pdf, register, upload
from database import Chunk, Document, DocumentFile

SAMPLE_PAGES = [
    "Solar panel energy output depends on sunlight and panel angle.",
    "The invoice payment is due within thirty days of delivery.",
    "The contract warranty lasts five years and refund requests need a receipt.",
]


def only_result(response):
    assert response.status_code == 201, response.text
    [result] = response.json()["results"]
    return result


def test_upload_validation_errors(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_PAGES", 2)

    cases = {
        "notes.txt": (b"hello", "text/plain", 400),
        "fake.pdf": (b"this is not a pdf", "application/pdf", 400),
        "corrupt.pdf": (b"%PDF-1.4\n garbage", "application/pdf", 400),
        "empty.pdf": (b"", "application/pdf", 400),
        "wrongtype.pdf": (make_pdf(["x"]), "text/html", 415),
        "scanned.pdf": (make_pdf(["", ""]), "application/pdf", 422),
        "long.pdf": (make_pdf(SAMPLE_PAGES), "application/pdf", 413),
    }

    for name, (content, content_type, expected) in cases.items():
        result = only_result(upload(client, headers, [(name, content, content_type)]))
        assert result["ok"] is False, name
        assert result["status_code"] == expected, (name, result)

    assert "OCR" in only_result(upload(client, headers, [("s.pdf", make_pdf([""]))]))["error"]
    assert client.get("/documents", headers=headers).json()["documents"] == []


def test_file_too_large(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_FILE_SIZE", 100)
    result = only_result(upload(client, headers, [("big.pdf", make_pdf(SAMPLE_PAGES))]))
    assert result["status_code"] == 413


def test_request_body_limit(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(main, "MAX_FILES_PER_UPLOAD", 1)
    response = upload(client, headers, [("a.pdf", b"%PDF-" + b"x" * 2_000_000)])
    assert response.status_code == 413


def test_too_many_files(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_FILES_PER_UPLOAD", 2)
    response = upload(client, headers, [(f"{i}.pdf", make_pdf([f"doc {i}"])) for i in range(3)])
    assert response.status_code == 400


def test_multiple_uploads_and_library(client):
    headers = register(client)
    response = upload(
        client,
        headers,
        [("solar.pdf", make_pdf(SAMPLE_PAGES[:1])), ("bad.txt", b"x", "text/plain"), ("invoice.pdf", make_pdf(SAMPLE_PAGES[1:]))],
    )
    results = response.json()["results"]
    assert [r["ok"] for r in results] == [True, False, True]

    library = client.get("/documents", headers=headers).json()["documents"]
    assert {doc["filename"] for doc in library} == {"solar.pdf", "invoice.pdf"}
    assert all(doc["status"] == "ready" and doc["chunk_count"] >= 1 for doc in library)

    invoice = next(doc for doc in library if doc["filename"] == "invoice.pdf")
    assert invoice["pages"] == 2
    assert client.get(f"/documents/{invoice['id']}", headers=headers).json()["document"]["id"] == invoice["id"]


def test_duplicate_upload_reuses_embeddings(client, embedder):
    headers = register(client)
    pdf = make_pdf(SAMPLE_PAGES)

    first = only_result(upload(client, headers, [("a.pdf", pdf)]))
    embedded = embedder.embedded_texts
    second = only_result(upload(client, headers, [("renamed.pdf", pdf)]))

    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["document"]["id"] == first["document"]["id"]
    assert embedder.embedded_texts == embedded
    assert len(client.get("/documents", headers=headers).json()["documents"]) == 1


def test_document_limit_per_user(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 1)
    only_result(upload(client, headers, [("a.pdf", make_pdf(["Solar"]))]))
    result = only_result(upload(client, headers, [("b.pdf", make_pdf(["Invoice"]))]))
    assert result["ok"] is False and "up to 1" in result["error"]


def test_upload_rate_limit(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "UPLOAD_LIMIT", (1, 3600))
    upload(client, headers, [("a.pdf", make_pdf(["Solar"]))])
    response = upload(client, headers, [("b.pdf", make_pdf(["Invoice"]))])
    assert response.status_code == 429


def test_download_returns_original_pdf(client):
    headers = register(client)
    pdf = make_pdf(SAMPLE_PAGES)
    doc = only_result(upload(client, headers, [("report.pdf", pdf)]))["document"]

    response = client.get(f"/documents/{doc['id']}/file", headers=headers)
    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"


def test_users_cannot_access_each_others_documents(client):
    alice = register(client, "alice@example.com")
    bob = register(client, "bob@example.com")
    doc = only_result(upload(client, alice, [("private.pdf", make_pdf(SAMPLE_PAGES))]))["document"]

    assert client.get("/documents", headers=bob).json()["documents"] == []
    for method, path in [
        ("get", f"/documents/{doc['id']}"),
        ("get", f"/documents/{doc['id']}/file"),
        ("post", f"/documents/{doc['id']}/reindex"),
        ("delete", f"/documents/{doc['id']}"),
    ]:
        assert getattr(client, method)(path, headers=bob).status_code == 404, path

    chat = client.post("/chat", headers=bob, json={"document_ids": [doc["id"]], "question": "invoice?"})
    assert chat.status_code == 404
    assert "private.pdf" not in chat.text and "invoice" not in chat.text.lower().replace("invoice?", "")

    # Alice still has her document.
    assert client.get(f"/documents/{doc['id']}", headers=alice).status_code == 200


def test_same_file_for_two_users_is_stored_separately(client):
    alice = register(client, "alice@example.com")
    bob = register(client, "bob@example.com")
    pdf = make_pdf(SAMPLE_PAGES)
    a = only_result(upload(client, alice, [("a.pdf", pdf)]))["document"]
    b = only_result(upload(client, bob, [("b.pdf", pdf)]))["document"]
    assert a["id"] != b["id"]

    client.delete(f"/documents/{a['id']}", headers=alice)
    assert client.get(f"/documents/{b['id']}", headers=bob).status_code == 200


def test_delete_removes_file_chunks_and_retrieval(client):
    headers = register(client)
    doc = only_result(upload(client, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))["document"]

    assert client.delete(f"/documents/{doc['id']}", headers=headers).status_code == 204

    with database.SessionLocal() as db:
        assert db.query(Document).count() == 0
        assert db.query(DocumentFile).count() == 0
        assert db.query(Chunk).count() == 0

    chat = client.post("/chat", headers=headers, json={"document_ids": [doc["id"]], "question": "invoice?"})
    assert chat.status_code == 404
    assert client.get(f"/documents/{doc['id']}/file", headers=headers).status_code == 404


def test_failed_indexing_is_recorded_and_can_be_retried(client, monkeypatch):
    headers = register(client)
    original = main.embed_texts

    def quota(_texts):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "Quota", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(main, "embed_texts", quota)
    result = only_result(upload(client, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))
    assert result["ok"] is False and result["status_code"] == 429

    [doc] = client.get("/documents", headers=headers).json()["documents"]
    assert doc["status"] == "failed" and "quota" in doc["error"].lower()

    chat = client.post("/chat", headers=headers, json={"document_ids": [doc["id"]], "question": "invoice?"})
    assert chat.status_code == 409

    monkeypatch.setattr(main, "embed_texts", original)
    retry = client.post(f"/documents/{doc['id']}/reindex", headers=headers)
    assert retry.status_code == 200
    assert retry.json()["document"]["status"] == "ready"


def test_missing_api_key_marks_document_failed(client, monkeypatch):
    headers = register(client)
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.setattr(genai_service, "_configured", False)
    result = only_result(upload(client, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))
    assert result["status_code"] == 503
    assert "GOOGLE_API_KEY" in result["error"]
    assert "test-key" not in str(result)


def test_persistence_across_restart(client, embedder):
    """Upload, ask, restart the app process state, ask again, then delete."""

    headers = register(client)
    doc = only_result(upload(client, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))["document"]
    question = {"document_ids": [doc["id"]], "question": "When is the invoice payment due?"}
    before = client.post("/chat", headers=headers, json=question).json()
    embedded = embedder.embedded_texts

    # Simulate a backend restart: drop the engine and start a new app lifespan
    # against the same database file.
    database.engine.dispose()
    database.engine = None
    with TestClient(main.app) as restarted:
        assert restarted.get("/auth/me", headers=headers).status_code == 200
        [stored] = restarted.get("/documents", headers=headers).json()["documents"]
        assert stored["status"] == "ready"

        after = restarted.post("/chat", headers=headers, json=question).json()
        assert after["sources"] == before["sources"]
        assert embedder.embedded_texts == embedded  # no document re-embedding

        # Re-uploading the unchanged file after the restart reuses the index too.
        again = only_result(upload(restarted, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))
        assert again["duplicate"] is True
        assert embedder.embedded_texts == embedded

        assert restarted.delete(f"/documents/{doc['id']}", headers=headers).status_code == 204
        assert restarted.post("/chat", headers=headers, json=question).status_code == 404


def test_interrupted_processing_is_recovered_on_startup(client):
    headers = register(client)
    doc = only_result(upload(client, headers, [("a.pdf", make_pdf(SAMPLE_PAGES))]))["document"]

    with database.SessionLocal() as db:
        db.query(Document).update({"status": "processing"})
        db.commit()

    database.engine.dispose()
    with TestClient(main.app) as restarted:
        stored = restarted.get(f"/documents/{doc['id']}", headers=headers).json()["document"]
        assert stored["status"] == "failed"
        assert "interrupted" in stored["error"]
        assert restarted.post(f"/documents/{doc['id']}/reindex", headers=headers).json()["document"]["status"] == "ready"
