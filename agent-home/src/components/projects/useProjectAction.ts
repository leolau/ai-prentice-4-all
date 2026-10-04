"use client";

import { useCallback, useRef, useState } from "react";

import { friendlyError } from "@/components/projects/errors";
import { useRefresh } from "@/components/ui/useRefresh";

/**
 * One shared way to fire a project mutation from a button.
 *
 * The bug this exists to prevent: a click goes through, the backend acts,
 * but the button looks unchanged and can be clicked again — so the action
 * runs twice. Every project write goes through here so that:
 *
 * - the **first** click takes a synchronous lock (a ref, not state — two
 *   clicks in the same frame both see React state as "idle");
 * - the caller is `busy` from that click until the refreshed server tree
 *   has landed, not just until the response arrives;
 * - every attempt carries an `Idempotency-Key`, and `retry()` re-sends the
 *   *same* key, so a retry after a lost response replays the first result
 *   on the server instead of executing again.
 */

export type ActionPhase = "idle" | "pending" | "refreshing" | "error";

export interface ActionResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  /** A sentence for the person, never a raw status line. */
  error: string | null;
}

export interface ActionRequest<T> {
  method?: "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Runs with the server's answer before the refresh starts, so the view
   * can show the new state immediately (optimistic-on-confirm). */
  onSuccess?: (data: T) => void;
  /** Skip the `router.refresh()` after success (e.g. the caller navigates). */
  skipRefresh?: boolean;
}

export const IDEMPOTENCY_HEADER = "Idempotency-Key";

export function newIdempotencyKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `k_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

/** The fetch half, DOM-free so it is unit-testable on its own. */
export async function sendProjectAction<T>(
  path: string,
  key: string,
  request: Pick<ActionRequest<T>, "method" | "body">,
  fetchImpl: typeof fetch = fetch,
): Promise<ActionResult<T>> {
  const headers: Record<string, string> = { [IDEMPOTENCY_HEADER]: key };
  let body: BodyInit | undefined;
  if (request.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(request.body);
  }
  let res: Response;
  try {
    res = await fetchImpl(path, {
      method: request.method ?? "POST",
      headers,
      body,
    });
  } catch {
    return { ok: false, status: 0, data: null, error: "Could not reach the server." };
  }
  const data = (await res.json().catch(() => null)) as
    | (T & { detail?: string })
    | null;
  if (!res.ok) {
    return {
      ok: false,
      status: res.status,
      data,
      error: friendlyError(
        { status: res.status, detail: data?.detail },
        "That did not go through.",
      ),
    };
  }
  return { ok: true, status: res.status, data, error: null };
}

export interface ProjectAction<T> {
  /** Fire the action. Resolves `null` when ignored because one is in flight. */
  run: (path: string, request?: ActionRequest<T>) => Promise<ActionResult<T> | null>;
  /** Re-send the last failed request with the same idempotency key. */
  retry: () => Promise<ActionResult<T> | null>;
  /** True from the click until the refreshed page has rendered. */
  busy: boolean;
  phase: ActionPhase;
  error: string | null;
  clearError: () => void;
}

export function useProjectAction<T = Record<string, unknown>>(
  fetchImpl: typeof fetch = fetch,
): ProjectAction<T> {
  const { refresh, refreshing } = useRefresh();
  const lock = useRef(false);
  const last = useRef<{ path: string; request: ActionRequest<T>; key: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const exec = useCallback(
    async (path: string, request: ActionRequest<T>, key: string) => {
      if (lock.current) return null;
      lock.current = true;
      last.current = { path, request, key };
      setPending(true);
      setError(null);
      try {
        const result = await sendProjectAction<T>(path, key, request, fetchImpl);
        if (!result.ok) {
          setError(result.error);
          return result;
        }
        last.current = null;
        if (result.data !== null) request.onSuccess?.(result.data);
        if (!request.skipRefresh) refresh();
        return result;
      } finally {
        lock.current = false;
        setPending(false);
      }
    },
    [fetchImpl, refresh],
  );

  const run = useCallback(
    (path: string, request: ActionRequest<T> = {}) =>
      exec(path, request, newIdempotencyKey()),
    [exec],
  );

  const retry = useCallback(async () => {
    const prev = last.current;
    if (!prev) return null;
    return exec(prev.path, prev.request, prev.key);
  }, [exec]);

  const busy = pending || refreshing;
  const phase: ActionPhase = pending
    ? "pending"
    : refreshing
      ? "refreshing"
      : error
        ? "error"
        : "idle";

  return {
    run,
    retry,
    busy,
    phase,
    error,
    clearError: useCallback(() => setError(null), []),
  };
}
