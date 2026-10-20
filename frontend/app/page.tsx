"use client";

import { useCallback, useEffect, useState } from "react";

import {
  api,
  ApiError,
  formatSize,
  type DocumentInfo,
  type Source,
  type UploadResult,
  type User,
} from "@/lib/api";

const TOKEN_KEY = "documind-session";

type Message = {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
};

const WELCOME_MESSAGE: Message = {
  role: "assistant",
  text: "Welcome to DocuMind AI. Upload PDFs, select the documents to search, and ask a question. Every answer lists the pages it came from.",
};

function readStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage can be unavailable (private mode); the session then lasts for this page only.
  }
}

export default function Home() {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [authNotice, setAuthNotice] = useState("");

  const signOutLocally = useCallback((notice = "") => {
    storeToken(null);
    setToken(null);
    setUser(null);
    setAuthNotice(notice);
  }, []);

  useEffect(() => {
    // Restore a session saved in this browser tab, if any.
    Promise.resolve(readStoredToken())
      .then((stored) => {
        if (!stored) return;
        return api<{ user: User }>("/auth/me", { token: stored }).then((result) => ({ stored, result }));
      })
      .then((restored) => {
        if (!restored) return;
        const { stored, result } = restored;
        setToken(stored);
        setUser(result.user);
      })
      .catch((error: unknown) => {
        if (!(error instanceof ApiError)) return;
        if (error.status === 401) storeToken(null);
        else setAuthNotice(error.message);
      })
      .finally(() => setCheckingSession(false));
  }, []);

  if (checkingSession) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#f7f8fc] text-sm text-slate-500">
        Loading DocuMind AI...
      </main>
    );
  }

  if (!token || !user) {
    return (
      <AuthScreen
        notice={authNotice}
        onSignedIn={(newToken, newUser) => {
          storeToken(newToken);
          setToken(newToken);
          setUser(newUser);
          setAuthNotice("");
        }}
      />
    );
  }

  return (
    <Workspace
      token={token}
      user={user}
      onSignOut={async () => {
        try {
          await api("/auth/logout", { method: "POST", token });
        } catch {
          // The local session is cleared even if the backend is unreachable.
        }
        signOutLocally();
      }}
      onSessionExpired={() => signOutLocally("Your session has expired. Please sign in again.")}
    />
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-xl font-bold text-white">
        D
      </div>
      <div>
        <h1 className="text-lg font-bold">DocuMind AI</h1>
        <p className="text-xs text-slate-500">Your document assistant</p>
      </div>
    </div>
  );
}

function AuthScreen({
  notice,
  onSignedIn,
}: {
  notice: string;
  onSignedIn: (token: string, user: User) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    setBusy(true);

    try {
      const result = await api<{ token: string; user: User }>(
        mode === "login" ? "/auth/login" : "/auth/register",
        { method: "POST", json: { email, password } },
      );
      onSignedIn(result.token, result.user);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#f7f8fc] px-4 py-10 text-slate-900">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
        <Brand />

        <h2 className="mt-8 text-2xl font-bold tracking-tight">
          {mode === "login" ? "Sign in" : "Create your account"}
        </h2>
        <p className="mt-2 text-sm text-slate-500">
          Your documents are private to your account.
        </p>

        {(notice || error) && (
          <p
            role="alert"
            className="mt-5 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800"
          >
            {error || notice}
          </p>
        )}

        <form
          className="mt-6 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="block text-sm font-medium">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            />
          </label>

          <label className="block text-sm font-medium">
            Password
            <input
              type="password"
              required
              minLength={8}
              maxLength={128}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-indigo-400"
            />
            {mode === "register" && (
              <span className="mt-1 block text-xs font-normal text-slate-400">
                At least 8 characters.
              </span>
            )}
          </label>

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          {mode === "login" ? "New to DocuMind AI?" : "Already have an account?"}{" "}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError("");
            }}
            className="font-semibold text-indigo-600 hover:underline"
          >
            {mode === "login" ? "Create an account" : "Sign in"}
          </button>
        </p>
      </div>
    </main>
  );
}

