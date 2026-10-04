"use client";

import type { ProjectDetail } from "@/types";

/**
 * One box under the live bar: "Ask about this project" (read-only Q&A) or
 * "Add requirement / change" (hands the text to `onChangeRequest`).
 * (Stub — filled in by the ask workstream.)
 */
export function AskOrChangeBox(props: {
  project: ProjectDetail;
  onChangeRequest: (initialText?: string) => void;
}) {
  void props;
  return null;
}
