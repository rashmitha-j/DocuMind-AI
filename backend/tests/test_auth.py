from datetime import timedelta

import database
import main
from conftest import register
from database import AuthSession, User, utcnow
from security import limiter


def test_health_reports_storage_without_ai(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY")
    body = client.get("/health").json()
    assert body == {
        "status": "healthy",
        "ai_configured": False,
        "database": "sqlite",
        "durable_storage": False,
    }


def test_register_login_me_logout(client):
    headers = register(client)
    assert client.get("/auth/me", headers=headers).json()["user"]["email"] == "alice@example.com"

    login = client.post("/auth/login", json={"email": "ALICE@example.com ", "password": "correct-horse-1"})
    assert login.status_code == 200
    second = {"Authorization": f"Bearer {login.json()['token']}"}

    assert client.post("/auth/logout", headers=second).status_code == 204
    assert client.get("/auth/me", headers=second).status_code == 401
    # The first session is still valid.
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_passwords_are_hashed(client):
    register(client)
    with database.SessionLocal() as db:
        user = db.query(User).one()
        assert "correct-horse-1" not in user.password_hash
        assert user.password_hash.startswith("$argon2")
        session = db.query(AuthSession).one()
        assert len(session.token_hash) == 64


def test_duplicate_email_rejected(client):
    register(client)
    response = client.post("/auth/register", json={"email": "alice@example.com", "password": "another-pass-1"})
    assert response.status_code == 409


def test_invalid_email_and_short_password(client):
    assert client.post("/auth/register", json={"email": "nope", "password": "longenough1"}).status_code == 400
    assert client.post("/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_wrong_password_and_unknown_user_look_the_same(client):
    register(client)
    wrong = client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-password"})
    unknown = client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_protected_routes_require_token(client):
    assert client.get("/documents").status_code == 401
    assert client.post("/chat", json={"document_ids": ["x"], "question": "hi"}).status_code == 401
    assert client.get("/documents", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_expired_session_rejected(client):
    headers = register(client)
    with database.SessionLocal() as db:
        db.query(AuthSession).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "AUTH_LIMIT", (3, 60))
    limiter.reset()
    codes = [
        client.post("/auth/login", json={"email": "x@example.com", "password": "whatever-123"}).status_code
        for _ in range(4)
    ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429


def test_rate_limit_sets_retry_after(client, monkeypatch):
    monkeypatch.setattr(main, "AUTH_LIMIT", (1, 60))
    limiter.reset()
    client.post("/auth/login", json={"email": "x@example.com", "password": "whatever-123"})
    response = client.post("/auth/login", json={"email": "x@example.com", "password": "whatever-123"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_registration_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(main, "ALLOW_REGISTRATION", False)
    response = client.post("/auth/register", json={"email": "a@example.com", "password": "longenough1"})
    assert response.status_code == 403


def test_cors_allows_production_frontend_with_auth_header(client):
    response = client.options(
        "/documents",
        headers={
            "Origin": "https://docu-mind-ai-delta.vercel.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.headers["access-control-allow-origin"] == "https://docu-mind-ai-delta.vercel.app"


def test_cors_rejects_unknown_origin(client):
    response = client.options(
        "/documents",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers
