"use client";

import Link from "next/link";

// Shown when a page crashes while rendering (a bug, not a failed request: those are handled
// inside each page). Offers a retry and a way out, and never shows the technical error.
export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section role="alert" className="mx-auto max-w-lg rounded-lg border border-red-200 bg-white p-6">
      <h2 className="text-xl font-semibold">Something went wrong</h2>
      <p className="mt-2 text-sm text-slate-600">
        This page ran into a problem. Your records are not affected. You can try again, or go back to the
        dashboard.
      </p>
      <div className="mt-4 flex gap-3 text-sm">
        <button
          type="button"
          onClick={reset}
          className="rounded bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800"
        >
          Try again
        </button>
        <Link href="/" className="rounded border border-slate-300 px-4 py-2 hover:bg-slate-50">
          Go to the dashboard
        </Link>
      </div>
    </section>
  );
}
