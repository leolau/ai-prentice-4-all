"use client";

import type { ProjectBoardView, ProjectDetail } from "@/types";

/**
 * Sticky, on every tab: the project's state in one word, step progress,
 * and one row per concurrently running task with its live reasoning and
 * a Stop button; "Stop all" for the run. (Stub — filled in by the live
 * workstream.)
 */
export function LiveStatusBar(_props: {
  project: ProjectDetail;
  board: ProjectBoardView | null;
}) {
  return null;
}
