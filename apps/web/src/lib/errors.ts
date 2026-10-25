// How a failed request is described to the user. No imports, so Node's test runner can load it
// (tests/errors.test.mjs). It recognises the app's ApiError by its `status`, not by class.

export type Failure = {
  message: string;
  // The login is no longer valid: the page offers "Log in" instead of "Try again".
  sessionExpired: boolean;
  // The thing asked for is not there (or is not yours): no point offering a retry.
  notFound: boolean;
  // The id of the failed request, to quote when reporting a problem: it matches the server's log
  // lines (YGH-91). Null when the server was never reached (there is nothing to quote).
  requestId: string | null;
};

export const NETWORK_MESSAGE =
  "The server could not be reached, or it ran into a problem. Please try again.";
export const SERVER_MESSAGE = "Something went wrong on our side. Please try again.";

// A fresh id for one request: 32 hex characters, which the API accepts as the caller's own.
export function newRequestId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  let id = "";
  while (id.length < 32) id += Math.floor(Math.random() * 16).toString(16);
  return id;
}

// The id the server reports for a failed request: its response header, else the error body's field.
export function requestIdFromResponse(header: string | null, body: unknown): string | null {
  if (header) return header;
  const fromBody = (body as { error?: { request_id?: unknown } } | null)?.error?.request_id;
  return typeof fromBody === "string" && fromBody ? fromBody : null;
}

function statusOf(err: unknown): number | null {
  if (err && typeof err === "object" && "status" in err) {
    const status = (err as { status: unknown }).status;
    if (typeof status === "number") return status;
  }
  return null;
}

function requestIdOf(err: unknown): string | null {
  if (err && typeof err === "object" && "requestId" in err) {
    const id = (err as { requestId: unknown }).requestId;
    if (typeof id === "string" && id) return id;
  }
  return null;
}

export function describeFailure(err: unknown, fallback = NETWORK_MESSAGE): Failure {
  const status = statusOf(err);
  const requestId = requestIdOf(err);
  if (status === 401) {
    return {
      message: "Your session has expired. Please log in again.",
      sessionExpired: true,
      notFound: false,
      requestId,
    };
  }
  if (status !== null) {
    // The server's own words for what the user did wrong (4xx, or a 503 that explains itself);
    // for a crash (500, 502, 504) they are only "Internal server error", so say something kinder.
    const crashed = status >= 500 && status !== 503;
    const message = crashed ? SERVER_MESSAGE : err instanceof Error && err.message ? err.message : fallback;
    return { message, sessionExpired: false, notFound: status === 404, requestId };
  }
  return { message: fallback, sessionExpired: false, notFound: false, requestId };
}
