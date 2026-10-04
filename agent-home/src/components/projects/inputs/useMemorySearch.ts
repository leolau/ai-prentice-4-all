"use client";

import { useEffect, useRef, useState } from "react";

import type { MemoryRow, MemoryRowsResponse } from "@/types";

export const MEMORY_SEARCH_DEBOUNCE_MS = 300;

export interface MemorySearchState {
  results: MemoryRow[];
  loading: boolean;
  error: string | null;
  /** The query the shown results answer. */
  answered: string;
}

/**
 * Debounced search over the caller's memories through the existing
 * `/api/memory/rows?q=` BFF (semantic search, principal-scoped). A blank
 * query shows nothing; a stale answer never overwrites a newer one.
 */
export function useMemorySearch(
  query: string,
  {
    debounceMs = MEMORY_SEARCH_DEBOUNCE_MS,
    limit = 6,
    fetchImpl,
  }: { debounceMs?: number; limit?: number; fetchImpl?: typeof fetch } = {},
): MemorySearchState {
  const [answer, setAnswer] = useState<{
    q: string;
    results: MemoryRow[];
    error: string | null;
  }>({ q: "", results: [], error: null });
  const seq = useRef(0);
  const q = query.trim();

  useEffect(() => {
    const mine = ++seq.current;
    if (!q) return;
    const timer = setTimeout(() => {
      const doFetch = fetchImpl ?? fetch;
      void (async () => {
        try {
          const res = await doFetch(
            `/api/memory/rows?q=${encodeURIComponent(q)}&limit=${limit}`,
          );
          if (!res.ok) throw new Error(String(res.status));
          const data = (await res.json()) as MemoryRowsResponse;
          if (mine !== seq.current) return;
          setAnswer({ q, results: data.rows ?? [], error: null });
        } catch {
          if (mine !== seq.current) return;
          setAnswer({ q, results: [], error: "Could not search your memory just now." });
        }
      })();
    }, debounceMs);
    return () => clearTimeout(timer);
  }, [q, debounceMs, limit, fetchImpl]);

  if (!q) return { results: [], loading: false, error: null, answered: "" };
  if (answer.q !== q) {
    // Keep showing the previous answer while the new one is on its way.
    return { results: answer.results, loading: true, error: null, answered: answer.q };
  }
  return { results: answer.results, loading: false, error: answer.error, answered: q };
}
