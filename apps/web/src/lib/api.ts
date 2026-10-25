import { newRequestId, requestIdFromResponse } from "@/lib/errors";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "phi_access_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // ignore (private browsing, etc.) — the user will just need to log in again
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

export class ApiError extends Error {
  status: number;
  // The id of the failed request, from the server's response (YGH-91).
  requestId: string | null;
  constructor(status: number, message: string, requestId: string | null = null) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

// Reads a failed response into an ApiError, keeping the request id the server reported (or, if the
// server never answered with one, the one this client sent).
async function failureFrom(res: Response, sent: string): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let body: unknown = null;
  try {
    body = await res.json();
    message = (body as { error?: { message?: string } })?.error?.message || message;
  } catch {
    // response wasn't JSON; keep the generic message
  }
  const requestId = requestIdFromResponse(res.headers.get("X-Request-ID"), body) ?? sent;
  return new ApiError(res.status, message, requestId);
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const requestId = newRequestId();
  headers.set("X-Request-ID", requestId);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_URL}/api/v1${path}`, { ...init, headers });

  if (!res.ok) throw await failureFrom(res, requestId);

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// A file (the original document) rather than JSON. Needs the login header, so it cannot be a plain link.
export async function apiBlob(path: string): Promise<Blob> {
  const token = getToken();
  const requestId = newRequestId();
  const headers = new Headers({ "X-Request-ID": requestId });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_URL}/api/v1${path}`, { headers });
  if (!res.ok) throw await failureFrom(res, requestId);
  return res.blob();
}