function StatusBadge({ status }: { status: DocumentInfo["status"] }) {
  const styles = {
    ready: "bg-emerald-50 text-emerald-700",
    processing: "bg-amber-50 text-amber-700",
    failed: "bg-red-50 text-red-700",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${styles[status]}`}>
      {status}
    </span>
  );
}

function Workspace({
  token,
  user,
  onSignOut,
  onSessionExpired,
}: {
  token: string;
  user: User;
  onSignOut: () => void;
  onSessionExpired: () => void;
}) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [libraryError, setLibraryError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [busyDocument, setBusyDocument] = useState("");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);

  const addMessage = (message: Message) => setMessages((previous) => [...previous, message]);

  const handleError = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        onSessionExpired();
        return "";
      }
      return error instanceof Error ? error.message : "An unexpected error occurred.";
    },
    [onSessionExpired],
  );

  const loadDocuments = useCallback(
    () =>
      api<{ documents: DocumentInfo[] }>("/documents", { token })
        .then((result) => {
          setDocuments(result.documents);
          setLibraryError("");
          // Drop selections for documents that no longer exist or are not ready.
          setSelected((previous) =>
            previous.filter((id) =>
              result.documents.some((doc) => doc.id === id && doc.status === "ready"),
            ),
          );
        })
        .catch((error: unknown) => {
          const message = handleError(error);
          if (message) setLibraryError(message);
        })
        .finally(() => setLoadingLibrary(false)),
    [token, handleError],
  );

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  async function handleUpload(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;

    if (files.length > 5) {
      addMessage({ role: "assistant", text: "Please upload at most 5 PDFs at a time." });
      return;
    }

    const formData = new FormData();
    for (const file of files) formData.append("files", file);

    setUploading(true);
    addMessage({
      role: "assistant",
      text: `Uploading and indexing ${files.map((file) => file.name).join(", ")}. This can take a moment...`,
    });

    try {
      const result = await api<{ results: UploadResult[] }>("/documents", {
        method: "POST",
        token,
        body: formData,
      });

      const lines = result.results.map((item) =>
        item.ok && item.document
          ? `✓ ${item.filename}: ${item.duplicate ? "already in your library (reused, no re-indexing)" : `indexed ${item.document.pages} page(s)`}`
          : `✗ ${item.filename}: ${item.error}`,
      );
      addMessage({ role: "assistant", text: lines.join("\n") });

      const newIds = result.results
        .filter((item) => item.ok && item.document)
        .map((item) => item.document!.id);
      setSelected((previous) => Array.from(new Set([...previous, ...newIds])));
    } catch (error) {
      const message = handleError(error);
      if (message) addMessage({ role: "assistant", text: `Upload failed: ${message}` });
    } finally {
      setUploading(false);
      await loadDocuments();
    }
  }

  async function openDocument(doc: DocumentInfo) {
    // Open the tab synchronously so pop-up blockers allow it, then fill it in.
    const tab = window.open("", "_blank");
    try {
      const blob = await api<Blob>(`/documents/${doc.id}/file`, { token, raw: true });
      const url = URL.createObjectURL(blob);
      if (tab) tab.location.href = url;
      else window.location.href = url;
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      tab?.close();
      const message = handleError(error);
      if (message) addMessage({ role: "assistant", text: `Could not open ${doc.filename}: ${message}` });
    }
  }

  async function retryDocument(doc: DocumentInfo) {
    setBusyDocument(doc.id);
    try {
      await api(`/documents/${doc.id}/reindex`, { method: "POST", token });
      addMessage({ role: "assistant", text: `${doc.filename} is now indexed and ready.` });
    } catch (error) {
      const message = handleError(error);
      if (message) addMessage({ role: "assistant", text: `Retry failed for ${doc.filename}: ${message}` });
    } finally {
      setBusyDocument("");
      await loadDocuments();
    }
  }

  async function deleteDocument(doc: DocumentInfo) {
    if (!window.confirm(`Delete ${doc.filename}? This removes the file and its index.`)) return;

    setBusyDocument(doc.id);
    try {
      await api(`/documents/${doc.id}`, { method: "DELETE", token });
    } catch (error) {
      const message = handleError(error);
      if (message) addMessage({ role: "assistant", text: `Could not delete ${doc.filename}: ${message}` });
    } finally {
      setBusyDocument("");
      await loadDocuments();
    }
  }

  function toggleSelected(id: string) {
    setSelected((previous) =>
      previous.includes(id) ? previous.filter((item) => item !== id) : [...previous, id],
    );
  }

  async function sendMessage() {
    const questionText = question.trim();
    if (!questionText || asking) return;

    if (selected.length === 0) {
      addMessage({ role: "user", text: questionText });
      addMessage({
        role: "assistant",
        text: "Select at least one ready document in your library before asking a question.",
      });
      setQuestion("");
      return;
    }

    addMessage({ role: "user", text: questionText });
    setQuestion("");
    setAsking(true);

    try {
      const result = await api<{ answer: string; sources: Source[] }>("/chat", {
        method: "POST",
        token,
        json: { document_ids: selected, question: questionText },
      });
      addMessage({ role: "assistant", text: result.answer, sources: result.sources });
    } catch (error) {
      const message = handleError(error);
      if (message) addMessage({ role: "assistant", text: `Could not generate an answer: ${message}` });
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
        await loadDocuments();
      }
    } finally {
      setAsking(false);
    }
  }

  const readyCount = documents.filter((doc) => doc.status === "ready").length;

  return (
    <main className="min-h-screen bg-[#f7f8fc] text-slate-900 md:flex">
      <aside className="flex w-full flex-col border-b border-slate-200 bg-white p-5 md:min-h-screen md:w-80 md:border-r md:border-b-0">
        <Brand />

        <div className="mt-6 flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2">
          <span className="truncate text-xs text-slate-600" title={user.email}>
            {user.email}
          </span>
          <button
            type="button"
            onClick={onSignOut}
            className="shrink-0 text-xs font-medium text-slate-500 hover:text-red-600"
          >
            Sign out
          </button>
        </div>

        <label
          className={`mt-6 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed border-indigo-200 px-4 py-5 text-center transition hover:border-indigo-400 hover:bg-indigo-50/40 ${uploading ? "pointer-events-none opacity-60" : ""}`}
        >
          <span className="text-sm font-semibold">
            {uploading ? "Processing..." : "⇧ Upload PDFs"}
          </span>
          <span className="mt-1 text-xs text-slate-500">Up to 5 files, 10 MB each</span>
          <input
            type="file"
            accept=".pdf,application/pdf"
            multiple
            className="hidden"
            disabled={uploading}
            onChange={(event) => {
              void handleUpload(event.target.files);
              event.currentTarget.value = "";
            }}
          />
        </label>

        <div className="mt-6 flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            My documents
          </p>
          {readyCount > 1 && (
            <button
              type="button"
              onClick={() =>
                setSelected(
                  selected.length === readyCount
                    ? []
                    : documents.filter((doc) => doc.status === "ready").map((doc) => doc.id),
                )
              }
              className="text-xs font-medium text-indigo-600 hover:underline"
            >
              {selected.length === readyCount ? "Clear" : "Select all"}
            </button>
          )}
        </div>

        <div className="mt-3 max-h-80 space-y-2 overflow-y-auto md:max-h-none md:flex-1">
          {loadingLibrary && <p className="text-sm text-slate-400">Loading documents...</p>}
          {libraryError && <p className="text-sm text-red-600">{libraryError}</p>}
          {!loadingLibrary && !libraryError && documents.length === 0 && (
            <p className="text-sm text-slate-400">No documents yet. Upload a PDF to begin.</p>
          )}

          {documents.map((doc) => (
            <div
              key={doc.id}
              className={`rounded-xl border p-3 ${selected.includes(doc.id) ? "border-indigo-300 bg-indigo-50/60" : "border-slate-200"}`}
            >
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1 accent-indigo-600"
                  checked={selected.includes(doc.id)}
                  disabled={doc.status !== "ready"}
                  onChange={() => toggleSelected(doc.id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium" title={doc.filename}>
                    {doc.filename}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                    <StatusBadge status={doc.status} />
                    {doc.pages} pages · {formatSize(doc.size_bytes)} ·{" "}
                    {new Date(doc.created_at).toLocaleDateString()}
                  </span>
                </span>
              </label>

              {doc.error && <p className="mt-2 text-xs text-red-600">{doc.error}</p>}

              <div className="mt-2 flex gap-3 pl-6 text-xs">
                <button
                  type="button"
                  onClick={() => void openDocument(doc)}
                  className="text-slate-500 hover:text-indigo-600"
                >
                  Open
                </button>
                {doc.status === "failed" && (
                  <button
                    type="button"
                    disabled={busyDocument === doc.id}
                    onClick={() => void retryDocument(doc)}
                    className="text-amber-700 hover:underline disabled:opacity-50"
                  >
                    {busyDocument === doc.id ? "Retrying..." : "Retry"}
                  </button>
                )}
                <button
                  type="button"
                  disabled={busyDocument === doc.id}
                  onClick={() => void deleteDocument(doc)}
                  className="text-slate-500 hover:text-red-600 disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <section className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4 md:px-9">
          <div className="min-w-0">
            <h2 className="font-semibold">Document workspace</h2>
            <p className="mt-1 truncate text-xs text-slate-500">
              {selected.length === 0
                ? "No documents selected"
                : `Searching ${selected.length} selected document${selected.length > 1 ? "s" : ""}`}
            </p>
          </div>

          <button
            type="button"
            onClick={() => setMessages([WELCOME_MESSAGE])}
            className="shrink-0 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
          >
            + New conversation
          </button>
        </header>

        <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col px-4 py-8 md:px-8">
          <div className="mb-8">
            <p className="mb-3 text-sm font-medium text-indigo-600">
              YOUR AI DOCUMENT ASSISTANT
            </p>
            <h2 className="text-3xl font-bold tracking-tight md:text-4xl">
              Understand your documents.
              <span className="block text-indigo-600">Ask better questions.</span>
            </h2>
            <p className="mt-4 max-w-2xl leading-7 text-slate-500">
              Upload PDFs, choose which ones to search, and get answers grounded in
              their content with page-level citations.
            </p>
          </div>

          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-semibold">Conversation</h3>
            <span className="text-xs text-slate-400">AI assistant</span>
          </div>

          <div className="flex-1 space-y-4 rounded-2xl border border-slate-200 bg-white p-4 md:p-6">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {message.role === "assistant" && (
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-sm font-bold text-white">
                    D
                  </div>
                )}

                <div
                  className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6 ${
                    message.role === "user"
                      ? "bg-indigo-600 text-white"
                      : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {message.text}

                  {message.sources && message.sources.length > 0 && (
                    <details className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-500">
                      <summary className="cursor-pointer font-medium text-indigo-600">
                        Sources ({message.sources.length})
                      </summary>
                      <ul className="mt-2 space-y-2">
                        {message.sources.map((source) => (
                          <li key={source.chunk_id} className="rounded-lg bg-white p-2">
                            <span className="font-semibold text-slate-700">
                              [{source.label}] {source.filename} · Page {source.page}
                            </span>
                            <span className="block">{source.excerpt}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </div>
            ))}

            {asking && (
              <p className="text-sm text-slate-500">DocuMind AI is preparing your answer...</p>
            )}
          </div>

          <form
            className="mt-4 flex gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void sendMessage();
            }}
          >
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={
                selected.length ? "Ask a question about the selected documents..." : "Select a document first..."
              }
              maxLength={2000}
              disabled={asking || uploading}
              className="min-w-0 flex-1 bg-transparent px-3 py-3 text-sm outline-none"
            />

            <button
              type="submit"
              disabled={asking || uploading || !question.trim()}
              className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {asking ? "Thinking..." : "Send ↗"}
            </button>
          </form>

          <p className="mt-3 text-center text-xs text-slate-400">
            Answers are generated by Google Gemini from your selected PDFs and may contain
            mistakes. Check the cited pages.
          </p>
        </div>
      </section>
    </main>
  );
}
