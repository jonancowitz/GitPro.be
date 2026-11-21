import type { OptimizeRequest, OptimizeResponse, ParsedDeckItem } from "@/types/deck";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim().replace(/\/$/, "");
  return configured || DEFAULT_API_BASE_URL;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) {
    return "Couldn't reach the API. Start the backend and check NEXT_PUBLIC_API_BASE_URL.";
  }
  return "Something went wrong talking to the API.";
}

export async function parseDeckText(text: string): Promise<ParsedDeckItem[]> {
  return postJson("/api/v1/deck/parse", { text });
}

export async function parseDeckUrl(url: string): Promise<ParsedDeckItem[]> {
  return postJson("/api/v1/deck/parse", { url });
}

export async function parseDeckFile(file: File): Promise<ParsedDeckItem[]> {
  const body = new FormData();
  body.append("file", file, file.name);
  const response = await fetch(`${apiBaseUrl()}/api/v1/deck/parse`, {
    method: "POST",
    body,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw await errorFromResponse(response);
  return (await response.json()) as ParsedDeckItem[];
}

export async function optimizeDeck(request: OptimizeRequest): Promise<OptimizeResponse> {
  return postJson("/api/v1/optimize", request);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await errorFromResponse(response);
  return (await response.json()) as T;
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let code = "request_failed";
  let message = `The API returned ${response.status}.`;
  try {
    const parsed = readDetail(await response.json());
    if (parsed) {
      code = parsed.code;
      message = parsed.message;
    }
  } catch {
    // Keep the status fallback when the body is not JSON.
  }
  return new ApiError(response.status, code, message);
}

function readDetail(body: unknown): { code: string; message: string } | null {
  if (!isRecord(body) || !("detail" in body)) return null;
  const detail = body.detail;
  if (typeof detail === "string" && detail.trim()) {
    return { code: "request_failed", message: detail };
  }
  if (isRecord(detail)) {
    const message = typeof detail.message === "string" ? detail.message : null;
    const code = typeof detail.code === "string" ? detail.code : "request_failed";
    if (message) return { code, message };
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      if (!isRecord(entry) || typeof entry.msg !== "string") return [];
      return [entry.msg];
    });
    if (messages.length > 0) return { code: "invalid_body", message: messages.join(" ") };
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
