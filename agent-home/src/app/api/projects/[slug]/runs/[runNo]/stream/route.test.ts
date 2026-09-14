/**
 * The run-row BFF stream route (§12 push edition): an SSE stream is piped
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

function params(slug: string, runNo: string) {
  return { params: Promise.resolve({ slug, runNo }) };
}

const url = "http://x/api/projects/digest/runs/3/stream";

describe("GET /api/projects/:slug/runs/:runNo/stream", () => {
  it("refuses a caller with no session", async () => {
    state.principal = null;
    const res = await GET(new Request(url), params("digest", "3"));
    expect(res.status).toBe(401);
    state.principal = { user_id: "leo" };
  });

  it("rejects a non-integer run number before touching upstream", async () => {
    state.client = {
      openRunStream: async () => {
        throw new Error("upstream must not be called");
      },
    };
    const res = await GET(new Request(url), params("digest", "not-a-number"));
    expect(res.status).toBe(400);
  });

  it("pipes the upstream stream through unbuffered", async () => {
    const seen: unknown[] = [];
    state.client = {
      openRunStream: async (slug: string, runNo: number) => {
        seen.push([slug, runNo]);
        return new Response(
          'event: update\ndata: {"status":"running"}\n\n',
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    };
    const res = await GET(new Request(url), params("digest", "3"));
    expect(res.status).toBe(200);
    expect(seen).toEqual([["digest", 3]]);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("running");
  });

  it("renders an upstream refusal as JSON rather than an empty stream", async () => {
    state.client = {
      openRunStream: async () => {
        throw new HermesApiError(404, "run not found");
      },
    };
    const res = await GET(new Request(url), params("digest", "3"));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
  });
});
