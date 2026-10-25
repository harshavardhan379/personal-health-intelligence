// Run with `npm test` (Node's built-in runner; it loads the .ts file directly).
import assert from "node:assert/strict";
import test from "node:test";
import {
  EXAMPLE_QUESTIONS,
  LABEL_TEXT,
  documentHref,
  isBlocked,
  sourceLabel,
  splitCitations,
} from "../src/lib/assistant-format.ts";

const cite = (number) => ({ kind: "citation", number });
const text = (value) => ({ kind: "text", text: value });
const answer = (safety_action) => ({ safety_action });

test("citation markers are split out of the answer text, in order", () => {
  assert.deepEqual(splitCitations("HbA1c was 7.2 [1] then 6.5 [2]."), [
    text("HbA1c was 7.2 "), cite(1), text(" then 6.5 "), cite(2), text("."),
  ]);
});

test("adjacent markers, a marker at either end, and multi-digit numbers", () => {
  assert.deepEqual(splitCitations("[1][2] both"), [cite(1), cite(2), text(" both")]);
  assert.deepEqual(splitCitations("end [12]"), [text("end "), cite(12)]);
});

test("text without markers is one text part, and an empty answer has no parts", () => {
  assert.deepEqual(splitCitations("No markers here."), [text("No markers here.")]);
  assert.deepEqual(splitCitations(""), []);
});

test("things that only look like markers are left as text", () => {
  for (const odd of ["[x]", "[ 1 ]", "[]", "[1.5]", "(1)"]) {
    assert.deepEqual(splitCitations(odd), [text(odd)], odd);
  }
});

test("only the safety actions that stop an answer count as blocked", () => {
  for (const action of ["CLARIFY", "ABSTAIN", "REDIRECT_TO_PROFESSIONAL"]) {
    assert.equal(isBlocked(answer(action)), true, action);
  }
  for (const action of ["ALLOW", "ALLOW_WITH_UNCERTAINTY", null]) {
    assert.equal(isBlocked(answer(action)), false, String(action));
  }
});

test("a source's document link scrolls to that document in the documents list", () => {
  assert.equal(documentHref("abc-123"), "/documents#doc-abc-123");
});

test("every verification label has plain-language text", () => {
  assert.deepEqual(Object.keys(LABEL_TEXT).sort(),
    ["CONTRADICTED", "INSUFFICIENT", "SUPPORTED", "UNSUPPORTED"]);
  for (const words of Object.values(LABEL_TEXT)) assert.ok(words.length > 5);
});

test("the example questions are real questions", () => {
  assert.equal(EXAMPLE_QUESTIONS.length, 3);
  for (const q of EXAMPLE_QUESTIONS) assert.ok(q.endsWith("?"), q);
});

test("a source is labelled with its file and page, and never with page 0", () => {
  assert.equal(sourceLabel("lab.pdf", 2), "lab.pdf · page 2");
  assert.equal(sourceLabel("lab.pdf", 0), "lab.pdf");
  assert.equal(sourceLabel(null, 3), "Source document unavailable · page 3");
  assert.equal(sourceLabel(null, 0), "Source document unavailable");
});
