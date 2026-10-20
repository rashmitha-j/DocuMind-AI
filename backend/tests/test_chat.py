from google.genai import errors
import genai_service
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

    def capture(model, prompt):
        prompts.append(prompt)
        return "captured"

    monkeypatch.setattr(genai_service, "complete", capture)
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


def overloaded_error():
    return errors.ServerError(
        503,
        {"error": {"code": 503, "message": "This model is currently experiencing high demand.", "status": "UNAVAILABLE"}},
    )


def test_overloaded_model_falls_back(client, monkeypatch):
    headers, ids = setup_documents(client)
    calls = []

    def flaky(model, prompt, timeout_seconds=None):
        calls.append(model)
        if model == genai_service.chat_model():
            raise overloaded_error()
        return "fallback answer"

    monkeypatch.setattr(genai_service, "complete", flaky)
    body = ask(client, headers, [ids["contract.pdf"]], "warranty years").json()

    assert calls == [genai_service.chat_model(), genai_service.FALLBACK_MODEL]
    assert body["answer"] == "fallback answer"
    assert body["model"] == genai_service.FALLBACK_MODEL
    assert body["sources"][0]["filename"] == "contract.pdf"


def test_timeout_falls_back_with_longer_timeout(client, monkeypatch):
    headers, ids = setup_documents(client)
    calls = []

    def slow_then_ok(model, prompt, timeout_seconds=genai_service.LLM_TIMEOUT_SECONDS):
        calls.append((model, timeout_seconds))
        if model == genai_service.chat_model():
            raise TimeoutError("The read operation timed out")
        return "fallback answer"

    monkeypatch.setattr(genai_service, "complete", slow_then_ok)
    body = ask(client, headers, [ids["contract.pdf"]], "warranty years").json()

    assert body["model"] == genai_service.FALLBACK_MODEL
    assert calls[1] == (genai_service.FALLBACK_MODEL, genai_service.FALLBACK_TIMEOUT_SECONDS)


def test_overload_without_fallback_reports_503(client, monkeypatch):
    headers, ids = setup_documents(client)

    def always_overloaded(model, prompt, timeout_seconds=None):
        raise overloaded_error()

    monkeypatch.setattr(genai_service, "complete", always_overloaded)
    monkeypatch.setattr(genai_service, "FALLBACK_MODEL", "")

    response = ask(client, headers, [ids["contract.pdf"]], "warranty years")
    assert response.status_code == 503
    assert "overloaded" in response.json()["detail"]


def test_quota_error_does_not_use_fallback(client, monkeypatch):
    headers, ids = setup_documents(client)
    calls = []

    def quota(model, prompt, timeout_seconds=None):
        calls.append(model)
        raise errors.ClientError(429, {"error": {"code": 429, "message": "Quota", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(genai_service, "complete", quota)
    assert ask(client, headers, [ids["contract.pdf"]], "warranty years").status_code == 429
    assert calls == [genai_service.chat_model()]


def test_answer_reports_model(client):
    headers, ids = setup_documents(client)
    body = ask(client, headers, [ids["solar.pdf"]], "solar?").json()
    assert body["model"] == genai_service.chat_model()
    assert body["answer"] == f"Mock answer from {genai_service.chat_model()}."
