"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { Async, Badge, Card, EmptyState, ErrorState, LoadingState } from "@/components/states";
import { EXAMPLE_QUESTIONS, documentHref } from "@/lib/assistant-format";
import {
  documentStatusLabel,
  documentStatusStyle,
  documentTypeLabel,
  eventTitle,
  eventTypeLabel,
  eventTypeStyle,
  formatEventDate,
  formatTimestamp,
  sparkline,
  timelinePositions,
  trendLabel,
} from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/useApi";
import type { DocumentOut } from "@/app/documents/page";
import type { TimelineEvent } from "@/app/timeline/page";

type Trend = {
  test_name: string;
  points: { date: string; value: number; unit: string }[];
  direction: string;
};

const MAX_TRENDS = 4;
const UNAVAILABLE = <p className="mt-3 text-sm text-slate-500">Unavailable right now.</p>;
const RECENT = 5;

function Tile({ label, value, href, hint }: { label: string; value: number | string; href: string; hint?: string }) {
  return (
    <Link href={href} className="rounded-lg border border-slate-200 bg-white p-4 hover:border-teal-600">
      <p className="text-3xl font-semibold">{value}</p>
      <p className="text-sm text-slate-600">{label}</p>
      {hint && <p className="mt-1 text-xs text-amber-700">{hint}</p>}
    </Link>
  );
}

function AskCard() {
  const router = useRouter();
  const [text, setText] = useState("");
  function go(question: string) {
    const q = question.trim();
    if (q) router.push(`/assistant?ask=${encodeURIComponent(q)}`);
  }
  return (
    <Card
      title="Ask about your records"
      description="Answers come only from what you have uploaded and confirmed, with the source for every statement."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          go(text);
        }}
        className="mt-4 flex flex-col gap-2 sm:flex-row"
      >
        <label htmlFor="dashboard-question" className="sr-only">
          Your question
        </label>
        <input
          id="dashboard-question"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          placeholder="e.g. What is my latest HbA1c?"
          autoComplete="off"
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          className="rounded bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800"
        >
          Ask
        </button>
      </form>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">Try:</span>
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => go(q)}
            className="rounded-full border border-slate-300 px-3 py-1 hover:bg-slate-100"
          >
            {q}
          </button>
        ))}
      </div>
    </Card>
  );
}

