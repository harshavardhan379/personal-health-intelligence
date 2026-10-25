"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { Async, Badge, Card, EmptyState } from "@/components/states";
import { getToken } from "@/lib/api";
import {
  documentStatusLabel,
  documentStatusStyle,
  documentTypeLabel,
  formatTimestamp,
} from "@/lib/format";
import { useApi } from "@/lib/useApi";

export type DocumentOut = {
  id: string;
  filename: string;
  mime_type: string;
  processing_status: "uploaded" | "processing" | "processed" | "failed";
  classification: string | null;
  classification_confidence: number | null;
  processing_error: string | null;
  uploaded_at: string | null;
};

// Poll only while something is still being processed, so a finished list is left alone.
function pollWhileProcessing(docs: DocumentOut[] | null): number | undefined {
  return docs?.some((d) => d.processing_status === "uploaded" || d.processing_status === "processing")
    ? 3000
    : undefined;
}

function DocumentsPageInner() {
  const documents = useApi<DocumentOut[]>("/documents", {
    pollMs: pollWhileProcessing,
    fallback: "Could not load documents",
  });
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) {
      setUploadError("Please choose a file first.");
      return;
    }

    setUploadError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${API_URL}/api/v1/documents`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: formData,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.error?.message || `Upload failed (${res.status})`);
      }
      if (fileInput.current) fileInput.current.value = "";
      documents.reload();
    } catch (err) {
      setUploadError(
        err instanceof TypeError
          ? "The upload could not reach the server. Please try again."
          : err instanceof Error
            ? err.message
            : "Upload failed",
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="grid gap-6 md:grid-cols-[1fr_1.4fr]">
      <Card title="Upload a report" description="PDF, JPG, JPEG or PNG. Synthetic data only.">
        <form onSubmit={onUpload} className="mt-4 flex flex-col gap-3">
          <label htmlFor="file" className="sr-only">
            Choose a file to upload
          </label>
          <input
            id="file"
            ref={fileInput}
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            className="text-sm"
          />
          <button
            type="submit"
            disabled={uploading}
            className="rounded bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {uploading ? "Uploading..." : "Upload"}
          </button>
          {uploadError && (
            <p role="alert" className="text-sm text-red-600">
              {uploadError}
            </p>
          )}
        </form>
      </Card>

      <Card title="Your documents">
        <Async
          state={documents}
          loadingLabel="Loading your documents..."
          isEmpty={(docs) => docs.length === 0}
          empty={
            <EmptyState title="No documents yet">
              Upload a lab report, prescription or discharge summary and its facts will appear
              here for you to review.
            </EmptyState>
          }
        >
          {(docs) => (
            <ul className="mt-3 divide-y divide-slate-200">
              {docs.map((doc) => (
                <li key={doc.id} id={`doc-${doc.id}`} className="scroll-mt-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <Link
                      href={`/documents/${doc.id}`}
                      className="truncate text-sm font-medium text-teal-800 hover:underline"
                    >
                      {doc.filename}
                    </Link>
                    <Badge className={`shrink-0 ${documentStatusStyle(doc.processing_status)}`}>
                      {documentStatusLabel(doc.processing_status)}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {documentTypeLabel(doc.classification)}
                    {doc.classification_confidence !== null &&
                      ` (${Math.round(doc.classification_confidence * 100)}% sure)`}
                    {" · "}Uploaded {formatTimestamp(doc.uploaded_at)}
                  </p>
                  {doc.processing_error && (
                    <p className="mt-1 text-xs text-red-600">{doc.processing_error}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Card>
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <RequireAuth>
      <DocumentsPageInner />
    </RequireAuth>
  );
}
