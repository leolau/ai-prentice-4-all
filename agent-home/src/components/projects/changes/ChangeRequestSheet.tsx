"use client";

import type {
  ProjectDetail,
  ProjectDirectivesResponse,
  ProjectPlaybookResponse,
} from "@/types";

/**
 * "What's changed?": describe → when should it apply → review the agent's
 * updated plan → approve. (Stub — filled in by the change workstream.)
 */
export function ChangeRequestSheet(_props: {
  project: ProjectDetail;
  playbook: ProjectPlaybookResponse | null;
  directives: ProjectDirectivesResponse | null;
  initialText?: string;
  onClose: () => void;
}) {
  return null;
}
