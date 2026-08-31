/**
 * The run-activity BFF route: an SSE stream is piped through unchanged so
 * the browser reads the box's own frames, and the cursor is validated here
 * rather than upstream. Nothing on this route may buffer — a run's reasoning
 * that arrives at the end of the run is not a live view.
 */
import { describe, expect, it, vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

const state: { client: unknown; principal: unknown } = {
  client: null,
  principal: { user_id: "leo" },
};

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => state.principal,
  apiClientForRequest: async () => state.client,
}));

import { GET } from "./route";

function params(slug: string, runNo: string) {
  return { params: Promise.resolve({ slug, runNo }) };
}

const url = "http://x/api/projects/digest/runs/3/activity";

describe("GET /api/projects/:slug/runs/:runNo/activity", () => {
  it("refuses a caller with no session", async () => {
    state.principal = null;
    const res = await GET(new Request(url), params("digest", "3"));
    expect(res.status).toBe(401);
    state.principal = { user_id: "leo" };
  });

  it("rejects a nonsense cursor before touching upstream", async () => {
    state.client = {
      openRunActivityStream: async () => {
        throw new Error("upstream must not be called");
      },
    };
    const res = await GET(new Request(`${url}?after=abc`), params("digest", "3"));
    expect(res.status).toBe(400);
  });

  it("pipes the upstream stream through unbuffered", async () => {
    const seen: unknown[] = [];
    state.client = {
      openRunActivityStream: async (
        slug: string,
        runNo: number,
        after: number,
      ) => {
        seen.push([slug, runNo, after]);
        return new Response(
          'event: reasoning\ndata: {"text":"thinking"}\n\n',
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    };
    const res = await GET(new Request(`${url}?after=7`), params("digest", "3"));
    expect(res.status).toBe(200);
    expect(seen).toEqual([["digest", 3, 7]]);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("thinking");
  });

  it("renders an upstream refusal as JSON rather than an empty stream", async () => {
    state.client = {
      openRunActivityStream: async () => {
        throw new HermesApiError(404, "run not found");
      },
    };
    const res = await GET(new Request(url), params("digest", "3"));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
  });
});
