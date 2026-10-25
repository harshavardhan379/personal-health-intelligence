"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DeleteDocument } from "@/components/DeleteControls";
import FailedDocument from "@/components/FailedDocument";
import RequireAuth from "@/components/RequireAuth";
import { Async, Badge, Card, EmptyState, ErrorState, LoadingState } from "@/components/states";
import {
  documentStatusLabel,
  documentStatusStyle,
  documentTypeLabel,
  eventDetails,
  eventTitle,
  eventTypeLabel,
  eventTypeStyle,
  formatEventDate,
  formatTimestamp,
  precisionNote,
  reviewStatusLabel,
  reviewStatusStyle,
} from "@/lib/format";
import { useApi, useBlobUrl } from "@/lib/useApi";
import type { DocumentOut } from "@/app/documents/page";

type DocumentEvent = {
  id: string;
  event_type: string;
  event_date: string;
  event_date_precision: string;
  data: Record<string, unknown>;
  confidence: number;
  review_status: string;
  provenance: { page_number: number; quote: string }[];
};

function pollWhileProcessing(doc: DocumentOut | null): number | undefined {
  return doc && (doc.processing_status === "uploaded" || doc.processing_status === "processing")
    ? 3000
    : undefined;
}

// The original file, opened at the page an extracted fact came from.
function OriginalViewer({ id, mime, filename, page }: { id: string; mime: string; filename: string; page: number }) {
  const file = useBlobUrl(`/documents/${id}/file`);
  if (file.loading) return <LoadingState label="Loading the original document..." />;
  if (file.error || !file.url) {
    return file.error ? (
      <ErrorState failure={file.error} />
    ) : (
      <EmptyState title="The original file is not available." />
    );
  }
  if (mime.startsWith("image/")) {
    // eslint-disable-next-line @next/next/no-img-element -- a blob URL of the user's own upload
    return <img src={file.url} alt={`Original document ${filename}`} className="mt-4 max-w-full rounded border border-slate-200" />;
  }
  return (
    <iframe
      key={page}
      src={page > 0 ? `${file.url}#page=${page}` : file.url}
      title={`Original document ${filename}`}
      className="mt-4 h-[32rem] w-full rounded border border-slate-200"
    />
  );
}

function EventRow({ event, onShowPage }: { event: DocumentEvent; onShowPage: (page: number) => void }) {
  const note = precisionNote(event.event_date_precision);
  const style = eventTypeStyle(event.event_type);
  const details = eventDetails(event.event_type, event.data);
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={style.badge}>{eventTypeLabel(event.event_type)}</Badge>
        <span className="text-sm font-medium">{eventTitle(event.event_type, event.data)}</span>
        <Badge className={reviewStatusStyle(event.review_status)}>
          {reviewStatusLabel(event.review_status)}
        </Badge>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {formatEventDate(event.event_date, event.event_date_precision)}
        {note && ` (${note})`}
        {details.length > 0 && ` · ${details.join(" · ")}`}
        {` · extraction confidence ${Math.round(event.confidence * 100)}%`}
      </p>
      {event.provenance.map((source, index) => (
        <div key={index} className="mt-2 rounded border-l-2 border-slate-300 bg-slate-50 px-3 py-2 text-sm">
          <button
            type="button"
            onClick={() => onShowPage(source.page_number)}
            className="text-xs font-medium text-teal-700 underline"
          >
            Show page {source.page_number}
          </button>
          <blockquote className="mt-1 whitespace-pre-wrap text-slate-700">{source.quote}</blockquote>
        </div>
      ))}
    </li>
  );
}

function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const requested = Number(search.get("page"));
  const [page, setPage] = useState(Number.isInteger(requested) && requested > 0 ? requested : 0);

  const doc = useApi<DocumentOut>(`/documents/${id}`, {
    pollMs: pollWhileProcessing,
    fallback: "Could not load this document",
  });
  const events = useApi<DocumentEvent[]>(`/documents/${id}/events`, {
    pollMs: () => pollWhileProcessing(doc.data),
    fallback: "Could not load the extracted information",
  });

  return (
    <div className="space-y-6">
      <p className="text-sm">
        <Link href="/documents" className="text-teal-700 underline">
          &larr; All documents
        </Link>
      </p>

      <Card>
        <Async state={doc} loadingLabel="Loading the document...">
          {(d) => (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="break-all text-xl font-semibold">{d.filename}</h2>
                <Badge className={documentStatusStyle(d.processing_status)}>
                  {documentStatusLabel(d.processing_status)}
                </Badge>
              </div>
              <p className="mt-1 text-sm text-slate-500">
                {documentTypeLabel(d.classification)} · Uploaded {formatTimestamp(d.uploaded_at)}
              </p>
              {d.processing_status === "failed" && (
                <FailedDocument
                  stage={d.failed_stage}
                  reason={d.processing_error}
                  documentId={d.id}
                  onRetried={() => {
                    doc.reload();
                    events.reload();
                  }}
                />
              )}
              <DeleteDocument id={d.id} onDeleted={() => router.push("/documents")} />
            </>
          )}
        </Async>
      </Card>

      {doc.data && (
        <Card title="Original document" description="The file exactly as you uploaded it.">
          <OriginalViewer id={id} mime={doc.data.mime_type} filename={doc.data.filename} page={page} />
        </Card>
      )}

      {doc.data && (
        <Card
          title="Extracted information"
          description="What was read from this document, where it was found, and whether you have confirmed it."
        >
          <Async
            state={events}
            loadingLabel="Loading the extracted information..."
            isEmpty={(list) => list.length === 0}
            empty={
              <EmptyState title="Nothing was extracted from this document">
                {doc.data?.processing_status === "failed"
                  ? "Processing failed, so there is nothing to review."
                  : doc.data?.processing_status === "processed"
                    ? "No facts the system recognises were found in it."
                    : "It is still being processed; this page updates by itself."}
              </EmptyState>
            }
          >
            {(list) => {
              const pending = list.filter((e) => e.review_status === "pending").length;
              return (
                <>
                  {pending > 0 && (
                    <p className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
                      {pending} {pending === 1 ? "fact is" : "facts are"} waiting for your review and not
                      on your timeline yet.{" "}
                      <Link href="/review" className="underline">
                        Review now
                      </Link>
                    </p>
                  )}
                  <ul className="mt-2 divide-y divide-slate-200">
                    {list.map((event) => (
                      <EventRow key={event.id} event={event} onShowPage={setPage} />
                    ))}
                  </ul>
                </>
              );
            }}
          </Async>
        </Card>
      )}
    </div>
  );
}

export default function DocumentDetailPage() {
  return (
    <RequireAuth>
      <Suspense fallback={<LoadingState />}>
        <DocumentDetail />
      </Suspense>
    </RequireAuth>
  );
}
