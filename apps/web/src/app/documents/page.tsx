"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import RequireAuth from "@/components/RequireAuth";
import { apiFetch, ApiError, getToken } from "@/lib/api";

type DocumentOut = {
  id: string;
  filename: string;
  mime_type: string;
  processing_status: "uploaded" | "processing" | "processed" | "failed";
  classification: string | null;
  classification_confidence: number | null;
  processing_error: string | null;
};

const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded",
  processing: "Processing...",
  processed: "Processed",
  failed: "Failed",
};

function DocumentsPageInner() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const docs = await apiFetch<DocumentOut[]>("/documents");
      setDocuments(docs);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load documents");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    load();
    // Poll so "Processing..." flips to "Processed" without a manual refresh.
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
  }, [load]);

  async function onUpload(e: React.FormEvent) {
    e.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) return;

    setError(null);
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
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="grid gap-6 md:grid-cols-[1fr_1.4fr]">
      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="text-xl font-semibold">Upload a report</h2>
        <p className="mt-1 text-sm text-slate-500">PDF, JPG, JPEG or PNG. Synthetic data only.</p>
        <form onSubmit={onUpload} className="mt-4 flex flex-col gap-3">
          <input
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
          {error && <p className="text-sm text-red-600">{error}</p>}
        </form>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-6">
        <h2 className="text-xl font-semibold">Your documents</h2>
        {documents.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No documents yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-200">
            {documents.map((doc) => (
              <li key={doc.id} className="py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{doc.filename}</span>
                  <span
                    className={
                      "shrink-0 rounded-full px-2 py-0.5 text-xs " +
                      (doc.processing_status === "failed"
                        ? "bg-red-100 text-red-700"
                        : doc.processing_status === "processed"
                          ? "bg-green-100 text-green-700"
                          : "bg-slate-100 text-slate-600")
                    }
                  >
                    {STATUS_LABEL[doc.processing_status] || doc.processing_status}
                  </span>
                </div>
                {doc.classification && (
                  <p className="mt-1 text-xs text-slate-500">
                    Classified as {doc.classification}
                    {doc.classification_confidence != null &&
                      ` (${Math.round(doc.classification_confidence * 100)}% confidence)`}
                  </p>
                )}
                {doc.processing_error && (
                  <p className="mt-1 text-xs text-red-600">{doc.processing_error}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
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
