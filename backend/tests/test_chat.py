from google.genai import errors
from llama_index.core import Settings

import main
from conftest import make_pdf, register, upload

SOLAR = ["Solar panel energy output depends on sunlight.", "Battery storage keeps energy for the night."]
CONTRACT = ["The invoice payment is due within thirty days.", "The contract warranty lasts five years."]


def setup_documents(client):
    headers = register(client)
    response = upload(client, headers, [("solar.pdf", make_pdf(SOLAR)), ("contract.pdf", make_pdf(CONTRACT))])
    ids = {r["document"]["filename"]: r["document"]["id"] for r in response.json()["results"]}
    return headers, ids


def ask(client, headers, ids, question):
    return client.post("/chat", headers=headers, json={"document_ids": ids, "question": question})


def test_citation_points_to_correct_document_and_page(client):
    headers, ids = setup_documents(client)
    body = ask(client, headers, [ids["contract.pdf"]], "How many years does the warranty last?").json()

    top = body["sources"][0]
    assert top["filename"] == "contract.pdf"
    assert top["document_id"] == ids["contract.pdf"]
    assert top["page"] == 2
    assert top["label"] == "S1"
    assert "warranty lasts five years" in top["excerpt"]
    assert isinstance(top["chunk_id"], int)
    assert body["answer"]


def test_retrieval_is_limited_to_selected_documents(client):
    headers, ids = setup_documents(client)
    body = ask(client, headers, [ids["solar.pdf"]], "When is the invoice payment due?").json()
    assert {source["filename"] for source in body["sources"]} == {"solar.pdf"}


def test_multi_document_question(client):
    headers, ids = setup_documents(client)
    body = ask(client, headers, list(ids.values()), "battery storage and invoice payment").json()
    assert {source["filename"] for source in body["sources"]} == {"solar.pdf", "contract.pdf"}
    pages = {(s["filename"], s["page"]) for s in body["sources"][:2]}
    assert pages == {("solar.pdf", 2), ("contract.pdf", 1)}


def test_prompt_contains_only_retrieved_sources(client, monkeypatch):
    headers, ids = setup_documents(client)
    prompts = []
    original = Settings.llm.complete

    def capture(prompt, **kwargs):
        prompts.append(prompt)
        return original(prompt, **kwargs)

    monkeypatch.setattr(type(Settings.llm), "complete", lambda self, prompt, **kw: capture(prompt, **kw))
    ask(client, headers, [ids["contract.pdf"]], "warranty years")

    [prompt] = prompts
    assert "[S1] contract.pdf, page 2" in prompt
    assert "Solar" not in prompt
    assert "do not provide enough information" in prompt


def test_unknown_and_mixed_ownership_ids(client):
    headers, ids = setup_documents(client)
    bob = register(client, "bob@example.com")
    bob_doc = upload(client, bob, [("bob.pdf", make_pdf(["Refund policy"]))]).json()["results"][0]["document"]

    assert ask(client, headers, ["missing"], "hi").status_code == 404
    # A valid own ID combined with another user's ID is rejected entirely.
    assert ask(client, headers, [ids["solar.pdf"], bob_doc["id"]], "refund").status_code == 404


def test_blank_question_and_validation(client):
    headers, ids = setup_documents(client)
    assert ask(client, headers, [ids["solar.pdf"]], "   ").status_code == 400
    assert ask(client, headers, [], "hello").status_code == 422
    assert ask(client, headers, [ids["solar.pdf"]], "x" * 2001).status_code == 422


def test_quota_error_during_chat(client, monkeypatch):
    headers, ids = setup_documents(client)

    def quota(*_):
        raise errors.ClientError(429, {"error": {"code": 429, "message": "Quota", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(main, "generate_answer", quota)
    response = ask(client, headers, [ids["solar.pdf"]], "Summary?")
    assert response.status_code == 429
    assert "quota" in response.json()["detail"].lower()


def test_invalid_key_during_chat(client, monkeypatch):
    headers, ids = setup_documents(client)

    def invalid(*_):
        raise errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})

    monkeypatch.setattr(main, "embed_query", invalid)
    response = ask(client, headers, [ids["solar.pdf"]], "Summary?")
    assert response.status_code == 502
    assert "test-key-not-real" not in response.text


def test_timeout_during_chat(client, monkeypatch):
    headers, ids = setup_documents(client)

    def timeout(*_):
        raise TimeoutError("Request timed out")

    monkeypatch.setattr(main, "generate_answer", timeout)
    assert ask(client, headers, [ids["solar.pdf"]], "Summary?").status_code == 504


def test_embedding_model_change_requires_reindex(client, monkeypatch):
    headers, ids = setup_documents(client)
    monkeypatch.setattr(main, "embedding_model_id", lambda: "other-model:768")
    response = ask(client, headers, [ids["solar.pdf"]], "solar?")
    assert response.status_code == 409
    assert "different embedding model" in response.json()["detail"]


def test_chat_rate_limit(client, monkeypatch):
    headers, ids = setup_documents(client)
    monkeypatch.setattr(main, "CHAT_LIMIT", (2, 600))
    codes = [ask(client, headers, [ids["solar.pdf"]], "solar?").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
