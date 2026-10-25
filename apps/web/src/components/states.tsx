"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { Failure } from "@/lib/errors";
import { ApiState } from "@/lib/useApi";

// The shared look of a panel, and of the four states every workflow needs:
// loading, failure, empty and success (the page's own content).

export function Card({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-6 ${className}`}>
      {(title || action) && (
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          {title && <h2 className="text-xl font-semibold">{title}</h2>}
          {action}
        </div>
      )}
      {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
      {children}
    </section>
  );
}

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="mt-4 text-sm text-slate-500">
      {label}
    </p>
  );
}

export function ErrorState({
  failure,
  onRetry,
}: {
  failure: Failure;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm">
      <p className="text-red-800">{failure.message}</p>
      {failure.sessionExpired ? (
        <Link href="/login" className="mt-2 inline-block text-teal-700 underline">
          Log in
        </Link>
      ) : (
        onRetry &&
        !failure.notFound && (
          <button type="button" onClick={onRetry} className="mt-2 text-teal-700 underline">
            Try again
          </button>
        )
      )}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: { href: string; label: string };
}) {
  return (
    <div className="mt-4 rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-600">
      <p className="font-medium text-slate-700">{title}</p>
      {children && <p className="mt-1">{children}</p>}
      {action && (
        <Link href={action.href} className="mt-2 inline-block text-teal-700 underline">
          {action.label}
        </Link>
      )}
    </div>
  );
}

// Renders loading, then failure, then the empty state, then the content: so a page cannot forget one.
export function Async<T>({
  state,
  isEmpty,
  empty,
  loadingLabel,
  children,
}: {
  state: ApiState<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  loadingLabel?: string;
  children: (data: T) => ReactNode;
}) {
  if (state.loading && state.data === null) return <LoadingState label={loadingLabel} />;
  if (state.error && state.data === null) return <ErrorState failure={state.error} onRetry={state.reload} />;
  if (state.data === null) return null;
  return (
    <>
      {state.error && <ErrorState failure={state.error} onRetry={state.reload} />}
      {isEmpty && isEmpty(state.data) ? empty : children(state.data)}
    </>
  );
}

export function Badge({ className, children }: { className: string; children: ReactNode }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs ${className}`}>{children}</span>;
}
