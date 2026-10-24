"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import AnswerCard from "@/components/assistant/AnswerCard";
import RequireAuth from "@/components/RequireAuth";
import { ApiError } from "@/lib/api";
import { AssistantAnswer, EXAMPLE_QUESTIONS } from "@/lib/assistant-format";
import { askAssistant } from "@/lib/assistant";

const MAX_QUESTION_LENGTH = 500;

type Entry = {
  id: number;
  question: string;
  status: "pending" | "done" | "error";
  answer?: AssistantAnswer;
  error?: string;
  sessionExpired?: boolean;
};

function describeError(err: unknown): Pick<Entry, "error" | "sessionExpired"> {
  if (err instanceof ApiError && err.status === 401) {
    return { error: "Your session has expired. Please log in again.", sessionExpired: true };
  }
  if (err instanceof ApiError) return { error: err.message };
  // A failed fetch: the server is unreachable, or it answered with a server error the browser
  // is not allowed to read (the API sends no CORS headers on a 500). Either way, say so plainly.
  return { error: "The assistant could not be reached, or it ran into a problem. Please try again." };
}

function AssistantPageInner() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [text, setText] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const nextId = useRef(1);
  const busy = entries.some((entry) => entry.status === "pending");

  async function ask(question: string, replaceId?: number) {
    const id = replaceId ?? nextId.current++;
    const pending: Entry = { id, question, status: "pending" };
    setEntries((all) =>
      replaceId === undefined ? [pending, ...all] : all.map((e) => (e.id === id ? pending : e)),
    );
    try {
      const answer = await askAssistant(question);
      setEntries((all) =>
        all.map((e) => (e.id === id ? { id, question, status: "done", answer } : e)),
      );
    } catch (err) {
      setEntries((all) =>
        all.map((e) => (e.id === id ? { id, question, status: "error", ...describeError(err) } : e)),
      );
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const question = text.trim();
    if (!question) {
      setFormError("Type a question about your records first.");
      return;
    }
    setFormError(null);
    setText("");
    void ask(question);
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="text-xl font-semibold">Ask about your records</h2>
        <p className="mt-1 text-sm text-slate-500">
          Answers come only from documents you uploaded and confirmed, and every statement shows
          where it came from. This is not medical advice.
        </p>

        <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3 sm:flex-row">
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <input
            id="question"
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={MAX_QUESTION_LENGTH}
            placeholder="e.g. What is my latest HbA1c?"
            autoComplete="off"
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {busy ? "Looking..." : "Ask"}
          </button>
        </form>
        {formError && (
          <p role="alert" className="mt-2 text-sm text-red-600">
            {formError}
          </p>
        )}

        {entries.length === 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-slate-500">Try:</span>
            {EXAMPLE_QUESTIONS.map((example) => (
              <button
                key={example}
                type="button"
                disabled={busy}
                onClick={() => void ask(example)}
                className="rounded-full border border-slate-300 px-3 py-1 hover:bg-slate-100 disabled:opacity-50"
              >
                {example}
              </button>
            ))}
          </div>
        )}
      </section>

      <div aria-live="polite" className="space-y-4">
        {entries.map((entry) => (
          <article key={entry.id} className="rounded-lg border border-slate-200 bg-white p-6">
            <h3 className="font-semibold">{entry.question}</h3>
            <div className="mt-3">
              {entry.status === "pending" && (
                <p className="text-sm text-slate-500">Looking through your records...</p>
              )}
              {entry.status === "error" && (
                <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm">
                  <p className="text-red-800">{entry.error}</p>
                  {entry.sessionExpired ? (
                    <Link href="/login" className="mt-2 inline-block text-teal-700 underline">
                      Log in
                    </Link>
                  ) : (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void ask(entry.question, entry.id)}
                      className="mt-2 text-teal-700 underline disabled:opacity-50"
                    >
                      Try again
                    </button>
                  )}
                </div>
              )}
              {entry.status === "done" && entry.answer && (
                <AnswerCard entryId={entry.id} answer={entry.answer} />
              )}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

export default function AssistantPage() {
  return (
    <RequireAuth>
      <AssistantPageInner />
    </RequireAuth>
  );
}
