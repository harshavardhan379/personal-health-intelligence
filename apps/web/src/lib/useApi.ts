"use client";

import { useCallback, useEffect, useState } from "react";
import { apiBlob, apiFetch } from "@/lib/api";
import { Failure, describeFailure } from "@/lib/errors";

export type ApiState<T> = {
  data: T | null;
  error: Failure | null;
  // True only until the first answer (or after reload()). A background poll never flips it,
  // so data already on screen does not flash away.
  loading: boolean;
  reload: () => void;
};

type Snapshot<T> = { data: T | null; error: Failure | null; loading: boolean };

// Loads `path` once on mount (and again on reload()). Pass null to wait, for example until an id
// is known. `pollMs` re-requests quietly on an interval (a number, or a function of the latest
// data so polling can stop once nothing is changing); a failed poll keeps the last good data.
export function useApi<T>(
  path: string | null,
  options: { pollMs?: number | ((data: T | null) => number | undefined); fallback?: string } = {},
): ApiState<T> {
  const { pollMs, fallback } = options;
  const [snapshot, setSnapshot] = useState<Snapshot<T>>({
    data: null,
    error: null,
    loading: path !== null,
  });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    apiFetch<T>(path)
      .then((data) => {
        if (!cancelled) setSnapshot({ data, error: null, loading: false });
      })
      .catch((err) => {
        if (!cancelled) {
          setSnapshot((s) => ({ data: s.data, error: describeFailure(err, fallback), loading: false }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path, tick, fallback]);

  const ms = typeof pollMs === "function" ? pollMs(snapshot.data) : pollMs;
  useEffect(() => {
    if (!ms || path === null) return;
    const interval = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(interval);
  }, [ms, path]);

  const reload = useCallback(() => {
    setSnapshot((s) => ({ ...s, loading: true, error: null }));
    setTick((t) => t + 1);
  }, []);

  return { ...snapshot, reload };
}

export type BlobState = { url: string | null; error: Failure | null; loading: boolean };

// The original document as an object URL (the request needs the login header, so a plain link
// cannot be used). The URL is released when the page is left or the path changes.
export function useBlobUrl(path: string | null): BlobState {
  const [state, setState] = useState<BlobState>({ url: null, error: null, loading: path !== null });

  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    let created: string | null = null;
    apiBlob(path)
      .then((blob) => {
        created = URL.createObjectURL(blob);
        if (cancelled) URL.revokeObjectURL(created);
        else setState({ url: created, error: null, loading: false });
      })
      .catch((err) => {
        if (!cancelled) setState({ url: null, error: describeFailure(err), loading: false });
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [path]);

  return state;
}
