/**
 * The events-poll BFF route (E3): a poll failure must come back as a JSON
 * error envelope the client hook swallows — never a throw, never 200 HTML.
 * `since` validation stays route-local, as on every projects mirror route.
 */
import { describe, expect, it, vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => ({ user_id: "leo" }),
  apiClientForRequest: async () => clientState.client,
}));

import { GET } from "./route";

function req(url: string): Request {
  return new Request(url);
}

function params(slug: string) {
  return { params: Promise.resolve({ slug }) };
}

describe("GET /api/projects/:slug/events", () => {
  it("rejects a malformed since before touching upstream", async () => {
    const res = await GET(req("http://x/api/projects/digest/events?since=abc"), params("digest"));
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toContain("since");
  });

  it("passes the tail through on success", async () => {
    clientState.client = {
      projectEvents: async () => ({ events: [], latest_event_id: 3, since: 0 }),
    };
    const res = await GET(req("http://x/api/projects/digest/events"), params("digest"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ events: [], latest_event_id: 3, since: 0 });
  });

  it("renders a failed poll as a JSON error envelope, not a crash", async () => {
    clientState.client = {
      projectEvents: async () => {
        throw new HermesApiError(500, "the board store is locked");
      },
    };
    const res = await GET(req("http://x/api/projects/digest/events"), params("digest"));
    expect(res.status).toBe(500);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body.error).toBe("api_error");
    expect(typeof body.detail).toBe("string");
    // The envelope is what the poller's !res.ok branch swallows — a 200
    // here would be rendered as stale-but-happy silence instead.
    expect(res.headers.get("content-type")).toContain("application/json");
  });

  it("maps an unreachable upstream to a 502 envelope", async () => {
    clientState.client = {
      projectEvents: async () => {
        throw new Error("connection refused");
      },
    };
    const res = await GET(req("http://x/api/projects/digest/events"), params("digest"));
    expect(res.status).toBe(502);
    expect(((await res.json()) as { error: string }).error).toBe("api_unreachable");
  });
});
