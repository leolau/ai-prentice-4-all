/**
 * The project-events-cursor BFF stream route (§12 push edition): an SSE
 * stream is piped through unchanged so the browser reads the box's own
 * frames.
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

function params(slug: string) {
  return { params: Promise.resolve({ slug }) };
}

const url = "http://x/api/projects/digest/events/stream";

describe("GET /api/projects/:slug/events/stream", () => {
  it("refuses a caller with no session", async () => {
    state.principal = null;
    const res = await GET(new Request(url), params("digest"));
    expect(res.status).toBe(401);
    state.principal = { user_id: "leo" };
  });

  it("pipes the upstream stream through unbuffered", async () => {
    const seen: unknown[] = [];
    state.client = {
      openProjectEventsStream: async (slug: string) => {
        seen.push(slug);
        return new Response(
          'event: update\ndata: {"latest_event_id":7}\n\n',
          { headers: { "content-type": "text/event-stream" } },
        );
      },
    };
    const res = await GET(new Request(url), params("digest"));
    expect(res.status).toBe(200);
    expect(seen).toEqual(["digest"]);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("latest_event_id");
  });

  it("renders an upstream refusal as JSON rather than an empty stream", async () => {
    state.client = {
      openProjectEventsStream: async () => {
        throw new HermesApiError(404, "project not found");
      },
    };
    const res = await GET(new Request(url), params("digest"));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
  });
});
