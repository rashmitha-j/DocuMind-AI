import hashlib
import logging
import os
import re
import threading
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

# Load local .env when running on your own computer.
# Render uses its Environment Variables settings.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import database  # noqa: E402
from database import AuthSession, Chunk, Document, DocumentFile, User, get_db  # noqa: E402
from genai_service import (  # noqa: E402
    classify_ai_error,
    embed_query,
    embed_texts,
    embedding_model_id,
    generate_answer,
    rank_chunks,
    split_pages,
)
from security import (  # noqa: E402
    client_ip,
    create_session,
    get_current_session,
    get_current_user,
    hash_password,
    limiter,
    parse_limit,
    verify_password,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("documind")

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "10")) * 1024 * 1024
MAX_FILES_PER_UPLOAD = int(os.getenv("MAX_FILES_PER_UPLOAD", "5"))
MAX_PAGES = int(os.getenv("MAX_PDF_PAGES", "300"))
MAX_DOCUMENTS_PER_USER = int(os.getenv("MAX_DOCUMENTS_PER_USER", "10"))
# Total original-PDF bytes per account, so one user cannot fill the free database.
MAX_STORAGE_PER_USER = int(os.getenv("MAX_STORAGE_MB_PER_USER", "50")) * 1024 * 1024
MAX_CHAT_DOCUMENTS = 10
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").lower() == "true"

AUTH_LIMIT = parse_limit("RATE_LIMIT_AUTH", "10/60")
# Caps sign-ups across all clients, so forged client addresses cannot mass-register.
REGISTER_GLOBAL_LIMIT = parse_limit("RATE_LIMIT_REGISTER_GLOBAL", "30/3600")
UPLOAD_LIMIT = parse_limit("RATE_LIMIT_UPLOAD", "10/3600")
CHAT_LIMIT = parse_limit("RATE_LIMIT_CHAT", "20/600")
REINDEX_LIMIT = parse_limit("RATE_LIMIT_REINDEX", "10/3600")

ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_ORIGINS = [
    "https://docu-mind-ai-delta.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
EXTRA_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]


def recover_interrupted_documents():
    """Mark documents left in 'processing' by a crash or restart as failed."""

    with database.SessionLocal() as db:
        count = (
            db.query(Document)
            .filter(Document.status == "processing")
            .update(
                {
                    "status": "failed",
                    "error": "Processing was interrupted. Use Retry to process it again.",
                },
                synchronize_session=False,
            )
        )
        db.commit()

    if count:
        logger.warning("Marked %d interrupted document(s) as failed", count)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # No Gemini calls happen at startup.
    database.init_db()
    recover_interrupted_documents()
    yield


app = FastAPI(
    title="DocuMind AI API",
    description="Upload PDFs and ask questions about their content",
    version="3.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEFAULT_ORIGINS + EXTRA_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Reject oversized bodies before they are parsed."""

    limit = MAX_FILES_PER_UPLOAD * MAX_FILE_SIZE + 1024 * 1024
    length = request.headers.get("content-length")

    if length and length.isdigit() and int(length) > limit:
        return JSONResponse(status_code=413, content={"detail": "Request is too large."})

    return await call_next(request)


# ---------- Schemas ----------


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)


class ChatRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=MAX_CHAT_DOCUMENTS)
    question: str = Field(max_length=2000)


def serialize_user(user: User) -> dict:
    return {"id": user.id, "email": user.email}


def serialize_document(document: Document) -> dict:
    return {
        "id": document.id,
        "filename": document.filename,
        "size_bytes": document.size_bytes,
        "pages": document.pages,
        "characters": document.characters,
        "chunk_count": document.chunk_count,
        "status": document.status,
        "error": document.error,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


# ---------- PDF processing ----------


def clean_text(text: str) -> str:
    """Normalise whitespace in text extracted from a PDF page."""

    text = text.replace("\x00", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def safe_filename(name: str | None) -> str:
    """Display name only; files are stored by generated ID, never by this name."""

    name = Path((name or "document.pdf").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip() or "document.pdf"
    return name[-200:]


def extract_pages(content: bytes) -> list[str]:
    """Return the cleaned text of every page, raising HTTPException on bad PDFs."""

    if not content.lstrip()[:5].startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="This file is not a valid PDF.")

    try:
        reader = PdfReader(BytesIO(content))

        if reader.is_encrypted and not reader.decrypt(""):
            raise HTTPException(
                status_code=400,
                detail="This PDF is password-protected. Please upload an unlocked copy.",
            )

        if len(reader.pages) > MAX_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"This PDF has more than {MAX_PAGES} pages. Please upload a shorter document.",
            )

        return [clean_text(page.extract_text() or "") for page in reader.pages]

    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("PDF reading error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Could not read this PDF. It may be corrupted. Please try another file.",
        )


def index_document(db: Session, document: Document, pages: list[str]):
    """Chunk and embed a stored document, replacing any previous chunks."""

    chunks = split_pages(pages)

    try:
        vectors = embed_texts([chunk["text"] for chunk in chunks])
    except Exception as exc:
        status, detail = classify_ai_error(exc)
        logger.error("Embedding error for document %s: %s", document.id, type(exc).__name__)
        document.status = "failed"
        document.error = detail
        db.commit()
        raise HTTPException(status_code=status, detail=f"Indexing failed. {detail}")

    db.query(Chunk).filter(Chunk.document_id == document.id).delete(synchronize_session=False)
    db.add_all(
        Chunk(
            document_id=document.id,
            owner_id=document.owner_id,
            page=chunk["page"],
            chunk_index=chunk["chunk_index"],
            text=chunk["text"],
            embedding=vector,
        )
        for chunk, vector in zip(chunks, vectors)
    )
    document.status = "ready"
    document.error = None
    document.chunk_count = len(chunks)
    document.embedding_model = embedding_model_id()
    db.commit()


# Serialises quota checks per user within this process; the row lock below does
# the same across processes on PostgreSQL.
_user_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def storage_usage(db: Session, user_id: int) -> tuple[int, int]:
    """Return (document count, total PDF bytes) for a user."""

    count, total = (
        db.query(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0))
        .filter(Document.owner_id == user_id)
        .one()
    )
    return int(count), int(total)


