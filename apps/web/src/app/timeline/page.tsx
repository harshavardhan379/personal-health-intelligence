"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { Async, Badge, Card, EmptyState } from "@/components/states";
import {
  eventDetails,
  eventTitle,
  eventTypeLabel,
  eventTypeStyle,
  formatEventDate,
  groupByMonth,
  precisionNote,
} from "@/lib/format";
import { documentHref } from "@/lib/assistant-format";
import { useApi } from "@/lib/useApi";
import type { DocumentOut } from "@/app/documents/page";

export type TimelineEvent = {
  id: string;
  event_type: string;
  event_date: string;
  event_date_precision: string;
  data: Record<string, unknown>;
  confidence: number;
  review_status: string;
  provenance: { document_id: string; page_number: number; quote: string }[];
};

const TYPES = ["lab", "medication", "encounter", "symptom"];

function byDate(newestFirst: boolean) {
  return (a: TimelineEvent, b: TimelineEvent) =>
    (a.event_date < b.event_date ? -1 : a.event_date > b.event_date ? 1 : 0) * (newestFirst ? -1 : 1);
}

function EventItem({ event, names }: { event: TimelineEvent; names: Map<string, string> }) {
  const style = eventTypeStyle(event.event_type);
  const note = precisionNote(event.event_date_precision);
  const details = eventDetails(event.event_type, event.data);
  const source = event.provenance[0];
  return (
    <li className="relative pb-5 pl-6">
      <span className={`absolute left-0 top-1.5 h-3 w-3 rounded-full ring-4 ring-white ${style.dot}`} aria-hidden />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500">
          {formatEventDate(event.event_date, event.event_date_precision)}
          {note && <span className="italic"> ({note})</span>}
        </span>
        <Badge className={style.badge}>{eventTypeLabel(event.event_type)}</Badge>
      </div>
      <p className="mt-0.5 font-medium">{eventTitle(event.event_type, event.data)}</p>
      {details.length > 0 && <p className="text-sm text-slate-600">{details.join(" · ")}</p>}
      {source && (
        <div className="mt-1 text-xs text-slate-500">
          <Link
            href={documentHref(source.document_id, source.page_number)}
            className="text-teal-700 underline"
          >
            {names.get(source.document_id) ?? "Source document"}, page {source.page_number}
          </Link>
          <span>
            {" "}
            &mdash; &ldquo;{source.quote}&rdquo;
          </span>
        </div>
      )}
    </li>
  );
}

function TimelinePageInner() {
  const timeline = useApi<TimelineEvent[]>("/timeline", { fallback: "Could not load your timeline" });
  const documents = useApi<DocumentOut[]>("/documents");
  const pending = useApi<{ id: string }[]>("/events?status_filter=pending");
  const [type, setType] = useState<string>("all");
  const [newestFirst, setNewestFirst] = useState(true);

  const names = useMemo(
    () => new Map((documents.data ?? []).map((d) => [d.id, d.filename])),
    [documents.data],
  );
  const pendingCount = pending.data?.length ?? 0;

  return (
    <div className="space-y-4">
      <Card
        title="Health timeline"
        description="Confirmed facts only, grouped by month. Each one links to the page of the document it came from."
      >
        {pendingCount > 0 && (
          <p className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
            {pendingCount} {pendingCount === 1 ? "extracted fact is" : "extracted facts are"} waiting for
            your review and {pendingCount === 1 ? "is" : "are"} not on the timeline yet.{" "}
            <Link href="/review" className="underline">
              Review now
            </Link>
          </p>
        )}

        <Async
          state={timeline}
          loadingLabel="Loading your timeline..."
          isEmpty={(events) => events.length === 0}
          empty={
            <EmptyState
              title="Nothing on your timeline yet"
              action={{ href: pendingCount > 0 ? "/review" : "/documents", label: pendingCount > 0 ? "Review extracted facts" : "Upload a report" }}
            >
              Facts appear here once you upload a report and confirm what was extracted from it.
            </EmptyState>
          }
        >
          {(events) => {
            const counts = new Map<string, number>();
            for (const e of events) counts.set(e.event_type, (counts.get(e.event_type) ?? 0) + 1);
            const shown = events
              .filter((e) => type === "all" || e.event_type === type)
              .sort(byDate(newestFirst));
            const chip = (value: string, label: string, count: number) => (
              <button
                key={value}
                type="button"
                aria-pressed={type === value}
                onClick={() => setType(value)}
                className={
                  "rounded-full border px-3 py-1 text-sm " +
                  (type === value
                    ? "border-teal-700 bg-teal-700 text-white"
                    : "border-slate-300 hover:bg-slate-100")
                }
              >
                {label} ({count})
              </button>
            );
            return (
              <>
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  {chip("all", "All", events.length)}
                  {TYPES.filter((t) => counts.has(t)).map((t) => chip(t, eventTypeLabel(t), counts.get(t) ?? 0))}
                  <button
                    type="button"
                    onClick={() => setNewestFirst((v) => !v)}
                    className="ml-auto text-sm text-teal-700 underline"
                  >
                    {newestFirst ? "Show oldest first" : "Show newest first"}
                  </button>
                </div>

                {shown.length === 0 ? (
                  <EmptyState title={`No ${eventTypeLabel(type).toLowerCase()} entries`}>
                    <button type="button" onClick={() => setType("all")} className="text-teal-700 underline">
                      Show everything
                    </button>
                  </EmptyState>
                ) : (
                  groupByMonth(shown).map((group) => (
                    <section key={group.key} className="mt-6">
                      <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                        {group.label}
                      </h3>
                      <ol className="mt-3 border-l-2 border-slate-200 pl-3">
                        {group.items.map((event) => (
                          <EventItem key={event.id} event={event} names={names} />
                        ))}
                      </ol>
                    </section>
                  ))
                )}
              </>
            );
          }}
        </Async>
      </Card>
    </div>
  );
}

export default function TimelinePage() {
  return (
    <RequireAuth>
      <TimelinePageInner />
    </RequireAuth>
  );
}
