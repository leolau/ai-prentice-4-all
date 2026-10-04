"use client";

import { useEffect, useState } from "react";

import type { ProjectChangesHistory } from "@/types";

/**
 * `GET /changes` — retired directives, change kinds and each run's pinned
 * plan revision. Re-read whenever `version` changes (the server tree's
 * project object is new after every refresh). `null` until it lands or when
 * it fails — the tab then groups from the page's own data.
 */
export function useChangesHistory(slug: string, version: unknown): ProjectChangesHistory | null {
  const [history, setHistory] = useState<ProjectChangesHistory | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/projects/${encodeURIComponent(slug)}/changes`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ProjectChangesHistory | null) => {
        if (!cancelled && data && Array.isArray(data.changes) && Array.isArray(data.runs)) {
          setHistory(data);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [slug, version]);
  return history;
}
