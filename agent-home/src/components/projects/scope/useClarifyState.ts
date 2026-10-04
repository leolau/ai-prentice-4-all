"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { CLARIFY_POLL_MS, clarifyPath, isClarifyState } from "@/components/projects/scope/clarify";
import { useRefresh } from "@/components/ui/useRefresh";
import type { ClarifyState } from "@/types";

export interface ClarifyStateHandle {
  /** `null` until the first read lands (unless an initial state was given). */
  state: ClarifyState | null;
  /** The last read failed (the shown state, if any, may be stale). */
  loadError: string | null;
  /** Show a write's answer at once; a GET that started earlier can't undo it. */
  apply: (next: ClarifyState) => void;
  reload: () => void;
}

/**
 * `GET /clarify` on mount and whenever `version` changes (the server tree's
 * project object is new after every refresh), then every `pollMs` while the
 * agent is writing its questions. When that job finishes the page is
 * refreshed once so the Dashboard's summary follows.
 */
export function useClarifyState(
  slug: string,
  {
    initial = null,
    version,
    pollMs = CLARIFY_POLL_MS,
    fetchImpl,
  }: {
    initial?: ClarifyState | null;
    version?: unknown;
    pollMs?: number;
    fetchImpl?: typeof fetch;
  } = {},
): ClarifyStateHandle {
  const [state, setState] = useState<ClarifyState | null>(initial);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const seq = useRef(0);
  const { refresh } = useRefresh();

  const read = useCallback((): Promise<ClarifyState | null> => {
    const mine = ++seq.current;
    return fetchClarify(slug, fetchImpl).then((data) => {
      if (mine !== seq.current) return null;
      if (!data) {
        setLoadError("Could not load the agent's questions.");
        return null;
      }
      setLoadError(null);
      setState(data);
      return data;
    });
  }, [fetchImpl, slug]);

  useEffect(() => {
    let cancelled = false;
    const mine = ++seq.current;
    void fetchClarify(slug, fetchImpl).then((data) => {
      if (cancelled || mine !== seq.current) return;
      if (data) setState(data);
      setLoadError(data ? null : "Could not load the agent's questions.");
    });
    return () => {
      cancelled = true;
    };
  }, [fetchImpl, slug, version, reloads]);

  const running = state?.job.status === "running";
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      timer = setTimeout(async () => {
        const next = await read();
        // Still running (or a failed read): keep polling. Otherwise the state
        // change ends this effect.
        if (!cancelled && (!next || next.job.status === "running")) tick();
      }, pollMs);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [running, read, pollMs]);

  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) refresh();
    wasRunning.current = running;
  }, [running, refresh]);

  const apply = useCallback((next: ClarifyState) => {
    seq.current++;
    setLoadError(null);
    setState(next);
  }, []);

  const reload = useCallback(() => setReloads((n) => n + 1), []);

  return { state, loadError, apply, reload };
}

/** One `GET /clarify`; `null` on any failure or an unexpected payload. */
export async function fetchClarify(
  slug: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ClarifyState | null> {
  try {
    const res = await fetchImpl(clarifyPath(slug), { cache: "no-store" });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    return isClarifyState(data) ? data : null;
  } catch {
    return null;
  }
}
