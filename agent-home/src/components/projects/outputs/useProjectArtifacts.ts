"use client";

import { useCallback, useEffect, useState } from "react";

import { artifactsFromOutputs } from "@/components/projects/outputs/artifacts";
import type { ProjectArtifact, ProjectOutputWithDeliveries } from "@/types";

export interface ProjectArtifactsState {
  artifacts: ProjectArtifact[];
  /** True until the first read has answered (reloads keep the old list). */
  loading: boolean;
  /** True when the read failed and the list is derived from the outputs. */
  fallback: boolean;
  reload: () => void;
  /** Patch the local list (e.g. after attaching a file) before the reload. */
  setArtifacts: (update: (prev: ProjectArtifact[]) => ProjectArtifact[]) => void;
}

interface ReadState {
  data: ProjectArtifact[] | null;
  failed: boolean;
  loadedTick: number;
}

/**
 * The produced-files read, client-side. Loads on mount (unless `initial` is
 * given — SSR tests and prefetched pages), and falls back to the deliveries
 * on `project.outputs` when the read fails, so the shelf is never blank just
 * because one endpoint is down.
 */
export function useProjectArtifacts(
  slug: string,
  outputs: ProjectOutputWithDeliveries[],
  opts: { initial?: ProjectArtifact[]; fetchImpl?: typeof fetch } = {},
): ProjectArtifactsState {
  const { initial, fetchImpl } = opts;
  const [tick, setTick] = useState(initial === undefined ? 1 : 0);
  const [state, setState] = useState<ReadState>({
    data: initial ?? null,
    failed: false,
    loadedTick: 0,
  });

  useEffect(() => {
    if (tick === 0) return;
    let cancelled = false;
    const doFetch = fetchImpl ?? fetch;
    void (async () => {
      let next: ReadState;
      try {
        const res = await doFetch(`/api/projects/${encodeURIComponent(slug)}/artifacts`);
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as unknown;
        if (!Array.isArray(data)) throw new Error("bad shape");
        next = { data: data as ProjectArtifact[], failed: false, loadedTick: tick };
      } catch {
        next = { data: null, failed: true, loadedTick: tick };
      }
      if (!cancelled) setState(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [slug, tick, fetchImpl]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setArtifacts = useCallback(
    (update: (prev: ProjectArtifact[]) => ProjectArtifact[]) =>
      setState((s) => ({ ...s, data: update(s.data ?? artifactsFromOutputs(outputs)) })),
    [outputs],
  );
  const artifacts = state.data ?? (state.failed ? artifactsFromOutputs(outputs) : []);
  return {
    artifacts,
    loading: tick !== 0 && state.loadedTick === 0,
    fallback: state.failed,
    reload,
    setArtifacts,
  };
}
