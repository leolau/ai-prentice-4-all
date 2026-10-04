"use client";

import { BoardView } from "@/components/projects/board/BoardView";
import type { ProjectTabProps } from "@/components/projects/tabs/types";

export function BoardTab({ project, board, callerUserId, canLead }: ProjectTabProps) {
  return (
    <div data-component="BoardTab">
      <BoardView
        project={project}
        board={board}
        callerUserId={callerUserId}
        canLead={canLead}
      />
    </div>
  );
}
