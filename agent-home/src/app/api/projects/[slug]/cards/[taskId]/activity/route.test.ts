/**
 * The card-activity BFF route: the worker's SSE stream is piped through
 * unchanged, the cursor is validated here, and an upstream refusal comes
 * back as JSON rather than an empty stream.
 */
import { describe, expect, it } from "vitest";
import { vi } from "vitest";

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

const url = "http://x/api/projects/digest/cards/t_1/activity";

describe("GET /api/projects/:slug/cards/:taskId/activity", () => {
  it("refuses a caller with no session", async () => {
    state.principal = null;
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(401);
    state.principal = { user_id: "leo" };
  });

  it("rejects a nonsense cursor before touching upstream", async () => {
    state.client = {
      openCardActivityStream: async () => {
        throw new Error("upstream must not be called");
      },
    };
    for (const bad of ["abc", "-1", "1.5"]) {
      const res = await GET(new Request(`${url}?after=${bad}`), params("digest", "t_1"));
      expect(res.status).toBe(400);
    }
  });

  it("pipes the upstream stream through unbuffered", async () => {
    const seen: unknown[] = [];
    state.client = {
      openCardActivityStream: async (slug: string, taskId: string, after: number) => {
        seen.push([slug, taskId, after]);
        return new Response('event: reasoning\ndata: {"text":"thinking"}\n\n', {
          headers: { "content-type": "text/event-stream" },
        });
      },
    };
    const res = await GET(new Request(`${url}?after=7`), params("digest", "t_1"));
    expect(res.status).toBe(200);
    expect(seen).toEqual([["digest", "t_1", 7]]);
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(res.headers.get("cache-control")).toContain("no-transform");
    expect(res.headers.get("x-accel-buffering")).toBe("no");
    expect(await res.text()).toContain("thinking");
  });

  it("defaults the cursor to zero", async () => {
    const seen: unknown[] = [];
    state.client = {
      openCardActivityStream: async (_s: string, _t: string, after: number) => {
        seen.push(after);
        return new Response("", { headers: { "content-type": "text/event-stream" } });
      },
    };
    await GET(new Request(url), params("digest", "t_1"));
    expect(seen).toEqual([0]);
  });

  it("renders an upstream refusal as JSON rather than an empty stream", async () => {
    state.client = {
      openCardActivityStream: async () => {
        throw new HermesApiError(404, "card not found");
      },
    };
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toContain("application/json");
  });

  it("says the AI layer is unreachable on a transport failure", async () => {
    state.client = {
      openCardActivityStream: async () => {
        throw new TypeError("fetch failed");
      },
    };
    const res = await GET(new Request(url), params("digest", "t_1"));
    expect(res.status).toBe(502);
  });
});
