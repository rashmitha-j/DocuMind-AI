import hashlib
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import AuthSession, User, get_db, utcnow

SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").lower() == "true"

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)

# Used to keep login timing similar for unknown emails.
_DUMMY_HASH = _hasher.hash("documind-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(hours=SESSION_TTL_HOURS),
    )
    db.add(session)

    # Opportunistically remove this user's expired sessions.
    db.query(AuthSession).filter(
        AuthSession.user_id == user.id, AuthSession.expires_at < utcnow()
    ).delete(synchronize_session=False)

    db.commit()
    return token, session


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthSession:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Please sign in to continue.")

    session = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == hash_token(credentials.credentials))
        .one_or_none()
    )

    if session is None:
        raise _unauthorized("Your session is invalid. Please sign in again.")

    expires_at = session.expires_at
    if expires_at.tzinfo is None:  # SQLite returns naive datetimes
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)

    if expires_at < utcnow():
        db.delete(session)
        db.commit()
        raise _unauthorized("Your session has expired. Please sign in again.")

    return session


def get_current_user(session: AuthSession = Depends(get_current_session)) -> User:
    return session.user


def client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding-window limiter kept in process memory.

    Suitable for a single backend instance (Render's free tier). Multiple
    instances would each keep their own counters.
    """

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int):
        now = time.monotonic()

        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()

            if len(hits) >= limit:
                retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests. Please try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)},
                )

            hits.append(now)

    def reset(self):
        with self._lock:
            self._hits.clear()


def parse_limit(name: str, default: str) -> tuple[int, int]:
    """Read a 'count/seconds' limit such as '10/60' from the environment."""

    count, seconds = os.getenv(name, default).split("/")
    return int(count), int(seconds)


limiter = RateLimiter()
