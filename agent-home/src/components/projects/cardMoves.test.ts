/**
 * The optimistic board helpers: `moveTaskInBoard` relocates a card between
 * columns and stamps its new status, `addTaskToBoard` appends a freshly
 * created card. Neither mutates the input — the board is a server prop.
 */
import { describe, expect, it } from "vitest";

import {
  addTaskToBoard,
  moveTaskInBoard,
} from "@/components/projects/cardMoves";
import type { ProjectBoardTask, ProjectBoardView } from "@/types";

const CARD = (id: string, status: string): ProjectBoardTask => ({
  id,
  title: `Card ${id}`,
  body: null,
  status,
  assignee: null,
  priority: 0,
  created_at: 1_700_000_000,
  started_at: null,
  completed_at: null,
  tenant: null,
  project_id: null,
  result: null,
  current_step_key: null,
});

const BOARD: ProjectBoardView = {
  columns: [
    { name: "triage", tasks: [CARD("t1", "triage"), CARD("t2", "triage")] },
    { name: "ready", tasks: [CARD("r1", "ready")] },
    { name: "done", tasks: [] },
  ],
};

describe("moveTaskInBoard", () => {
  it("moves the card to the end of the destination column with the new status", () => {
    const next = moveTaskInBoard(BOARD, "t1", "ready");
    expect(next.columns[0].tasks.map((t) => t.id)).toEqual(["t2"]);
    expect(next.columns[1].tasks.map((t) => t.id)).toEqual(["r1", "t1"]);
    expect(next.columns[1].tasks[1].status).toBe("ready");
    // The input board is a server prop — it must not be mutated in place.
    expect(BOARD.columns[0].tasks).toHaveLength(2);
  });

  it("returns the board unchanged for an unknown task or column", () => {
    expect(moveTaskInBoard(BOARD, "nope", "ready")).toBe(BOARD);
    expect(moveTaskInBoard(BOARD, "t1", "narnia")).toBe(BOARD);
  });
});

describe("addTaskToBoard", () => {
  it("appends the new card to the named column", () => {
    const next = addTaskToBoard(BOARD, "triage", CARD("t3", "triage"));
    expect(next.columns[0].tasks.map((t) => t.id)).toEqual(["t1", "t2", "t3"]);
    expect(next.columns[1].tasks).toHaveLength(1);
    expect(BOARD.columns[0].tasks).toHaveLength(2);
  });
});
