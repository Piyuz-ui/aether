"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

/**
 * Load data from the backend, reload when `revision` changes (after each agent run), and
 * optionally poll. `interval` may be a function of the current data, e.g. poll fast only
 * while a document is still indexing.
 */
export function useLive<T>(
  fetcher: () => Promise<T>,
  revision: number,
  interval?: number | ((data: T | undefined) => number | undefined),
) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | null>(null);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const reload = useCallback(async () => {
    try {
      const d = await fetchRef.current();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      return undefined;
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, revision]);

  const delay = typeof interval === "function" ? interval(data) : interval;
  useEffect(() => {
    if (!delay) return;
    const t = window.setInterval(() => void reload(), delay);
    return () => window.clearInterval(t);
  }, [delay, reload]);

  return { data, error, reload, setData };
}
