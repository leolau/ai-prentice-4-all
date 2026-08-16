"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ProjectRow } from "@/components/projects/ProjectRow";
import { ProjectsFilters } from "@/components/projects/ProjectsFilters";
import {
  EMPTY_FILTERS,
  filtersFromParams,
  filtersToParams,
  type ProjectsFilterState,
} from "@/components/projects/filters";
import type { ProjectListItem, ProjectsResponse } from "@/types";

const PAGE_SIZE = 50;

/**
 * The project list: everything the agent is working on, running on a
 * schedule, or standing watch over.
 *
 * Same contract as the to-do list: keyset paging, filters round-trip through
 * the URL (the cursor never does), and the first page is the server's — so
 * the debounce arms on the first interaction instead of refetching the page
 * that just rendered.
 */
export function ProjectsList({ initial }: { initial: ProjectsResponse }) {
  const [items, setItems] = useState<ProjectListItem[]>(initial.items);
  const [cursor, setCursor] = useState<string | null>(initial.next_cursor);
  // The server rendered from the same URL, so the state starts from the
  // address bar rather than from an effect that would flash the default view.
  const [filters, setFilters] = useState<ProjectsFilterState>(() =>
    typeof window === "undefined"
      ? EMPTY_FILTERS
      : filtersFromParams(new URLSearchParams(window.location.search)),
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPage = useCallback(
    async (value: ProjectsFilterState, after: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const params = filtersToParams(value, after);
        params.set("limit", String(PAGE_SIZE));
        const res = await fetch(`/api/projects?${params.toString()}`, {
          cache: "no-store",
        });
        if (!res.ok) {
          setError(
            res.status === 401
              ? "Your session expired — sign in again."
              : "Couldn't load your projects.",
          );
          return;
        }
        const body = (await res.json()) as ProjectsResponse;
        setItems((prev) => (after ? [...prev, ...body.items] : body.items));
        setCursor(body.next_cursor);
      } catch {
        setError("Couldn't reach the AI layer.");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const armed = useRef(false);
  useEffect(() => {
    if (!armed.current) {
      armed.current = true;
      return;
    }
    const timer = setTimeout(() => {
      const params = filtersToParams(filters);
      const query = params.toString();
      window.history.replaceState(
        null,
        "",
        query ? `/projects?${query}` : "/projects",
      );
      void fetchPage(filters, null);
    }, 300);
    return () => clearTimeout(timer);
  }, [filters, fetchPage]);

  // Infinite scroll, with the button below as the accessible fallback for
  // keyboard users and for browsers where the sentinel never intersects.
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = sentinel.current;
    if (!node || cursor == null || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !loading) {
        void fetchPage(filters, cursor);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [cursor, filters, loading, fetchPage]);

  const narrowed = filters !== EMPTY_FILTERS && filters.view !== "active";

  return (
    <div data-component="ProjectsList" className="flex flex-col gap-3">
      <ProjectsFilters value={filters} onChange={setFilters} />

      {error ? (
        <p
          data-component="ProjectsError"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          {error}
        </p>
      ) : null}

      {items.length === 0 && !loading ? (
        <p
          data-component="ProjectsEmpty"
          className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-muted)]"
        >
          {narrowed || filters.q
            ? "Nothing matches that view."
            : "No active projects. Anything the agent works on over time — a deliverable, a recurring job, a standing duty — lives here."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((project) => (
            <ProjectRow key={project.id} project={project} />
          ))}
        </ul>
      )}

      <div ref={sentinel} aria-hidden className="h-px" />

      <div className="flex items-center justify-center py-2 text-xs text-[var(--color-muted)]">
        {loading ? (
          <span>Loading…</span>
        ) : cursor ? (
          <button
            type="button"
            onClick={() => void fetchPage(filters, cursor)}
            className="rounded-lg border border-[var(--color-border)] px-3 py-1.5"
          >
            Load more
          </button>
        ) : items.length > 0 ? (
          <span>That&apos;s everything.</span>
        ) : null}
      </div>
    </div>
  );
}
