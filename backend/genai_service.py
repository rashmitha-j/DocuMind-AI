import logging
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
ANSWER_MAX_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2048"))
# The main model gets less time so an overloaded call still leaves room for the fallback.
LLM_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))
FALLBACK_TIMEOUT_SECONDS = int(os.getenv("GEMINI_FALLBACK_TIMEOUT_SECONDS", "60"))
# Empty disables the setting for models that do not support thinking levels.
THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "LOW").strip().upper()
# Used only when the main model is overloaded (5xx). Empty disables it.
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash").strip()

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

logger = logging.getLogger("documind")

_configured = False
_client = None


class AIConfigurationError(RuntimeError):
    """Raised when the Gemini API key is missing."""


class EmptyAnswerError(RuntimeError):
    """Raised when Gemini returns no text (for example, a blocked response)."""


def embedding_model_id() -> str:
    """Identifies the vectors stored for a document, so stale ones are detected."""

    return f"{os.getenv('GEMINI_EMBED_MODEL', DEFAULT_EMBED_MODEL)}:{EMBED_DIMENSIONS}"


def chat_model() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_CHAT_MODEL)


def require_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise AIConfigurationError("GOOGLE_API_KEY is not configured.")
    return api_key


def generation_config(timeout_seconds: int = LLM_TIMEOUT_SECONDS):
    """Answer settings with bounded latency.

    No temperature: Gemini 3 models can fail when it is set. Output is capped
    and thinking is kept low so answers return well within the timeout.
    """

    from google.genai import types

    config = {
        "max_output_tokens": ANSWER_MAX_TOKENS,
        "http_options": types.HttpOptions(timeout=timeout_seconds * 1000),
    }
    if THINKING_LEVEL:
        config["thinking_config"] = types.ThinkingConfig(thinking_level=THINKING_LEVEL)
    return types.GenerateContentConfig(**config)


def genai_client():
    """Shared Gemini SDK client with an HTTP timeout, so a stalled call cannot hang."""

    global _client

    if _client is None:
        from google import genai
        from google.genai import types

        _client = genai.Client(
            api_key=require_api_key(),
            http_options=types.HttpOptions(timeout=max(LLM_TIMEOUT_SECONDS, FALLBACK_TIMEOUT_SECONDS) * 1000),
        )

    return _client


def complete(model: str, prompt: str, timeout_seconds: int = LLM_TIMEOUT_SECONDS) -> str:
    """One generate_content request: no retries, no chat session, no metadata calls."""

    response = genai_client().models.generate_content(
        model=model, contents=prompt, config=generation_config(timeout_seconds)
    )
    text = (response.text or "").strip()

    if not text:
        raise EmptyAnswerError("Gemini returned an empty answer.")

    return text


def is_overloaded(exc: Exception) -> bool:
    """True for Gemini server-side failures such as 503 'high demand'."""

    code = getattr(exc, "code", None)
    text = f"{type(exc).__name__} {exc}".lower()
    return (isinstance(code, int) and code >= 500) or any(
        marker in text for marker in ("unavailable", "high demand", "overloaded", "servererror")
    )


def should_fall_back(exc: Exception) -> bool:
    """Overloads and timeouts of the main model are retried once on the fallback."""

    text = f"{type(exc).__name__} {exc}".lower()
    return is_overloaded(exc) or "timeout" in text or "timed out" in text


def configure_gemini():
    """Configure the Gemini embedding model once per process."""

    global _configured

    if _configured:
        return

    api_key = require_api_key()

    # Imported lazily so tests and health checks never need the Gemini SDK.
    from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

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
    model = chat_model()

    try:
        answer = complete(model, prompt)
    except Exception as exc:
        # Only Google-side overloads and timeouts fall back; quota and key errors do not.
        if not (should_fall_back(exc) and FALLBACK_MODEL and FALLBACK_MODEL != model):
            raise
        logger.warning("%s unavailable (%s); using fallback %s", model, type(exc).__name__, FALLBACK_MODEL)
        model = FALLBACK_MODEL
        answer = complete(model, prompt, FALLBACK_TIMEOUT_SECONDS)

    return {"answer": answer, "sources": sources, "model": model}


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

    if is_overloaded(exc):
        return 503, (
            "Gemini is temporarily overloaded (high demand on Google's side). "
            "Please try again in a minute."
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
