"use client";

import { useEffect, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { apiFetch, ApiError } from "@/lib/api";

type TimelineEvent = {
  id: string;
  event_type: string;
  event_date: string;
  event_date_precision: string;
  data: Record<string, unknown>;
  confidence: number;
  review_status: string;
  provenance: { document_id: string; page_number: number; quote: string }[];
};

function describeEvent(event: TimelineEvent): string {
  switch (event.event_type) {
    case "lab":
      return `${event.data.test_name}: ${event.data.value} ${event.data.unit}`;
    case "medication":
      return `${event.data.medication}${event.data.dose ? ` ${event.data.dose}` : ""}`;
    case "encounter":
      return String(event.data.encounter_type);
    case "symptom":
      return `Symptom: ${event.data.symptom}`;
    default:
      return event.event_type;
  }
}

function TimelinePageInner() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<TimelineEvent[]>("/timeline")
      .then(setEvents)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Could not load timeline")
      );
  }, []);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6">
      <h2 className="text-xl font-semibold">Health timeline</h2>
      <p className="mt-1 text-sm text-slate-500">Confirmed events only, in chronological order.</p>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {events.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">
          Nothing here yet. Upload a report and confirm its extracted facts to see it here.
        </p>
      ) : (
        <ol className="mt-4 border-l-2 border-slate-200 pl-4">
          {events.map((event) => (
            <li key={event.id} className="mb-4">
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span>{event.event_date}</span>
                {event.event_date_precision !== "day" && (
                  <span className="italic">({event.event_date_precision} only)</span>
                )}
              </div>
              <p className="text-sm font-medium">{describeEvent(event)}</p>
              {event.provenance[0] && (
                <p className="mt-1 text-xs text-slate-500">
                  Source: page {event.provenance[0].page_number} — &ldquo;
                  {event.provenance[0].quote}&rdquo;
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function TimelinePage() {
  return (
    <RequireAuth>
      <TimelinePageInner />
    </RequireAuth>
  );
}
