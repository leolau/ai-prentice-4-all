/**
 * BFF route tests for GET /api/chat/sessions/search (session search).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/chat/sessions/search/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const searchSessions = vi.fn(async () => ({
  results: [{ session_id: "s1", snippet: "hello <mark>world</mark>", score: 0.9 }],
}));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ searchSessions }),
}));

function get(searchParams: URLSearchParams): Promise<Response> {
  const url = `http://home.test/api/chat/sessions/search?${searchParams.toString()}`;
  return GET(new Request(url)) as unknown as Promise<Response>;
}

describe("GET /api/chat/sessions/search", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "mia",
      display: "Mia",
      role: "member",
      channels: [],
      is_owner: false,
    });
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await get(new URLSearchParams({ q: "hello" }));
    expect(res.status).toBe(401);
  });

  it("returns empty results when q param is missing", async () => {
    const res = await get(new URLSearchParams());
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.results).toEqual([]);
    expect(searchSessions).not.toHaveBeenCalled();
  });

  it("returns empty results for empty query string", async () => {
    const res = await get(new URLSearchParams({ q: "" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.results).toEqual([]);
    expect(searchSessions).not.toHaveBeenCalled();
  });

  it("forwards q to client.searchSessions", async () => {
    const res = await get(new URLSearchParams({ q: "hello" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.results).toHaveLength(1);
    expect(searchSessions).toHaveBeenCalledWith("hello", 20);
  });

  it("forwards limit param", async () => {
    await get(new URLSearchParams({ q: "hello", limit: "10" }));
    expect(searchSessions).toHaveBeenCalledWith("hello", 10);
  });

  it("returns 502 on upstream error", async () => {
    const { HermesApiError } = await import("@/lib/api/client");
    searchSessions.mockRejectedValueOnce(new HermesApiError(502, "down"));
    const res = await get(new URLSearchParams({ q: "hello" }));
    expect(res.status).toBe(502);
  });
});
