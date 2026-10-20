import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DEFAULT_SQLITE_PATH = Path(__file__).resolve().parent / "data" / "documind.db"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()

    if not url:
        DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"

    # Hosted providers often hand out postgres:// URLs; use the psycopg 3 driver.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]

    return url


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Only a SHA-256 hash of the bearer token is stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("owner_id", "sha256", name="uq_owner_document_hash"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(Integer, default=0)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    file: Mapped["DocumentFile"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentFile(Base):
    """The original PDF bytes, kept apart so listing documents never loads them."""

    __tablename__ = "document_files"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)

    document: Mapped[Document] = relationship(back_populates="file")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    page: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # float32 vector bytes (numpy.tobytes()).
    embedding: Mapped[bytes] = mapped_column(LargeBinary)

    document: Mapped[Document] = relationship(back_populates="chunks")


engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_db(url: str | None = None):
    """Create the engine and tables. Safe to call more than once."""

    global engine

    url = url or database_url()
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    return engine


def is_durable() -> bool:
    return engine is not None and engine.dialect.name != "sqlite"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
