from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.requests import Request

import main
import security
from conftest import make_pdf, register, upload
from security import limiter


def result_of(response):
    assert response.status_code == 201, response.text
    [result] = response.json()["results"]
    return result


def test_document_count_limit_and_usage(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 2)

    for i in range(2):
        assert result_of(upload(client, headers, [(f"{i}.pdf", make_pdf([f"Solar doc {i}"]))]))["ok"]

    rejected = result_of(upload(client, headers, [("2.pdf", make_pdf(["Invoice doc"]))]))
    assert rejected["ok"] is False and rejected["status_code"] == 403

    usage = client.get("/documents", headers=headers).json()["usage"]
    assert usage["documents"] == 2 and usage["max_documents"] == 2
    assert usage["bytes"] > 0


def test_storage_quota(client, monkeypatch):
    headers = register(client)
    first = make_pdf(["Solar panel energy"])
    monkeypatch.setattr(main, "MAX_STORAGE_PER_USER", len(first) + 10)

    assert result_of(upload(client, headers, [("a.pdf", first)]))["ok"]
    rejected = result_of(upload(client, headers, [("b.pdf", make_pdf(["Invoice payment due"]))]))
    assert rejected["status_code"] == 403
    assert "storage limit" in rejected["error"]


def test_over_quota_file_is_not_parsed_or_embedded(client, monkeypatch, embedder):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 0)
    parsed = []
    monkeypatch.setattr(main, "extract_pages", lambda content: parsed.append(1) or ["x"])

    result = result_of(upload(client, headers, [("a.pdf", make_pdf(["Solar"]))]))
    assert result["status_code"] == 403
    assert parsed == [] and embedder.embedded_texts == 0


def test_duplicate_of_existing_file_does_not_count_against_quota(client, monkeypatch):
    headers = register(client)
    pdf = make_pdf(["Solar panel energy"])
    assert result_of(upload(client, headers, [("a.pdf", pdf)]))["ok"]

    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 1)
    again = result_of(upload(client, headers, [("a.pdf", pdf)]))
    assert again["ok"] and again["duplicate"]


def test_deleting_frees_quota(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 1)
    doc = result_of(upload(client, headers, [("a.pdf", make_pdf(["Solar"]))]))["document"]

    assert client.delete(f"/documents/{doc['id']}", headers=headers).status_code == 204
    assert client.get("/documents", headers=headers).json()["usage"]["bytes"] == 0
    assert result_of(upload(client, headers, [("b.pdf", make_pdf(["Invoice"]))]))["ok"]


def test_quota_is_per_user(client, monkeypatch):
    alice = register(client, "alice@example.com")
    bob = register(client, "bob@example.com")
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 1)

    assert result_of(upload(client, alice, [("a.pdf", make_pdf(["Solar"]))]))["ok"]
    assert result_of(upload(client, bob, [("b.pdf", make_pdf(["Invoice"]))]))["ok"]


def test_concurrent_uploads_cannot_exceed_limit(client, monkeypatch):
    headers = register(client)
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 2)
    pdfs = [make_pdf([f"Solar energy report number {i}"]) for i in range(6)]

    def send(i):
        return result_of(upload(client, headers, [(f"{i}.pdf", pdfs[i])]))

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(send, range(6)))

    assert sum(1 for r in results if r["ok"]) == 2
    assert all(r["status_code"] == 403 for r in results if not r["ok"])
    assert client.get("/documents", headers=headers).json()["usage"]["documents"] == 2


# ---------- client IP extraction ----------


def make_request(forwarded=None, peer="10.0.0.5"):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


@pytest.mark.parametrize(
    ("hops", "forwarded", "expected"),
    [
        (1, None, "10.0.0.5"),  # no header: socket peer
        (1, "", "10.0.0.5"),  # empty header
        (1, "203.0.113.7", "203.0.113.7"),  # header set by the trusted proxy
        (1, "1.1.1.1, 203.0.113.7", "203.0.113.7"),  # forged left entry is ignored
        (1, "1.1.1.1, 2.2.2.2, 203.0.113.7", "203.0.113.7"),
        (1, "not-an-ip", "10.0.0.5"),  # malformed: socket peer
        (1, "1.1.1.1, garbage", "10.0.0.5"),
        (2, "1.1.1.1, 203.0.113.7, 198.51.100.2", "203.0.113.7"),  # two proxies
        (2, "203.0.113.7", "10.0.0.5"),  # fewer entries than hops
        (0, "1.1.1.1", "10.0.0.5"),  # header ignored entirely
        (1, "2001:db8::1", "2001:db8::1"),
    ],
)
def test_client_ip(monkeypatch, hops, forwarded, expected):
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", hops)
    assert security.client_ip(make_request(forwarded)) == expected


def test_forged_forwarded_for_cannot_bypass_auth_limit(client, monkeypatch):
    monkeypatch.setattr(main, "AUTH_LIMIT", (3, 60))
    limiter.reset()

    # The trusted proxy always appends the real address last; the client
    # rotates a forged first entry and a new email on every attempt.
    codes = [
        client.post(
            "/auth/login",
            json={"email": f"user{i}@example.com", "password": "wrong-password"},
            headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.7"},
        ).status_code
        for i in range(4)
    ]
    assert codes == [401, 401, 401, 429]


def test_global_registration_limit(client, monkeypatch):
    monkeypatch.setattr(main, "REGISTER_GLOBAL_LIMIT", (2, 3600))
    monkeypatch.setattr(main, "AUTH_LIMIT", (100, 60))
    limiter.reset()

    codes = [
        client.post(
            "/auth/register",
            json={"email": f"new{i}@example.com", "password": "long-enough-1"},
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
        ).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]
