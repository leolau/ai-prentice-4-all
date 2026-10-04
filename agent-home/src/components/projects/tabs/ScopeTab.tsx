"use client";

import type { ProjectTabProps } from "@/components/projects/tabs/types";

/**
 * Scope — the agent's clarifying questions about goal and scope, asked
 * before implementation. Placeholder until the Scope workstream lands.
 */
export function ScopeTab({ project }: ProjectTabProps) {
  return (
    <div data-component="ScopeTab" className="text-sm text-[var(--color-muted)]">
      {project.clarify?.understanding ?? "The agent has not asked any questions yet."}
    </div>
  );
}
