/**
 * BFF route tests for POST /api/chat/sessions/tags/remove (remove tag from session).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/chat/sessions/tags/remove/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const removeSessionTag = vi.fn(async () => ({ ok: true }));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ removeSessionTag }),
}));

function post(body: unknown): Promise<Response> {
  return POST(
    new Request("http://home.test/api/chat/sessions/tags/remove", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  ) as unknown as Promise<Response>;
}

describe("POST /api/chat/sessions/tags/remove", () => {
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
    const res = await post({ sessionId: "s1", tagId: "t1" });
    expect(res.status).toBe(401);
  });

  it("returns 400 on missing sessionId", async () => {
    const res = await post({ tagId: "t1" });
    expect(res.status).toBe(400);
  });

  it("returns 400 on missing tagId", async () => {
    const res = await post({ sessionId: "s1" });
    expect(res.status).toBe(400);
  });

  it("forwards to removeSessionTag", async () => {
    const res = await post({ sessionId: "s1", tagId: "t1" });
    expect(res.status).toBe(200);
    expect((await res.json()).ok).toBe(true);
    expect(removeSessionTag).toHaveBeenCalledWith("s1", "t1");
  });
});
