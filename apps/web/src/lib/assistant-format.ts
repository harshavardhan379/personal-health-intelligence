// Types and pure helpers for the assistant page. No imports from the app, so Node's own
// test runner can load this file directly (tests/assistant-format.test.mjs).

// Mirrors packages/schemas/verification.py (VerifiedAnswer and what it contains).
export type VerificationLabel = "SUPPORTED" | "UNSUPPORTED" | "CONTRADICTED" | "INSUFFICIENT";

export type ClaimVerification = {
  claim_text: string;
  label: VerificationLabel;
  citation_indices: number[];
  explanation: string | null;
};

export type SourceEvidence = {
  number: number;
  text: string;
  document_id: string;
  filename: string | null;
  page: number;
  event_ids: string[];
  source: string;
};

export type AssistantAnswer = {
  answer: string;
  claim_verifications: ClaimVerification[];
  evidence_ids: string[];
  uncertainty: string | null;
  intent: string;
  fully_verified: boolean;
  safety_category: string | null;
  safety_action: string | null;
  evidence: SourceEvidence[];
};

// The safety layer's actions that stop an answer (packages/schemas/safety.py, policy.py).
const BLOCKING_ACTIONS = ["CLARIFY", "ABSTAIN", "REDIRECT_TO_PROFESSIONAL"];

export function isBlocked(answer: AssistantAnswer): boolean {
  return answer.safety_action !== null && BLOCKING_ACTIONS.includes(answer.safety_action);
}

// Where a source's document can be seen. There is no document page yet (YGH-118): until
// there is, this scrolls to the document in the documents list. Change it here, once.
export function documentHref(documentId: string): string {
  return `/documents#doc-${documentId}`;
}

export type AnswerPart = { kind: "text"; text: string } | { kind: "citation"; number: number };

// Splits an answer into plain text and its [n] citation markers.
export function splitCitations(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of answer.matchAll(/\[(\d+)\]/g)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ kind: "text", text: answer.slice(last, index) });
    parts.push({ kind: "citation", number: Number(match[1]) });
    last = index + match[0].length;
  }
  if (last < answer.length) parts.push({ kind: "text", text: answer.slice(last) });
  return parts;
}

export const LABEL_TEXT: Record<VerificationLabel, string> = {
  SUPPORTED: "Supported by the records",
  UNSUPPORTED: "Not supported by the cited source",
  CONTRADICTED: "Contradicts the records",
  INSUFFICIENT: "No source cited",
};

export const EXAMPLE_QUESTIONS = [
  "What is my latest HbA1c?",
  "Which medications am I taking?",
  "Is my HbA1c improving?",
];
