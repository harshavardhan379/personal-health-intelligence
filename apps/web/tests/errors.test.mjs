import assert from "node:assert/strict";
import test from "node:test";
import { NETWORK_MESSAGE, describeFailure } from "../src/lib/errors.ts";

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
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
  assert.deepEqual(f, { message: "Document not found", sessionExpired: false, notFound: true });
});

test("other server errors keep the server's message", () => {
  const f = describeFailure(new ApiError(422, "Unsupported file extension: .exe"));
  assert.equal(f.message, "Unsupported file extension: .exe");
  assert.equal(f.sessionExpired || f.notFound, false);
});

test("a server error with no message falls back to the caller's wording", () => {
  assert.equal(describeFailure(new ApiError(500, ""), "Could not load documents").message, "Could not load documents");
});

test("something that is not an HTTP failure is described as the server being unreachable", () => {
  for (const odd of [new TypeError("Failed to fetch"), "boom", null, undefined, { status: "x" }]) {
    const f = describeFailure(odd);
    assert.equal(f.message, NETWORK_MESSAGE);
    assert.equal(f.sessionExpired, false);
    assert.equal(f.notFound, false);
  }
  assert.equal(describeFailure(new TypeError("x"), "Could not load").message, "Could not load");
});
