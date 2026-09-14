/**
 * The card-row BFF stream route (§12 push edition): an SSE stream is piped
 * through unchanged so the browser reads the box's own frames.
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

function params(slug: string, taskId: string) {
  return { params: Promise.resolve({ slug, taskId }) };
}

const url = "http://x/api/projects/digest/cards/t_1/stream";

describe("GET /api/projects/:slug/cards/:taskId/stream", () => {
  it("refuses a caller with no session", async () => {
    state.principal = null;
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(401);
    state.principal = { user_id: "leo" };
  });

  it("pipes the upstream stream through unbuffered", async () => {
    const seen: unknown[] = [];
    state.client = {
      openCardStream: async (slug: string, taskId: string) => {
        seen.push([slug, taskId]);
        return new Response(
          'event: update\ndata: {"status":"running"}\n\n',
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    };
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(200);
    expect(seen).toEqual([["digest", "t_1"]]);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("running");
  });

  it("renders an upstream refusal as JSON rather than an empty stream", async () => {
    state.client = {
      openCardStream: async () => {
        throw new HermesApiError(404, "card not found");
      },
    };
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
  });
});
