# DocuMind AI

**Live application:** https://docu-mind-ai-delta.vercel.app/  
**Backend API:** https://documind-ai-backend-nzk0.onrender.com/  
**Backend health check:** https://documind-ai-backend-nzk0.onrender.com/health  
**GitHub repository:** https://github.com/rashmitha-j/DocuMind-AI

DocuMind AI is a web-based document assistant that lets users upload a text-based PDF and ask questions about its contents. The Next.js frontend communicates with a FastAPI backend. The backend extracts PDF text, creates a searchable vector index with LlamaIndex, and uses Google Gemini for document embeddings and answers.

> **Current status:** The deployed app has been tested with a PDF upload and a document-summary question. The current backend stores the active document index in memory, so the PDF must be uploaded again after a backend restart. Google AI Studio free-tier quotas and limits apply.

## Table of contents

- [Features](#features)
- [Live links](#live-links)
- [Technology stack](#technology-stack)
- [How it works](#how-it-works)
- [Repository structure](#repository-structure)
- [Requirements](#requirements)
- [Run locally on Windows](#run-locally-on-windows)
- [Environment variables and API keys](#environment-variables-and-api-keys)
- [API endpoints](#api-endpoints)
- [Deployment](#deployment)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

## Features

- Upload PDF files through a browser interface.
- Extract selectable text from PDFs using `pypdf`.
- Build a searchable vector index using LlamaIndex and Gemini embeddings.
- Ask natural-language questions about the currently uploaded document.
- Generate document summaries and answers using Google Gemini.
- Show PDF upload/indexing status and AI responses.
- Enforce a 10 MB upload limit on the backend.
- Deploy frontend and backend as separate services.

## Live links

| Component | URL |
|---|---|
| Web application | https://docu-mind-ai-delta.vercel.app/ |
| Backend root | https://documind-ai-backend-nzk0.onrender.com/ |
| Backend health check | https://documind-ai-backend-nzk0.onrender.com/health |
| GitHub repository | https://github.com/rashmitha-j/DocuMind-AI |

The health endpoint should return:

```json
{"status":"healthy"}
```

## Technology stack

### Frontend
- Next.js 16
- React 19
- TypeScript
- Tailwind CSS 4
- Vercel hosting

### Backend
- Python
- FastAPI
- Uvicorn
- LlamaIndex
- `pypdf` for PDF text extraction
- Google Gemini via LlamaIndex Google GenAI integrations
- Render hosting

### Configured AI models
- Chat/generation model: `gemini-3.8-flash`
- Embedding model: `gemini-embedding-001`

Model names and free-tier availability can change. If Google reports that a model is unavailable, choose a currently supported model for your API key and update `backend/main.py`.

## How it works

1. The user selects a PDF in the web app.
2. The frontend sends it to the backend's `POST /upload` endpoint.
3. FastAPI validates the file extension and size.
4. `pypdf` extracts selectable text.
5. LlamaIndex creates a vector index using Gemini embeddings.
6. The backend keeps the active index and filename in memory.
7. The user submits a question through the frontend.
8. The frontend sends the question to `POST /chat`.
9. LlamaIndex retrieves relevant document content and Gemini generates an answer.
10. The backend returns the answer for display in the frontend.

## Repository structure

```text
DocuMind-AI/
├── backend/
│   └── main.py              # FastAPI routes, PDF processing, indexing and chat
├── frontend/
│   ├── app/                 # Next.js app and UI
│   ├── package.json         # Frontend dependencies and scripts
│   └── ...                  # Other Next.js, TypeScript and styling files
├── requirements.txt         # Python dependencies
├── .gitignore               # Excludes secrets and generated files
└── README.md
```

## Requirements

For local development, install:

- Python 3.11 or a compatible Python version supported by the installed packages.
- Node.js and npm compatible with the version in `frontend/package.json`.
- Git.
- A Google AI Studio API key with access to the configured Gemini models.

A Gemini API key is separate from a ChatGPT subscription. Google AI Studio free-tier access has usage limits and is subject to Google's current terms and model availability.

## Run locally on Windows

Open PowerShell or the VS Code terminal.

### 1. Clone the repository

```powershell
git clone https://github.com/rashmitha-j/DocuMind-AI.git
cd DocuMind-AI
```

If the repository is already on your computer, open its existing folder instead of cloning it again.

### 2. Create and activate a Python virtual environment

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation in this terminal session, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 3. Install backend dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure the backend API key

Create a file named `.env` in the repository root (the same folder as `requirements.txt`) and add:

```dotenv
GOOGLE_API_KEY=your_google_ai_studio_api_key
```

Replace the example value with your own key. Never commit this file.

### 5. Start the backend

From the repository root, run:

```powershell
uvicorn main:app --app-dir backend --reload
```

The backend is normally available at:

- API root: http://127.0.0.1:8000/
- Health check: http://127.0.0.1:8000/health
- Interactive API documentation: http://127.0.0.1:8000/docs

### 6. Configure and start the frontend

Open a **second** terminal:

```powershell
cd frontend
npm install
```

Create `frontend/.env.local` with:

```dotenv
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Then start Next.js:

```powershell
npm run dev
```

Open http://localhost:3000.

The backend CORS configuration allows the deployed Vercel URL and local development URLs. If using another frontend domain or port, update `allow_origins` in `backend/main.py`.

## Environment variables and API keys

| Variable | Used by | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | Backend | Authenticates Gemini chat and embedding requests |
| `NEXT_PUBLIC_API_URL` | Frontend | Base URL of the FastAPI backend |

### Production configuration

- **Render:** Add `GOOGLE_API_KEY` in the backend service's Environment settings. Do not place the key in frontend variables.
- **Vercel:** Set `NEXT_PUBLIC_API_URL` to `https://documind-ai-backend-nzk0.onrender.com`, then redeploy the frontend after changing it.
- Keep `.env` and `.env.local` out of Git. The repository's `.gitignore` excludes `.env` and `.env.*`.

Never paste an API key into an issue, public repository, README, screenshot, or chat. If a key is exposed, revoke it and create a replacement.

## API endpoints

### `GET /`

Returns a short backend status message and configured provider.

### `GET /health`

Returns a simple health response:

```json
{"status":"healthy"}
```

### `POST /upload`

Uploads and indexes a PDF. Send a multipart form request with the file field named `file`.

Example using curl:

```bash
curl -X POST "http://127.0.0.1:8000/upload" \
  -F "file=@sample.pdf"
```

A successful response includes the filename, page count, extracted character count, a short text preview, and a success message.

Validation includes:
- Filename must end in `.pdf`.
- File must be no larger than 10 MB.
- PDF must contain extractable text.
- `GOOGLE_API_KEY` must be available to the backend.
- Gemini and embedding requests must be allowed by the key and usage limits.

Scanned/image-only PDFs are not OCR-processed by the current implementation.

### `POST /chat`

Ask a question about the currently indexed PDF. Send JSON with a `question` field.

Example:

```bash
curl -X POST "http://127.0.0.1:8000/chat" \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"Summarize this PDF in five bullet points.\"}"
```

A successful response includes the question, generated answer, and filename. Upload a PDF before calling this endpoint.

## Deployment

### Frontend — Vercel

1. Import the GitHub repository into Vercel.
2. Set the project root directory to `frontend`.
3. Add `NEXT_PUBLIC_API_URL` with the deployed Render backend URL.
4. Deploy. Future pushes to the connected branch can trigger deployments automatically.

### Backend — Render

1. Create a Web Service connected to this GitHub repository.
2. Use the repository root as the service root directory.
3. Install dependencies with `pip install -r requirements.txt`.
4. Set the start command to:

   ```bash
   uvicorn main:app --app-dir backend --host 0.0.0.0 --port $PORT
   ```

5. Add `GOOGLE_API_KEY` under Environment.
6. Deploy and verify `/health`.

If a service is recreated and its URL changes, update the links in this README and the frontend environment variable.

## Limitations

- **In-memory index:** The active document index is stored in process memory. A Render restart or deployment clears it; upload the PDF again.
- **One active document:** Uploading another PDF replaces the active index.
- **No persistent document library:** Uploaded PDFs and indexes are not currently saved in a database or object storage.
- **No authentication or user isolation:** Do not use this version for confidential documents or private multi-user workloads.
- **PDF text extraction only:** Scanned PDFs require OCR, which is not implemented.
- **Upload size:** Backend limit is 10 MB.
- **AI quotas:** Gemini usage limits and model availability can affect uploads and answers.
- **Free hosting:** Free hosting may sleep or cold-start; availability and performance depend on provider policies.
- **Answer accuracy:** Verify important answers against the source document.

## Troubleshooting

### PDF uploads, but questions fail
- Check Render runtime logs for `Gemini question-answering error`.
- Confirm the chat model is currently available to your Google AI Studio API key.
- Check Google AI Studio usage/rate limits and API key validity.
- Redeploy Render after backend code or dependency changes.

### Upload fails during indexing
- Confirm `GOOGLE_API_KEY` is set in Render Environment.
- Check Render runtime logs for `Gemini PDF indexing error`.
- Confirm the embedding model is available and the API quota has not been exhausted.

### Browser reports a CORS error
- Confirm the frontend URL is included in `allow_origins` in `backend/main.py`.
- Commit and push backend changes, then wait for Render to deploy.

### Local frontend cannot reach the backend
- Confirm the backend is running.
- Check `frontend/.env.local` and make sure `NEXT_PUBLIC_API_URL` points to the correct backend.
- Restart the Next.js development server after changing environment variables.

### Questions say no PDF is uploaded after a restart
This is expected with the current in-memory index. Upload the PDF again.

## Security

- Do not commit API keys, `.env` files, credentials, or private documents.
- Keep the Gemini API key on the backend only.
- Revoke and rotate any exposed key.
- Before allowing public users to upload documents, add authentication, per-user storage and isolation, rate limiting, file scanning, and appropriate privacy controls.

---

Built with Next.js, FastAPI, LlamaIndex, and Google Gemini.
