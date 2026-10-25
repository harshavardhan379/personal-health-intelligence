"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { Failure, describeFailure } from "@/lib/errors";

// Permanent deletions (YGH-119, YGH-121). Each one asks once more before it acts, and the two that
// remove everything also ask for the password again, so a login left open on a shared screen
// cannot wipe an account.

function ConfirmBox({
  warning,
  confirmLabel,
  needsPassword,
  busy,
  failure,
  onConfirm,
  onCancel,
}: {
  warning: string;
  confirmLabel: string;
  needsPassword: boolean;
  busy: boolean;
  failure: Failure | null;
  onConfirm: (password: string) => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");
  return (
    <form
      className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        onConfirm(password);
      }}
    >
      <p role="alert" className="text-red-800">
        {warning} <strong>This cannot be undone.</strong>
      </p>
      {needsPassword && (
        <label className="mt-3 block">
          <span className="text-slate-700">Enter your password to confirm</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 block w-full max-w-xs rounded border border-slate-300 px-2 py-1"
          />
        </label>
      )}
      {failure && (
        <p role="alert" className="mt-2 text-red-700">
          {failure.message}
          {failure.requestId && !failure.sessionExpired ? ` (Reference: ${failure.requestId})` : ""}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={busy || (needsPassword && password.length === 0)}
          className="rounded bg-red-700 px-3 py-1.5 font-medium text-white hover:bg-red-800 disabled:opacity-50"
        >
          {busy ? "Deleting..." : confirmLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 hover:bg-slate-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// One destructive action: a button that opens its confirmation, runs the call, reports a failure.
function Destructive({
  buttonLabel,
  warning,
  confirmLabel,
  needsPassword,
  run,
  className = "",
}: {
  buttonLabel: string;
  warning: string;
  confirmLabel: string;
  needsPassword: boolean;
  run: (password: string) => Promise<void>;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  async function confirm(password: string) {
    setBusy(true);
    setFailure(null);
    try {
      await run(password);
      setOpen(false);
    } catch (err) {
      setFailure(describeFailure(err, "The deletion could not be completed. Nothing was changed."));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`rounded border border-red-300 px-4 py-2 text-sm font-medium text-red-800 hover:bg-red-50 ${className}`}
      >
        {buttonLabel}
      </button>
    );
  }
  return (
    <ConfirmBox
      warning={warning}
      confirmLabel={confirmLabel}
      needsPassword={needsPassword}
      busy={busy}
      failure={failure}
      onConfirm={confirm}
      onCancel={() => {
        setOpen(false);
        setFailure(null);
      }}
    />
  );
}

export function DeleteDocument({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  return (
    <Destructive
      buttonLabel="Delete this document"
      warning="This deletes the document, every fact extracted from it and the original file."
      confirmLabel="Yes, delete this document"
      needsPassword={false}
      run={async () => {
        await apiFetch(`/documents/${id}`, { method: "DELETE" });
        onDeleted();
      }}
      className="mt-3"
    />
  );
}

export type Deleted = { documents: number; events: number; files_failed: number };

export function DeleteMyData({ onDeleted }: { onDeleted: (result: Deleted) => void }) {
  return (
    <Destructive
      buttonLabel="Delete all my documents and facts"
      warning="This deletes every document, extracted fact, source and uploaded file. Your account stays."
      confirmLabel="Yes, delete all my data"
      needsPassword
      run={async (password) => {
        const result = await apiFetch<Deleted>("/account/data", {
          method: "DELETE",
          body: JSON.stringify({ password }),
        });
        onDeleted(result);
      }}
    />
  );
}

export function DeleteMyAccount({ onDeleted }: { onDeleted: () => void }) {
  return (
    <Destructive
      buttonLabel="Delete my account"
      warning="This deletes your account and everything stored for it, and logs you out."
      confirmLabel="Yes, delete my account"
      needsPassword
      run={async (password) => {
        await apiFetch("/account", { method: "DELETE", body: JSON.stringify({ password }) });
        onDeleted();
      }}
    />
  );
}
