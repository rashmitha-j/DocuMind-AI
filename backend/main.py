
import os
from pathlib import Path
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader

from llama_index.core import VectorStoreIndex, Document, Settings
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding


# Load the API key from the project root .env file
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(
    title="DocuMind AI API",
    description="Upload PDFs and ask questions about their content",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store the currently uploaded document's index in memory.
document_index = None
document_filename = None


class ChatRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return {
        "message": "DocuMind AI backend is running!",
        "status": "success",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    global document_index, document_filename

    filename = file.filename or "document"

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PDF file.",
        )

    content = await file.read()

    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum size is 10 MB.",
        )

    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join(
            page.extract_text() or "" for page in reader.pages
        ).strip()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Could not read this PDF. Please try another file.",
        )

    if not text:
        raise HTTPException(
            status_code=400,
            detail="No extractable text found. Scanned PDFs need OCR.",
        )

    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is missing from the backend environment.",
        )

    try:
        Settings.llm = OpenAI(model="gpt-4o-mini")
        Settings.embed_model = OpenAIEmbedding(
            model="text-embedding-3-small"
        )

        document = Document(
            text=text,
            metadata={"filename": filename},
        )

        # Create a searchable index from the extracted PDF text.
        new_index = VectorStoreIndex.from_documents([document])

        # Replace the active index only after indexing succeeds.
        document_index = new_index
        document_filename = filename

    except Exception as exc:
        print("PDF indexing error:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail=(
                "PDF text was extracted, but AI indexing failed. "
                "Check API billing, model access, and the backend terminal."
            ),
        )

    return {
        "filename": filename,
        "pages": len(reader.pages),
        "characters": len(text),
        "text_preview": text[:3000],
        "message": "PDF uploaded and indexed successfully. You can ask questions now.",
    }


@app.post("/chat")
def chat(request: ChatRequest):
    if not request.question.strip():
        raise HTTPException(
            status_code=400,
            detail="Please enter a question.",
        )

    if document_index is None:
        raise HTTPException(
            status_code=400,
            detail="Upload a PDF before asking questions.",
        )

    try:
        query_engine = document_index.as_query_engine(
            similarity_top_k=3
        )
        response = query_engine.query(request.question)

        return {
            "question": request.question,
            "answer": str(response),
            "filename": document_filename,
        }

    except Exception as exc:
        print("Question-answering error:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not generate an answer. Check API billing, "
                "model access, and the backend terminal."
            ),
        )