def check_quota(db: Session, user_id: int, new_bytes: int):
    count, total = storage_usage(db, user_id)

    if count >= MAX_DOCUMENTS_PER_USER:
        raise HTTPException(
            status_code=403,
            detail=f"You can store up to {MAX_DOCUMENTS_PER_USER} documents. Delete some to upload more.",
        )

    if total + new_bytes > MAX_STORAGE_PER_USER:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This upload would exceed your {MAX_STORAGE_PER_USER // (1024 * 1024)} MB storage limit. "
                "Delete some documents to free space."
            ),
        )


def usage_summary(db: Session, user_id: int) -> dict:
    count, total = storage_usage(db, user_id)
    return {
        "documents": count,
        "max_documents": MAX_DOCUMENTS_PER_USER,
        "bytes": total,
        "max_bytes": MAX_STORAGE_PER_USER,
    }


def process_upload(db: Session, user: User, upload: UploadFile) -> dict:
    filename = safe_filename(upload.filename)

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Only PDF files (application/pdf) are accepted.")

    content = upload.file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)} MB.",
        )

    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    content_hash = hashlib.sha256(content).hexdigest()
    is_known = (
        db.query(Document.id)
        .filter(Document.owner_id == user.id, Document.sha256 == content_hash)
        .first()
        is not None
    )

    # Cheap early check so over-quota files are never parsed or embedded.
    if not is_known:
        check_quota(db, user.id, len(content))

    pages = extract_pages(content)
    characters = sum(len(page) for page in pages)

    if characters == 0:
        raise HTTPException(
            status_code=422,
            detail=(
                "No extractable text found. This looks like a scanned or image-only PDF, "
                "which needs OCR before it can be used."
            ),
        )

    document = (
        db.query(Document)
        .filter(Document.owner_id == user.id, Document.sha256 == content_hash)
        .one_or_none()
    )

    if document is not None and document.status == "ready" and document.embedding_model == embedding_model_id():
        # Same file already indexed for this user: reuse it, no new embeddings.
        return {"document": serialize_document(document), "duplicate": True}

    if document is None:
        with _user_locks[user.id]:
            # Authoritative check and insert in one transaction. FOR UPDATE on
            # the user row blocks concurrent uploads by the same user on
            # PostgreSQL (SQLite ignores it; the process lock covers it there).
            db.query(User).filter(User.id == user.id).with_for_update().one()
            check_quota(db, user.id, len(content))

            document = Document(
                id=uuid.uuid4().hex,
                owner_id=user.id,
                filename=filename,
                content_type="application/pdf",
                size_bytes=len(content),
                sha256=content_hash,
                pages=len(pages),
                characters=characters,
                status="processing",
            )
            document.file = DocumentFile(data=content)
            db.add(document)

            try:
                # Store the PDF and its metadata before the slower embedding step.
                db.commit()
            except IntegrityError:
                # The same file was stored by a concurrent request.
                db.rollback()
                raise HTTPException(status_code=409, detail="This file is already being uploaded.")
    else:
        document.status = "processing"
        document.error = None
        db.commit()

    index_document(db, document, pages)
    return {"document": serialize_document(document), "duplicate": False}


def get_owned_document(db: Session, user: User, document_id: str) -> Document:
    """Look up a document by ID, but only among the caller's own documents."""

    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.owner_id == user.id)
        .one_or_none()
    )

    if document is None:
        # Same response whether the document is missing or belongs to someone else.
        raise HTTPException(status_code=404, detail="Document not found.")

    return document


# ---------- Routes ----------


@app.get("/")
def home():
    return {
        "message": "DocuMind AI backend is running!",
        "status": "success",
        "provider": "Google Gemini",
        "docs": "/docs",
    }


@app.get("/health")
def health_check():
    # Never calls Gemini, so health checks cost no API quota.
    return {
        "status": "healthy",
        "ai_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "database": database.engine.dialect.name if database.engine else None,
        "durable_storage": database.is_durable(),
    }


