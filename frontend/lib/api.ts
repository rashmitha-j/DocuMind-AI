export const API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "");

// Allow for a Render cold start (~1 min) plus a Gemini answer that may need the fallback model.
const REQUEST_TIMEOUT_MS = 180_000;

export type User = { id: number; email: string };

export type DocumentInfo = {
  id: string;
  filename: string;
  size_bytes: number;
  pages: number;
  characters: number;
  chunk_count: number;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
  updated_at: string | null;
};

export type Source = {
  label: string;
  chunk_id: number;
  document_id: string;
  filename: string;
  page: number;
  score: number;
  excerpt: string;
};

export type Usage = {
  documents: number;
  max_documents: number;
  bytes: number;
  max_bytes: number;
};

export type UploadResult = {
  filename: string;
  ok: boolean;
  duplicate?: boolean;
  document?: DocumentInfo;
  error?: string;
  status_code?: number;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

type RequestOptions = {
  method?: string;
  token?: string | null;
  json?: unknown;
  body?: BodyInit;
  raw?: boolean;
};

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!API_URL) {
    throw new ApiError(
      "Backend URL is not configured. Set NEXT_PUBLIC_API_URL for the frontend.",
      0,
    );
  }

  const headers: Record<string, string> = {};
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.json !== undefined) headers["Content-Type"] = "application/json";

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
      signal: controller.signal,
    });

    if (options.raw && response.ok) return (await response.blob()) as T;
    if (response.status === 204) return undefined as T;

    const result = await response.json().catch(() => ({}));

    if (!response.ok) {
      const detail =
        typeof result.detail === "string"
          ? result.detail
          : response.status === 422
            ? "Please check the values you entered."
            : `The backend returned an error (HTTP ${response.status}).`;
      throw new ApiError(detail, response.status);
    }

    return result as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The backend took too long to respond. Please try again.", 0);
    }
    throw new ApiError(
      "Could not reach the backend. It may be waking up from sleep; please try again in a minute.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}

export function formatSize(bytes: number) {
  if (bytes === 0) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
