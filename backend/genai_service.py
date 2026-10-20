import os

import numpy as np
from llama_index.core import Document, Settings
from llama_index.core.node_parser import SentenceSplitter


DEFAULT_CHAT_MODEL = "gemini-3.8-flash"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1024"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
TOP_K = int(os.getenv("SIMILARITY_TOP_K", "5"))
EMBED_DIMENSIONS = int(os.getenv("GEMINI_EMBED_DIMENSIONS", "768"))

# Answers must come from the retrieved document text, not general knowledge.
QA_PROMPT = (
    "You are DocuMind AI, an assistant that answers questions about the user's "
    "uploaded PDFs.\n"
    "Use ONLY the numbered sources below. Cite the sources you use with their "
    "labels, for example [S1] or [S2].\n"
    "If the sources do not contain the answer, reply that the selected documents "
    "do not provide enough information. Do not invent facts, quotes or page numbers.\n"
    "---------------------\n"
    "{context}\n"
    "---------------------\n"
    "Question: {question}\n"
    "Answer: "
)

_configured = False


class AIConfigurationError(RuntimeError):
    """Raised when the Gemini API key is missing."""


def embedding_model_id() -> str:
    """Identifies the vectors stored for a document, so stale ones are detected."""

    return f"{os.getenv('GEMINI_EMBED_MODEL', DEFAULT_EMBED_MODEL)}:{EMBED_DIMENSIONS}"


def configure_gemini():
    """Configure the Gemini language and embedding models once per process."""

    global _configured

    if _configured:
        return

    api_key = os.getenv("GOOGLE_API_KEY")

    if not api_key:
        raise AIConfigurationError("GOOGLE_API_KEY is not configured.")

    # Imported lazily so tests and health checks never need the Gemini SDK.
    from llama_index.llms.google_genai import GoogleGenAI
    from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

    Settings.llm = GoogleGenAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_CHAT_MODEL),
        api_key=api_key,
        temperature=0.2,
        max_retries=2,
    )

    Settings.embed_model = GoogleGenAIEmbedding(
        model_name=os.getenv("GEMINI_EMBED_MODEL", DEFAULT_EMBED_MODEL),
        api_key=api_key,
        embedding_config={"output_dimensionality": EMBED_DIMENSIONS},
        retries=2,
        timeout=60,
    )

    _configured = True


def split_pages(pages: list[str]) -> list[dict]:
    """Split page texts into overlapping chunks that keep 1-based page numbers."""

    documents = [
        Document(text=text, metadata={"page": page_number})
        for page_number, text in enumerate(pages, start=1)
        if text.strip()
    ]
    splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    return [
        {"page": node.metadata["page"], "chunk_index": index, "text": node.get_content()}
        for index, node in enumerate(splitter.get_nodes_from_documents(documents))
    ]


def to_vector(values) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def embed_texts(texts: list[str]) -> list[bytes]:
    configure_gemini()
    embeddings = Settings.embed_model.get_text_embedding_batch(texts)
    return [to_vector(values).tobytes() for values in embeddings]


def embed_query(question: str) -> np.ndarray:
    configure_gemini()
    return to_vector(Settings.embed_model.get_query_embedding(question))


def rank_chunks(query_vector: np.ndarray, chunks: list, top_k: int = TOP_K) -> list[tuple]:
    """Return (chunk, score) pairs ordered by cosine similarity."""

    if not chunks:
        return []

    matrix = np.vstack([np.frombuffer(chunk.embedding, dtype=np.float32) for chunk in chunks])

    if matrix.shape[1] != query_vector.shape[0]:
        raise ValueError("Stored embeddings do not match the current embedding model.")

    scores = matrix @ query_vector
    order = np.argsort(-scores)[:top_k]
    return [(chunks[i], float(scores[i])) for i in order]


def generate_answer(question: str, ranked: list[tuple], filenames: dict[str, str]) -> dict:
    """Ask Gemini for an answer grounded in the ranked chunks, with citations."""

    configure_gemini()

    sources = []
    context_parts = []

    for number, (chunk, score) in enumerate(ranked, start=1):
        label = f"S{number}"
        filename = filenames[chunk.document_id]
        context_parts.append(f"[{label}] {filename}, page {chunk.page}:\n{chunk.text}")

        excerpt = " ".join(chunk.text.split())
        sources.append(
            {
                "label": label,
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "filename": filename,
                "page": chunk.page,
                "score": round(score, 3),
                "excerpt": excerpt[:400] + ("..." if len(excerpt) > 400 else ""),
            }
        )

    prompt = QA_PROMPT.format(context="\n\n".join(context_parts), question=question)
    response = Settings.llm.complete(prompt)

    return {"answer": str(response).strip(), "sources": sources}


def classify_ai_error(exc: Exception) -> tuple[int, str]:
    """Map a Gemini/LlamaIndex failure to a safe HTTP status and message."""

    if isinstance(exc, AIConfigurationError):
        return 503, "The AI service is not configured on the backend (GOOGLE_API_KEY is missing)."

    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = f"{type(exc).__name__} {exc}".lower()

    if code == 429 or "resource_exhausted" in text or "quota" in text or "rate limit" in text:
        return 429, (
            "The Gemini free-tier quota or rate limit has been reached. "
            "Please wait a minute and try again, or check usage in Google AI Studio."
        )

    if code in (401, 403) or "api_key_invalid" in text or "api key not valid" in text or "permission_denied" in text:
        return 502, "The backend's Gemini API key is invalid or lacks permission. The key must be updated on the server."

    if code == 404 or "not_found" in text or "is not found" in text:
        return 502, "The configured Gemini model is unavailable. Check GEMINI_MODEL / GEMINI_EMBED_MODEL on the backend."

    if "timeout" in text or "timed out" in text:
        return 504, "The Gemini request timed out. Please try again."

    if "connect" in text or "network" in text:
        return 503, "The backend could not reach the Gemini API. Please try again shortly."

    return 502, "The AI service failed to process this request. Please try again later."
