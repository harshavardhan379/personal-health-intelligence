import assert from "node:assert/strict";
import test from "node:test";
import {
  NETWORK_MESSAGE,
  SERVER_MESSAGE,
  describeFailure,
  newRequestId,
  requestIdFromResponse,
} from "../src/lib/errors.ts";

class ApiError extends Error {
  constructor(status, message, requestId = null) {
    super(message);
    this.status = status;
    this.requestId = requestId;
  }
}

test("an expired login is described as such and is not retryable", () => {
  const f = describeFailure(new ApiError(401, "Invalid token"));
  assert.equal(f.sessionExpired, true);
  assert.equal(f.notFound, false);
  assert.match(f.message, /log in again/i);
});

test("a 404 carries the server's own words and is marked not found", () => {
  const f = describeFailure(new ApiError(404, "Document not found"));
  assert.deepEqual(f, {
    message: "Document not found",
    sessionExpired: false,
    notFound: true,
    requestId: null,
  });
});

test("other server errors keep the server's message", () => {
  const f = describeFailure(new ApiError(422, "Unsupported file extension: .exe"));
  assert.equal(f.message, "Unsupported file extension: .exe");
  assert.equal(f.sessionExpired || f.notFound, false);
});

test("a client error with no message falls back to the caller's wording", () => {
  assert.equal(describeFailure(new ApiError(422, ""), "Could not load documents").message, "Could not load documents");
});

test("a crash is described kindly, not as 'Internal server error'", () => {
  for (const status of [500, 502, 504]) {
    const f = describeFailure(new ApiError(status, "Internal server error", "abc123"));
    assert.equal(f.message, SERVER_MESSAGE);
    assert.equal(f.requestId, "abc123");
  }
});

test("a 503 that explains itself keeps its explanation", () => {
  const f = describeFailure(new ApiError(503, "The file could not be stored. Please try again."));
  assert.equal(f.message, "The file could not be stored. Please try again.");
});

test("the request id of a failure is carried to the page", () => {
  assert.equal(describeFailure(new ApiError(422, "Bad", "req-9")).requestId, "req-9");
  assert.equal(describeFailure(new ApiError(404, "Gone", "req-8")).requestId, "req-8");
  assert.equal(describeFailure(new ApiError(401, "No", "req-7")).requestId, "req-7");
  assert.equal(describeFailure(new ApiError(500, "x")).requestId, null);
});

test("a request id that is not a non-empty string is ignored", () => {
  for (const odd of [42, "", {}, undefined]) {
    const err = new ApiError(500, "x");
    err.requestId = odd;
    assert.equal(describeFailure(err).requestId, null);
  }
});

test("a new request id is 32 hex characters and different each time", () => {
  const a = newRequestId();
  assert.match(a, /^[0-9a-f]{32}$/);
  assert.notEqual(a, newRequestId());
});

test("a new request id passes the API's own rule for ids a caller may send", () => {
  assert.match(newRequestId(), /^[A-Za-z0-9._-]{1,64}$/);
});

test("the response header wins over the body's id, and either is enough", () => {
  assert.equal(requestIdFromResponse("from-header", { error: { request_id: "from-body" } }), "from-header");
  assert.equal(requestIdFromResponse(null, { error: { request_id: "from-body" } }), "from-body");
  assert.equal(requestIdFromResponse(null, null), null);
  assert.equal(requestIdFromResponse(null, { error: { request_id: "" } }), null);
  assert.equal(requestIdFromResponse(null, { error: { request_id: 5 } }), null);
});

test("something that is not an HTTP failure is described as the server being unreachable", () => {
  for (const odd of [new TypeError("Failed to fetch"), "boom", null, undefined, { status: "x" }]) {
    const f = describeFailure(odd);
    assert.equal(f.message, NETWORK_MESSAGE);
    assert.equal(f.sessionExpired, false);
    assert.equal(f.notFound, false);
    assert.equal(f.requestId, null);
  }
  assert.equal(describeFailure(new TypeError("x"), "Could not load").message, "Could not load");
});

test("without crypto.randomUUID (an older browser) an id is still 32 hex characters and unique", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
  try {
    const a = newRequestId();
    assert.match(a, /^[0-9a-f]{32}$/);
    assert.notEqual(a, newRequestId());
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original);
    else delete globalThis.crypto;
  }
});
