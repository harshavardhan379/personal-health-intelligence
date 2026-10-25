// How a failed request is described to the user. No imports, so Node's test runner can load it
// (tests/errors.test.mjs). It recognises the app's ApiError by its `status`, not by class.

export type Failure = {
  message: string;
  // The login is no longer valid: the page offers "Log in" instead of "Try again".
  sessionExpired: boolean;
  // The thing asked for is not there (or is not yours): no point offering a retry.
  notFound: boolean;
};

export const NETWORK_MESSAGE =
  "The server could not be reached, or it ran into a problem. Please try again.";

function statusOf(err: unknown): number | null {
  if (err && typeof err === "object" && "status" in err) {
    const status = (err as { status: unknown }).status;
    if (typeof status === "number") return status;
  }
  return null;
}

export function describeFailure(err: unknown, fallback = NETWORK_MESSAGE): Failure {
  const status = statusOf(err);
  if (status === 401) {
    return { message: "Your session has expired. Please log in again.", sessionExpired: true, notFound: false };
  }
  if (status !== null) {
    const message = err instanceof Error && err.message ? err.message : fallback;
    return { message, sessionExpired: false, notFound: status === 404 };
  }
  return { message: fallback, sessionExpired: false, notFound: false };
}
