"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { Failure, describeFailure } from "@/lib/errors";
import { failedStageHint, failedStageLabel } from "@/lib/format";

// What a failed document shows: where it stopped, why, what to do, and a button to run it again.
export default function FailedDocument({
  stage,
  reason,
  documentId,
  onRetried,
}: {
  stage: string | null;
  reason: string | null;
  documentId: string;
  onRetried: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const label = failedStageLabel(stage);

  async function retry() {
    setBusy(true);
    setFailure(null);
    try {
      await apiFetch(`/documents/${documentId}/reprocess`, { method: "POST" });
      onRetried();
    } catch (err) {
      setFailure(describeFailure(err, "Could not start processing again"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 text-sm">
      <p role="alert" className="text-red-700">
        {label ? `Failed while ${label}. ` : "Failed. "}
        {reason}
      </p>
      <p className="mt-1 text-xs text-slate-600">{failedStageHint(stage)}</p>
      <button
        type="button"
        onClick={retry}
        disabled={busy}
        className="mt-2 rounded border border-teal-700 px-3 py-1 text-xs font-medium text-teal-800 hover:bg-teal-50 disabled:opacity-50"
      >
        {busy ? "Starting..." : "Try processing again"}
      </button>
      {failure && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {failure.message}
          {failure.requestId ? ` (Reference: ${failure.requestId})` : ""}
        </p>
      )}
    </div>
  );
}
