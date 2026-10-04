"use client";

import { BoardPanel } from "@/components/projects/panels/BoardPanel";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

export function BoardTab({ project, board }: ProjectTabProps) {
  return (
    <div data-component="BoardTab">
      <BoardPanel slug={project.slug} board={board} archived={project.archived} />
    </div>
  );
}