function RecentEvents({ events }: { events: TimelineEvent[] }) {
  const latest = [...events]
    .sort((a, b) => (a.event_date < b.event_date ? 1 : a.event_date > b.event_date ? -1 : 0))
    .slice(0, RECENT);
  return (
    <ul className="mt-3 divide-y divide-slate-200">
      {latest.map((event) => {
        const source = event.provenance[0];
        return (
          <li key={event.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <Badge className={eventTypeStyle(event.event_type).badge}>{eventTypeLabel(event.event_type)}</Badge>
            <span className="font-medium">{eventTitle(event.event_type, event.data)}</span>
            <span className="text-xs text-slate-500">
              {formatEventDate(event.event_date, event.event_date_precision)}
            </span>
            {source && (
              <Link
                href={documentHref(source.document_id, source.page_number)}
                className="ml-auto text-xs text-teal-700 underline"
              >
                Source
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const LANES = ["lab", "medication", "encounter", "symptom"];

// Every confirmed event as a dot from the first date to the last, one lane per type so facts
// from the same day sit side by side instead of on top of each other.
function TimelinePreview({ events }: { events: TimelineEvent[] }) {
  const placed = timelinePositions(events);
  const sorted = [...events].map((e) => e.event_date).sort();
  const lane = (type: string) => {
    const i = LANES.indexOf(type);
    return i === -1 ? LANES.length : i;
  };
  const lanes = LANES.length + 1;
  return (
    <div className="mt-4">
      <div className="relative h-24">
        {LANES.concat("other").map((_, i) => (
          <div
            key={i}
            className="absolute inset-x-0 h-px -translate-y-1/2 bg-slate-100"
            style={{ top: `${((i + 0.5) / lanes) * 100}%` }}
          />
        ))}
        {placed.map(({ event, percent }) => (
          <span
            key={event.id}
            title={`${formatEventDate(event.event_date, event.event_date_precision)}: ${eventTitle(event.event_type, event.data)}`}
            className={`absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white ${eventTypeStyle(event.event_type).dot}`}
            style={{ left: `${percent}%`, top: `${((lane(event.event_type) + 0.5) / lanes) * 100}%` }}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-slate-500">
        <span>{formatEventDate(sorted[0], "day")}</span>
        <span>{formatEventDate(sorted[sorted.length - 1], "day")}</span>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {Object.entries(
          events.reduce<Record<string, number>>((n, e) => ({ ...n, [e.event_type]: (n[e.event_type] ?? 0) + 1 }), {}),
        )
          .map(([type, count]) => `${count} ${eventTypeLabel(type).toLowerCase()}`)
          .join(" · ")}
      </p>
    </div>
  );
}

function TrendCard({ name }: { name: string }) {
  const trend = useApi<Trend>(`/trends/${encodeURIComponent(name)}`, { fallback: `Could not load ${name}` });
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <Async state={trend} loadingLabel={`Loading ${name}...`}>
        {(t) => {
          const points = t.points.map((p) => ({ date: p.date, value: p.value }));
          const last = t.points[t.points.length - 1];
          const { path, dots } = sparkline(points, 160, 48);
          return (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <p className="text-sm font-medium">{t.test_name}</p>
                <p className="text-xs text-slate-500">{trendLabel(t.direction)}</p>
              </div>
              <p className="text-xl font-semibold">
                {last.value} <span className="text-sm font-normal text-slate-500">{last.unit}</span>
              </p>
              <svg
                viewBox="0 0 160 48"
                className="mt-1 h-12 w-full"
                role="img"
                aria-label={`${t.test_name}: ${t.points.length} results from ${t.points[0].date} to ${last.date}`}
              >
                {path && dots.length > 1 && <path d={path} fill="none" stroke="#0f766e" strokeWidth="2" />}
                {dots.map((d, i) => (
                  <circle key={i} cx={d.x} cy={d.y} r="3" fill="#0f766e" />
                ))}
              </svg>
              <p className="text-xs text-slate-500">
                {t.points.length === 1
                  ? `One result, ${formatEventDate(last.date, "day")}`
                  : `${t.points.length} results, ${formatEventDate(t.points[0].date, "day")} to ${formatEventDate(last.date, "day")}`}
              </p>
            </>
          );
        }}
      </Async>
    </div>
  );
}

function DashboardInner() {
  const { user } = useAuth();
  const timeline = useApi<TimelineEvent[]>("/timeline", { fallback: "Could not load your timeline" });
  const documents = useApi<DocumentOut[]>("/documents", { fallback: "Could not load your documents" });
  const pending = useApi<{ id: string }[]>("/events?status_filter=pending");

  const labNames = Array.from(
    new Set((timeline.data ?? []).filter((e) => e.event_type === "lab").map((e) => String(e.data.test_name))),
  ).slice(0, MAX_TRENDS);
  const pendingCount = pending.data?.length ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Dashboard</h2>
        <p className="text-sm text-slate-500">Signed in as {user?.email}</p>
      </div>

      {timeline.error && timeline.data === null && (
        <ErrorState failure={timeline.error} onRetry={timeline.reload} />
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        <Tile label="Documents" value={documents.data?.length ?? "-"} href="/documents" />
        <Tile label="Confirmed facts on your timeline" value={timeline.data?.length ?? "-"} href="/timeline" />
        <Tile
          label="Facts awaiting your review"
          value={pending.data ? pendingCount : "-"}
          href="/review"
          hint={pendingCount > 0 ? "They are not on your timeline until you confirm them." : undefined}
        />
      </div>

      <AskCard />

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Recent health events" action={<Link href="/timeline" className="text-sm text-teal-700 underline">Full timeline</Link>}>
          <Async
            state={timeline}
            loadingLabel="Loading your events..."
            errorView={UNAVAILABLE}
            isEmpty={(events) => events.length === 0}
            empty={
              <EmptyState
                title="No confirmed facts yet"
                action={{ href: pendingCount > 0 ? "/review" : "/documents", label: pendingCount > 0 ? "Review extracted facts" : "Upload a report" }}
              >
                Upload a report and confirm what was extracted to see your history here.
              </EmptyState>
            }
          >
            {(events) => <RecentEvents events={events} />}
          </Async>
        </Card>

        <Card title="Timeline preview" description="Every confirmed fact, from your first record to your latest.">
          <Async
            state={timeline}
            loadingLabel="Loading your timeline..."
            errorView={UNAVAILABLE}
            isEmpty={(events) => events.length === 0}
            empty={<EmptyState title="Nothing to show yet" />}
          >
            {(events) => <TimelinePreview events={events} />}
          </Async>
        </Card>
      </div>

      <Card title="Trends" description="Each lab result over time. A trend says what the numbers did, not whether that is good or bad.">
        {timeline.loading && timeline.data === null ? (
          <LoadingState label="Loading trends..." />
        ) : timeline.error && timeline.data === null ? (
          UNAVAILABLE
        ) : labNames.length === 0 ? (
          <EmptyState title="No lab results yet">Confirmed lab results appear here as a chart.</EmptyState>
        ) : (
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            {labNames.map((name) => (
              <TrendCard key={name} name={name} />
            ))}
          </div>
        )}
      </Card>

      <Card title="Recent documents" action={<Link href="/documents" className="text-sm text-teal-700 underline">All documents</Link>}>
        <Async
          state={documents}
          loadingLabel="Loading your documents..."
          isEmpty={(docs) => docs.length === 0}
          empty={
            <EmptyState title="No documents yet" action={{ href: "/documents", label: "Upload a report" }}>
              Your uploads and their processing status show up here.
            </EmptyState>
          }
        >
          {(docs) => (
            <ul className="mt-3 divide-y divide-slate-200">
              {docs.slice(0, 3).map((doc) => (
                <li key={doc.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <Link href={`/documents/${doc.id}`} className="font-medium text-teal-800 hover:underline">
                    {doc.filename}
                  </Link>
                  <Badge className={documentStatusStyle(doc.processing_status)}>
                    {documentStatusLabel(doc.processing_status)}
                  </Badge>
                  <span className="ml-auto text-xs text-slate-500">
                    {documentTypeLabel(doc.classification)} · {formatTimestamp(doc.uploaded_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Card>
    </div>
  );
}

export default function Home() {
  return (
    <RequireAuth>
      <DashboardInner />
    </RequireAuth>
  );
}
