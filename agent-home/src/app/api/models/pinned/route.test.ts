/**
 * BFF route tests for GET /api/models/pinned — the fan-out that surfaces
 * kanban cards pinned to a model via `model_override`. Contract: only
 * non-terminal cards count, boards that fail to load are skipped, and the
 * project context travels with each row.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/models/pinned/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const projects = vi.fn();
const projectBoard = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ projects, projectBoard }),
}));

function get(): Promise<Response> {
  return GET(
    new Request("http://home.test/api/models/pinned"),
  ) as unknown as Promise<Response>;
}

const project = (slug: string, name: string, archived = false) => ({
  slug,
  name,
  archived,
});

const task = (id: string, status: string, model_override: string | null, title = id) => ({
  id,
  title,
  status,
  model_override,
});

const boardOf = (...tasks: ReturnType<typeof task>[]) => ({
  columns: [{ name: "col", tasks }],
});

describe("GET /api/models/pinned", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "leo",
      display: "Leo",
      role: "owner",
      channels: [],
      is_owner: true,
    });
    projects.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await get();
    expect(res.status).toBe(401);
  });

  it("collects overrides from live cards across boards", async () => {
    projects.mockResolvedValue({
      items: [project("canva-deck", "Canva deck"), project("figma", "Figma")],
      next_cursor: null,
    });
    projectBoard.mockImplementation(async (slug: string) =>
      slug === "canva-deck"
        ? boardOf(
            task("t1", "running", "deepseek-chat", "Generate slide 118"),
            task("t2", "done", "glm-5", "Old card"), // done → ignored
            task("t3", "ready", null, "Unpinned"), // no override → ignored
          )
        : boardOf(task("t4", "blocked", "glm-5", "Rebuild tokens")),
    );
    const res = await get();
    expect(res.status).toBe(200);
    const { pinned } = await res.json();
    expect(pinned).toHaveLength(2);
    expect(pinned[0]).toMatchObject({
      project_slug: "canva-deck",
      project_name: "Canva deck",
      task_id: "t1",
      model: "deepseek-chat",
      status: "running",
    });
    expect(pinned[1].task_id).toBe("t4");
  });

  it("skips archived projects and boards that fail to load", async () => {
    projects.mockResolvedValue({
      items: [
        project("dead", "Archived project", true),
        project("broken", "Broken board"),
        project("fine", "Fine"),
      ],
      next_cursor: null,
    });
    projectBoard.mockImplementation(async (slug: string) => {
      if (slug === "broken") throw new Error("board blew up");
      return boardOf(task("t9", "ready", "glm-5"));
    });
    const res = await get();
    const { pinned } = await res.json();
    expect(res.status).toBe(200);
    expect(pinned).toHaveLength(1);
    expect(pinned[0].project_slug).toBe("fine");
    expect(projectBoard).not.toHaveBeenCalledWith("dead");
  });

  it("returns an empty list when nothing is pinned", async () => {
    const res = await get();
    const { pinned } = await res.json();
    expect(res.status).toBe(200);
    expect(pinned).toEqual([]);
  });
});
