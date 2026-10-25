"use client";

import Link from "next/link";
import { useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { Async, Badge, Card, EmptyState } from "@/components/states";
import { apiFetch } from "@/lib/api";
import { describeFailure } from "@/lib/errors";
import {
  eventDetails,
  eventTitle,
  eventTypeLabel,
  eventTypeStyle,
  formatEventDate,
} from "@/lib/format";
import { useApi } from "@/lib/useApi";

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
  const pending = useApi<EventOut[]>("/events?status_filter=pending", {
    fallback: "Could not load pending events",
  });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function act(id: string, action: "confirm" | "reject") {
    setBusyId(id);
    setActionError(null);
    try {
      await apiFetch(`/events/${id}/${action}`, { method: "POST" });
      pending.reload();
    } catch (err) {
      setActionError(describeFailure(err, `Could not ${action} this fact. Please try again.`).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card
      title="Review extracted facts"
      description="Nothing here becomes part of your timeline until you confirm it."
    >
      {actionError && (
        <p role="alert" className="mt-2 text-sm text-red-600">
          {actionError}
        </p>
      )}
      <Async
        state={pending}
        loadingLabel="Loading facts to review..."
        isEmpty={(events) => events.length === 0}
        empty={
          <EmptyState
            title="Nothing is waiting for review"
            action={{ href: "/timeline", label: "Open your timeline" }}
          >
            New facts appear here after you upload a document.{" "}
            <Link href="/documents" className="text-teal-700 underline">
              Upload one
            </Link>
          </EmptyState>
        }
      >
        {(events) => (
          <ul className="mt-4 flex flex-col gap-3">
            {events.map((event) => {
              const details = eventDetails(event.event_type, event.data);
              return (
                <li key={event.id} className="rounded border border-slate-200 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={eventTypeStyle(event.event_type).badge}>
                      {eventTypeLabel(event.event_type)}
                    </Badge>
                    <span className="font-medium">{eventTitle(event.event_type, event.data)}</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {formatEventDate(event.event_date, "day")}
                    {details.length > 0 && ` · ${details.join(" · ")}`}
                    {` · ${Math.round(event.confidence * 100)}% confidence · page ${event.source_pages.join(", ") || "?"}`}
                  </p>
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
              );
            })}
          </ul>
        )}
      </Async>
    </Card>
  );
}

export default function ReviewPage() {
  return (
    <RequireAuth>
      <ReviewPageInner />
    </RequireAuth>
  );
}
