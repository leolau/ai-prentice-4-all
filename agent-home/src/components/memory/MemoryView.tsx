"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { MemoryMap } from "@/components/memory/MemoryMap";
import type {
  MemoryProjection,
  MemoryQueryPlacement,
  MemoryRow,
  MemoryRowsResponse,
  MemorySummary,
} from "@/types";

/**
 * FG-23 A2/A4 — the phone memory view. Server-rendered with the first page of
 * rows; search, paging, the map and query placement are client-side refetches
 * through the BFF handlers under `src/app/api/memory/*`.
 *
 * Read-only: no write, delete, re-embed or forget path exists on this surface.
 */
export function MemoryView({
  summary,
  initialRows,
}: {
  summary: MemorySummary;
  initialRows: MemoryRowsResponse;
}) {
  const [rows, setRows] = useState<MemoryRow[]>(initialRows.rows);
  const [total, setTotal] = useState(initialRows.total);
  const [offset, setOffset] = useState(initialRows.offset);
  const [limit] = useState(initialRows.limit);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [rowsError, setRowsError] = useState<string | null>(null);

  // --- Search (debounced 300 ms, offset reset to 0) -----------------------
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const doSearch = useCallback(
    async (query: string) => {
      setLoading(true);
      setSearching(!!query.trim());
      setRowsError(null);
      try {
        const sp = new URLSearchParams();
        if (query.trim()) sp.set("q", query.trim());
        sp.set("limit", String(limit));
        sp.set("offset", "0");
        const res = await fetch(`/api/memory/rows?${sp.toString()}`);
        if (!res.ok) {
          setRowsError(describeFailure(res.status));
          return;
        }
        const data: MemoryRowsResponse = await res.json();
        setRows(data.rows);
        setTotal(data.total);
        setOffset(0);
      } catch {
        setRowsError("Couldn't reach the AI layer.");
      } finally {
        setLoading(false);
      }
    },
    [limit],
  );

  // The server already rendered the first page, so the debounce must not fire
  // on mount: it would refetch identical rows over mobile data and flash the
  // list. It arms on the first keystroke.
  const searchArmed = useRef(false);
  useEffect(() => {
    if (!searchArmed.current) {
      searchArmed.current = true;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(q), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q, doSearch]);

  // --- Load more ----------------------------------------------------------
  const loadMore = useCallback(async () => {
    const nextOffset = offset + limit;
    if (nextOffset >= total) return;
    setLoading(true);
    setRowsError(null);
    try {
      const sp = new URLSearchParams();
      if (q.trim()) sp.set("q", q.trim());
      sp.set("limit", String(limit));
      sp.set("offset", String(nextOffset));
      const res = await fetch(`/api/memory/rows?${sp.toString()}`);
      if (!res.ok) {
        setRowsError(describeFailure(res.status));
        return;
      }
      const data: MemoryRowsResponse = await res.json();
      setRows((prev) => [...prev, ...data.rows]);
      setOffset(nextOffset);
    } catch {
      setRowsError("Couldn't reach the AI layer.");
    } finally {
      setLoading(false);
    }
  }, [offset, limit, total, q]);

  // --- Map (fetched after first paint) ------------------------------------
  const [projection, setProjection] = useState<MemoryProjection | null>(null);
  const [projectionError, setProjectionError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/memory/projection");
        if (!res.ok) {
          if (!cancelled) setProjectionError(true);
          return;
        }
        const data: MemoryProjection = await res.json();
        if (!cancelled) setProjection(data);
      } catch {
        if (!cancelled) setProjectionError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- Query placement (A4) ----------------------------------------------
  const [queryText, setQueryText] = useState("");
  const [queryResult, setQueryResult] = useState<MemoryQueryPlacement | null>(
    null,
  );
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  const placeQuery = useCallback(async () => {
    if (!queryText.trim()) return;
    setQueryLoading(true);
    setQueryError(null);
    try {
      const res = await fetch("/api/memory/query", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: queryText.trim() }),
      });
      if (!res.ok) {
        // 429 is the upstream's own rate limit (one placement every 3 s).
        setQueryError(
          res.status === 429
            ? "One placement every few seconds \u2014 try again shortly."
            : describeFailure(res.status),
        );
        return;
      }
      const data: MemoryQueryPlacement = await res.json();
      setQueryResult(data);
    } catch {
      setQueryError("Couldn't reach the AI layer.");
    } finally {
      setQueryLoading(false);
    }
  }, [queryText]);

  // --- Row lookup for nearest neighbours ----------------------------------
  const rowMap = new Map(rows.map((r) => [r.id, r]));

  return (
    <div data-component="MemoryView" className="space-y-4">
      {/* Pills */}
      <div className="flex flex-wrap gap-2">
        <Pill label="memories" value={summary.totals.memories} />
        <Pill
          label="never recalled"
          value={summary.recall_use.never_used}
        />
        {summary.totals.documents > 0 && (
          <Pill label="documents" value={summary.totals.documents} />
        )}
      </div>

      {/* Map */}
      {projection ? (
        <MemoryMap projection={projection} queryResult={queryResult} />
      ) : projectionError ? null : (
        <div className="h-40 animate-pulse rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)]" />
      )}

      {/* Query placement (A4) */}
      <div className="space-y-2">
        <div className="flex gap-2">
          <input
            type="text"
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") placeQuery();
            }}
            placeholder="Place a query on the map…"
            className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={placeQuery}
            disabled={queryLoading || !queryText.trim()}
            className="rounded-lg bg-[var(--color-accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            {queryLoading ? "…" : "Place"}
          </button>
        </div>
        {queryText.trim() && (
          <p className="text-xs text-[var(--color-muted)]">
            Your query is never stored — it&apos;s embedded and discarded.
          </p>
        )}
        {queryError && (
          <p data-component="MemoryQueryError" className="text-xs text-[var(--color-accent)]">
            {queryError}
          </p>
        )}
        {queryResult && (
          <QueryResultView
            result={queryResult}
            rowMap={rowMap}
          />
        )}
      </div>

      {/* Search */}
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search memories…"
        className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm"
      />

      {/* Rows */}
      {rowsError && (
        <p data-component="MemoryRowsError" className="text-xs text-[var(--color-accent)]">
          {rowsError}
        </p>
      )}
      <div className="space-y-2">
        {rows.length === 0 && !loading ? (
          <div className="py-8 text-center text-sm text-[var(--color-muted)]">
            {searching
              ? "No memories matched your search."
              : "No memories visible in your scope yet."}
          </div>
        ) : (
          rows.map((row) => <MemoryCard key={row.id} row={row} />)
        )}
      </div>

      {/* Load more */}
      {offset + limit < total && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loading}
          className="w-full rounded-lg border border-[var(--color-border)] py-2 text-sm text-[var(--color-muted)] disabled:opacity-40"
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}

