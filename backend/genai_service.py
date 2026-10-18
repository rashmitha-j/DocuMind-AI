
import os

from llama_index.core import Document, Settings, VectorStoreIndex
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding


def configure_gemini():
    """Configure the Gemini language and embedding models."""

    api_key = os.getenv("GOOGLE_API_KEY")

    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not configured.")

    Settings.llm = GoogleGenAI(
        model="gemini-3.8-flash",
        api_key=api_key,
    )

    Settings.embed_model = GoogleGenAIEmbedding(
        model_name="gemini-embedding-001",
        api_key=api_key,
    )


def create_document_index(text: str, filename: str):
    """Create a searchable index from extracted PDF text."""

    configure_gemini()

    document = Document(
        text=text,
        metadata={"filename": filename},
    )

    return VectorStoreIndex.from_documents([document])


def answer_question(index, question: str) -> str:
    """Answer a question using the indexed document."""

    query_engine = index.as_query_engine(
        similarity_top_k=3,
    )

    response = query_engine.query(question)

    return str(response)
