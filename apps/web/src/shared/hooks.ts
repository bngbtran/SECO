import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";

type Query = Record<string, string | number | boolean | null | undefined>;

/**
 * Load `path` from the admin API and reload whenever the path or query changes.
 * `path = null` skips loading (e.g. while an id is missing).
 */
export function useApi<T>(path: string | null, query?: Query) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const key = path === null ? null : `${path}?${JSON.stringify(query ?? {})}`;
  const request = useRef(0);

  const load = useCallback(
    async (silent = false) => {
      if (path === null) return;
      const id = ++request.current;
      if (!silent) setLoading(true);
      try {
        const result = await api<T>(path, { query });
        if (id === request.current) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (id === request.current) setError((e as Error).message);
      } finally {
        if (id === request.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );

  useEffect(() => {
    void load();
  }, [load]);

  /** Refetch without flashing the loading state (realtime refreshes). */
  const reload = useCallback(() => load(true), [load]);

  return { data, error, loading, reload, setData };
}

/** Call `fn` every `ms` while the tab is visible. */
export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    if (ms === null) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") saved.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
}

/** Re-render every `ms` so relative times and running durations stay fresh. */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), ms);
  return now;
}