/** A failed BFF call in words a phone user can act on. */
export function describeFailure(status: number): string {
  if (status === 401) return "Your session expired \u2014 sign in again.";
  if (status === 403) return "You don't have access to this memory.";
  if (status === 409) return "Your login isn't linked to a memory principal.";
  return "Couldn't load memories right now.";
}

function Pill({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1 text-xs">
      <span className="font-semibold">{value}</span>{" "}
      <span className="text-[var(--color-muted)]">{label}</span>
    </span>
  );
}

function MemoryCard({ row }: { row: MemoryRow }) {
  return (
    <div
      data-component="MemoryCard"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
    >
      <p className="text-sm leading-relaxed">
        {row.text}
        {row.truncated && <span className="text-[var(--color-muted)]"> …</span>}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted)]">
        {row.topic && (
          <span className="rounded bg-[var(--color-bg)] px-2 py-0.5">
            {row.topic}
          </span>
        )}
        <span>uses: {row.uses}</span>
        {row.last_used && <span>· {relativeTime(row.last_used)}</span>}
        {row.score != null && (
          <span>· score: {row.score.toFixed(3)}</span>
        )}
        {row.elevated && row.provenance && (
          <span className="text-[var(--color-accent)]">
            · {row.provenance}
          </span>
        )}
      </div>
    </div>
  );
}

function QueryResultView({
  result,
  rowMap,
}: {
  result: MemoryQueryPlacement;
  rowMap: Map<string, MemoryRow>;
}) {
  if (result.degraded) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm">
        <p className="text-[var(--color-muted)]">
          The map has no place for this query (UMAP basis unavailable), but
          here are the nearest memories by meaning:
        </p>
        <NearestList nearest={result.nearest} rowMap={rowMap} />
      </div>
    );
  }
  if (result.x == null || result.y == null) return null;
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm">
      <p className="text-[var(--color-muted)]">
        Query placed at ({result.x.toFixed(1)}, {result.y.toFixed(1)}).
      </p>
      <NearestList nearest={result.nearest} rowMap={rowMap} />
    </div>
  );
}

function NearestList({
  nearest,
  rowMap,
}: {
  nearest: { id: string; score: number }[];
  rowMap: Map<string, MemoryRow>;
}) {
  if (nearest.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {nearest.slice(0, 5).map((n) => {
        const row = rowMap.get(n.id);
        return (
          <li key={n.id} className="flex items-baseline gap-2 text-xs">
            <span className="font-mono text-[var(--color-muted)]">
              {n.score.toFixed(3)}
            </span>
            <span className="truncate">
              {row ? row.text.slice(0, 80) : "(a memory outside the loaded list)"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function relativeTime(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}