@app.post("/auth/register", status_code=201)
def register(credentials: Credentials, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"auth:ip:{client_ip(request)}", *AUTH_LIMIT)
    limiter.check("auth:register:all", *REGISTER_GLOBAL_LIMIT)

    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is currently disabled.")

    email = credentials.email.strip().lower()

    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    if db.query(User).filter(User.email == email).one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(email=email, password_hash=hash_password(credentials.password))
    db.add(user)
    db.commit()

    token, session = create_session(db, user)
    return {"token": token, "expires_at": session.expires_at.isoformat(), "user": serialize_user(user)}


@app.post("/auth/login")
def login(credentials: Credentials, request: Request, db: Session = Depends(get_db)):
    email = credentials.email.strip().lower()
    limiter.check(f"auth:ip:{client_ip(request)}", *AUTH_LIMIT)
    limiter.check(f"auth:email:{email}", *AUTH_LIMIT)

    user = db.query(User).filter(User.email == email).one_or_none()

    if not verify_password(user.password_hash if user else None, credentials.password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token, session = create_session(db, user)
    return {"token": token, "expires_at": session.expires_at.isoformat(), "user": serialize_user(user)}


@app.post("/auth/logout", status_code=204)
def logout(session: AuthSession = Depends(get_current_session), db: Session = Depends(get_db)):
    db.query(AuthSession).filter(AuthSession.id == session.id).delete()
    db.commit()
    return Response(status_code=204)


@app.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"user": serialize_user(user)}


@app.get("/documents")
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    documents = (
        db.query(Document)
        .filter(Document.owner_id == user.id)
        .order_by(Document.created_at.desc())
        .all()
    )
    return {
        "documents": [serialize_document(document) for document in documents],
        "usage": usage_summary(db, user.id),
    }


@app.post("/documents", status_code=201)
def upload_documents(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    limiter.check(f"upload:{user.id}", *UPLOAD_LIMIT)

    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Upload at most {MAX_FILES_PER_UPLOAD} files at a time.",
        )

    results = []
    for upload in files:
        try:
            result = process_upload(db, user, upload)
            results.append({"filename": safe_filename(upload.filename), "ok": True, **result})
        except HTTPException as exc:
            db.rollback()
            results.append(
                {
                    "filename": safe_filename(upload.filename),
                    "ok": False,
                    "status_code": exc.status_code,
                    "error": exc.detail,
                }
            )

    return {"results": results}


@app.get("/documents/{document_id}")
def get_document(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"document": serialize_document(get_owned_document(db, user, document_id))}


@app.get("/documents/{document_id}/file")
def download_document(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    document = get_owned_document(db, user, document_id)
    ascii_name = document.filename.encode("ascii", "ignore").decode().replace('"', "") or "document.pdf"

    return Response(
        content=document.file.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{ascii_name}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/documents/{document_id}/reindex")
def reindex_document(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    limiter.check(f"reindex:{user.id}", *REINDEX_LIMIT)
    document = get_owned_document(db, user, document_id)

    if document.status == "ready" and document.embedding_model == embedding_model_id():
        return {"document": serialize_document(document), "reindexed": False}

    pages = extract_pages(document.file.data)
    document.status = "processing"
    document.error = None
    db.commit()

    index_document(db, document, pages)
    return {"document": serialize_document(document), "reindexed": True}


@app.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    document = get_owned_document(db, user, document_id)

    # The file, chunks and metadata are removed in one transaction.
    db.delete(document)
    db.commit()
    return Response(status_code=204)


@app.post("/chat")
def chat(request: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")

    document_ids = list(dict.fromkeys(request.document_ids))
    documents = (
        db.query(Document)
        .filter(Document.id.in_(document_ids), Document.owner_id == user.id)
        .all()
    )

    if len(documents) != len(document_ids):
        raise HTTPException(status_code=404, detail="One or more selected documents were not found.")

    for document in documents:
        if document.status != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"{document.filename} is not ready ({document.status}). Retry processing it first.",
            )
        if document.embedding_model != embedding_model_id():
            raise HTTPException(
                status_code=409,
                detail=f"{document.filename} was indexed with a different embedding model. Retry processing it.",
            )

    limiter.check(f"chat:{user.id}", *CHAT_LIMIT)

    # Only chunks of the caller's own selected documents are ever searched.
    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id.in_(document_ids), Chunk.owner_id == user.id)
        .all()
    )
    filenames = {document.id: document.filename for document in documents}

    try:
        ranked = rank_chunks(embed_query(question), chunks)
        result = generate_answer(question, ranked, filenames)
    except Exception as exc:
        status, detail = classify_ai_error(exc)
        logger.error("Gemini question-answering error: %s", type(exc).__name__)
        raise HTTPException(status_code=status, detail=detail)

    return {
        "question": question,
        "answer": result["answer"],
        "sources": result["sources"],
        "model": result["model"],
        "document_ids": document_ids,
    }
