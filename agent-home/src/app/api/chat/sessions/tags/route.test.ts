/**
 * BFF route tests for GET /api/chat/sessions/tags (list all tags).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/chat/sessions/tags/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const listTags = vi.fn(async () => ({
  tags: [{ id: "t1", name: "bug", color: "red", session_count: 2 }],
}));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ listTags }),
}));

describe("GET /api/chat/sessions/tags", () => {
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
    const res = await GET();
    expect(res.status).toBe(401);
  });

  it("forwards to client.listTags and returns tags", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.tags).toHaveLength(1);
    expect(data.tags[0].name).toBe("bug");
    expect(listTags).toHaveBeenCalledOnce();
  });

  it("returns 502 when the AI layer is unreachable", async () => {
    const { HermesApiError } = await import("@/lib/api/client");
    listTags.mockRejectedValueOnce(new HermesApiError(502, "down"));
    const res = await GET();
    expect(res.status).toBe(502);
  });
});
