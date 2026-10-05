"use client";

import { useCallback, useEffect, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { apiFetch, ApiError } from "@/lib/api";

type EventOut = {
  id: string;
  event_type: string;
  event_date: string;
  data: Record<string, unknown>;
  confidence: number;
  review_status: string;
  source_pages: number[];
};

function ReviewPageInner() {
  const [events, setEvents] = useState<EventOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const pending = await apiFetch<EventOut[]>("/events?status_filter=pending");
      setEvents(pending);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load pending events");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    load();
  }, [load]);

  async function act(id: string, action: "confirm" | "reject") {
    setBusyId(id);
    setError(null);
    try {
      await apiFetch(`/events/${id}/${action}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${action} event`);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6">
      <h2 className="text-xl font-semibold">Review extracted facts</h2>
      <p className="mt-1 text-sm text-slate-500">
        Nothing here becomes part of your timeline until you confirm it.
      </p>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {events.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">Nothing pending review.</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
          {events.map((event) => (
            <li key={event.id} className="rounded border border-slate-200 p-4">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase text-slate-500">
                  {event.event_type}
                </span>
                <span className="text-xs text-slate-400">
                  {event.event_date} · {Math.round(event.confidence * 100)}% confidence · page{" "}
                  {event.source_pages.join(", ") || "?"}
                </span>
              </div>
              <pre className="mt-2 whitespace-pre-wrap text-sm text-slate-700">
                {JSON.stringify(event.data, null, 2)}
              </pre>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => act(event.id, "confirm")}
                  disabled={busyId === event.id}
                  className="rounded bg-teal-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  Confirm
                </button>
                <button
                  onClick={() => act(event.id, "reject")}
                  disabled={busyId === event.id}
                  className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function ReviewPage() {
  return (
    <RequireAuth>
      <ReviewPageInner />
    </RequireAuth>
  );
}
