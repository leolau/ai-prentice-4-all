"use client";

import { useEffect, useState } from "react";

import type { ProjectBoardContext, ProjectBoardView } from "@/types";

function isContext(data: unknown): data is ProjectBoardContext {
  return (
    !!data &&
    typeof data === "object" &&
    typeof (data as { card_runs?: unknown }).card_runs === "object"
  );
}

/**
 * The board's run context (which run made each card, is the open run
 * stalled), re-read whenever a fresh server board lands. `null` until it
 * arrives or when it can't be read — the board then degrades to what the
 * rows alone say. Pass `initial` to skip the read (tests, server data).
 */
export function useBoardContext(
  slug: string,
  board: ProjectBoardView | null,
  initial?: ProjectBoardContext | null,
): ProjectBoardContext | null {
  const [context, setContext] = useState<ProjectBoardContext | null>(initial ?? null);
  useEffect(() => {
    if (initial !== undefined) return;
    let live = true;
    fetch(`/api/projects/${encodeURIComponent(slug)}/board/context`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: unknown) => {
        if (live && isContext(data)) setContext(data);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [slug, board, initial]);
  return initial !== undefined ? initial : context;
}
