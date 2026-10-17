
"use client";

import { useState } from "react";

export default function Home() {
  const [question, setQuestion] = useState("");
  const [fileName, setFileName] = useState("");
  const [messages, setMessages] = useState<
    { role: string; text: string }[]
  >([
    {
      role: "assistant",
      text: "Welcome to DocuMind AI. Upload a document and ask questions to explore its content.",
    },
  ]);

  function handleUpload(file?: File) {
    if (file) setFileName(file.name);
  }

  function sendMessage() {
    if (!question.trim()) return;

    setMessages((previous) => [
      ...previous,
      { role: "user", text: question.trim() },
      {
        role: "assistant",
        text: fileName
          ? `Your question has been received about "${fileName}". Connect the AI backend to generate answers from the document.`
          : "Please upload a document first. Then connect the AI backend to ask questions about its content.",
      },
    ]);
    setQuestion("");
  }

  return (
    <main className="min-h-screen bg-[#f7f8fc] text-slate-900 md:flex">
      <aside className="flex w-full flex-col border-r border-slate-200 bg-white p-5 md:min-h-screen md:w-64">
        <div className="mb-10 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-600 text-xl font-bold text-white">
            D
          </div>
          <div>
            <h1 className="text-lg font-bold">DocuMind AI</h1>
            <p className="text-xs text-slate-500">Your document assistant</p>
          </div>
        </div>

        <button
          onClick={() => {
            setMessages([
              {
                role: "assistant",
                text: "Welcome to DocuMind AI. Upload a document and ask questions to explore its content.",
              },
            ]);
            setQuestion("");
            setFileName("");
          }}
          className="rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700"
        >
          + New conversation
        </button>

        <div className="mt-8">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Workspace
          </p>
          <div className="rounded-lg bg-indigo-50 px-3 py-3 text-sm font-medium text-indigo-700">
            ◈ Document chat
          </div>
          <div className="mt-2 rounded-lg px-3 py-3 text-sm text-slate-500">
            ▤ My documents
          </div>
        </div>

        <div className="mt-auto hidden rounded-xl bg-slate-50 p-4 md:block">
          <p className="text-sm font-semibold">Built for understanding</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            Find insights in your documents with AI-powered assistance.
          </p>
        </div>
      </aside>

      <section className="flex min-h-screen min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-4 md:px-9">
          <div>
            <h2 className="font-semibold">Document workspace</h2>
            <p className="mt-1 text-xs text-slate-500">
              Ask questions. Discover insights.
            </p>
          </div>
          <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
            ● Workspace ready
          </span>
        </header>

        <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col px-4 py-8 md:px-8">
          <div className="mb-8">
            <p className="mb-3 text-sm font-medium text-indigo-600">
              YOUR AI DOCUMENT ASSISTANT
            </p>
            <h2 className="text-3xl font-bold tracking-tight md:text-4xl">
              Understand your documents.
              <span className="block text-indigo-600">
                Ask better questions.
              </span>
            </h2>
            <p className="mt-4 max-w-2xl leading-7 text-slate-500">
              Upload a PDF or text document and prepare to explore its key
              ideas, summarize important sections, and find answers faster.
            </p>
          </div>

          <label className="mb-6 flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed border-indigo-200 bg-white px-5 py-8 text-center transition hover:border-indigo-400 hover:bg-indigo-50/40">
            <span className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-100 text-2xl">
              ⇧
            </span>
            <span className="font-semibold">Upload your document</span>
            <span className="mt-2 text-sm text-slate-500">
              Choose a PDF, TXT, or other document from your computer
            </span>
            <span className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white">
              Browse files
            </span>
            <input
              type="file"
              accept=".pdf,.txt,.md,.csv"
              className="hidden"
              onChange={(event) => handleUpload(event.target.files?.[0])}
            />
          </label>

          {fileName && (
            <div className="mb-5 flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
              <span className="text-xl">▤</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{fileName}</p>
                <p className="text-xs text-emerald-700">
                  Selected locally — AI processing is not connected yet
                </p>
              </div>
              <button
                onClick={() => setFileName("")}
                className="text-sm text-slate-500 hover:text-red-600"
              >
                Remove
              </button>
            </div>
          )}

          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-semibold">Conversation</h3>
            <span className="text-xs text-slate-400">AI assistant</span>
          </div>

          <div className="flex-1 space-y-4 rounded-2xl border border-slate-200 bg-white p-4 md:p-6">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`flex gap-3 ${
                  message.role === "user" ? "justify-end" : "justify-start"
                }`}
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
                </div>
              </div>
            ))}
          </div>

          <form
            className="mt-4 flex gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm"
            onSubmit={(event) => {
              event.preventDefault();
              sendMessage();
            }}
          >
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask a question about your document..."
              className="min-w-0 flex-1 bg-transparent px-3 py-3 text-sm outline-none"
            />
            <button
              type="submit"
              className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-semibold text-white hover:bg-indigo-700"
            >
              Send ↗
            </button>
          </form>

          <p className="mt-3 text-center text-xs text-slate-400">
            Preview interface · AI answers require a connected backend
          </p>
        </div>
      </section>
    </main>
  );
}
