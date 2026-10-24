import Link from "next/link";
import {
  AssistantAnswer,
  LABEL_TEXT,
  VerificationLabel,
  documentHref,
  isBlocked,
  splitCitations,
} from "@/lib/assistant-format";

const LABEL_STYLE: Record<VerificationLabel, string> = {
  SUPPORTED: "bg-green-100 text-green-800",
  UNSUPPORTED: "bg-amber-100 text-amber-800",
  CONTRADICTED: "bg-red-100 text-red-800",
  INSUFFICIENT: "bg-slate-100 text-slate-700",
};

const BLOCKED_TITLE: Record<string, string> = {
  CLARIFY: "Could you rephrase that?",
  ABSTAIN: "This assistant can't answer that",
  REDIRECT_TO_PROFESSIONAL: "Please talk to a clinician about this",
};

function sourceId(entryId: number, number: number): string {
  return `answer-${entryId}-source-${number}`;
}

function Citation({ entryId, number, known }: { entryId: number; number: number; known: boolean }) {
  if (!known) return <span className="text-slate-400">[{number}]</span>;
  return (
    <a
      href={`#${sourceId(entryId, number)}`}
      className="mx-0.5 rounded bg-teal-50 px-1 text-xs font-medium text-teal-800 hover:bg-teal-100"
      aria-label={`Source ${number}`}
    >
      {number}
    </a>
  );
}

export default function AnswerCard({ entryId, answer }: { entryId: number; answer: AssistantAnswer }) {
  if (isBlocked(answer)) {
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 p-4">
        <p className="font-medium text-amber-900">
          {BLOCKED_TITLE[answer.safety_action ?? ""] ?? "This question was not answered"}
        </p>
        <p className="mt-1 whitespace-pre-wrap text-sm text-amber-900">{answer.answer}</p>
      </div>
    );
  }

  const known = new Set(answer.evidence.map((item) => item.number));

  return (
    <div className="space-y-4">
      <div>
        <p className="whitespace-pre-wrap leading-relaxed">
          {splitCitations(answer.answer).map((part, index) =>
            part.kind === "text" ? (
              <span key={index}>{part.text}</span>
            ) : (
              <Citation key={index} entryId={entryId} number={part.number} known={known.has(part.number)} />
            ),
          )}
        </p>
        <p
          className={
            "mt-2 inline-block rounded-full px-2 py-0.5 text-xs " +
            (answer.fully_verified ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800")
          }
        >
          {answer.fully_verified
            ? "Every statement was checked against your records"
            : "Not every statement could be checked against your records"}
        </p>
      </div>

      {answer.uncertainty && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">What is uncertain</p>
          <p className="mt-1 whitespace-pre-wrap">{answer.uncertainty}</p>
        </div>
      )}

      {answer.claim_verifications.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-slate-700">How each statement was checked</h4>
          <ul className="mt-2 space-y-2">
            {answer.claim_verifications.map((claim, index) => (
              <li key={index} className="rounded-md border border-slate-200 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={"rounded-full px-2 py-0.5 text-xs " + LABEL_STYLE[claim.label]}
                  >
                    {LABEL_TEXT[claim.label]}
                  </span>
                  {claim.citation_indices.map((number) => (
                    <Citation
                      key={number}
                      entryId={entryId}
                      number={number}
                      known={known.has(number)}
                    />
                  ))}
                </div>
                <p className="mt-1">{claim.claim_text}</p>
                {claim.explanation && (
                  <p className="mt-1 text-xs text-slate-500">{claim.explanation}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4 className="text-sm font-semibold text-slate-700">Sources</h4>
        {answer.evidence.length === 0 ? (
          <p className="mt-1 text-sm text-slate-500">No sources were found for this answer.</p>
        ) : (
          <ol className="mt-2 space-y-2">
            {answer.evidence.map((item) => (
              <li
                key={item.number}
                id={sourceId(entryId, item.number)}
                className="scroll-mt-4 rounded-md border border-slate-200 bg-slate-50 p-3 target:border-teal-600"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-slate-600">
                  <span className="font-medium">
                    [{item.number}] {item.filename ?? "Source document unavailable"} · page {item.page}
                  </span>
                  {item.filename && (
                    <Link href={documentHref(item.document_id)} className="text-teal-700 underline">
                      Open in Documents
                    </Link>
                  )}
                </div>
                <blockquote className="mt-1 whitespace-pre-wrap border-l-2 border-slate-300 pl-3 text-sm">
                  {item.text}
                </blockquote>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
