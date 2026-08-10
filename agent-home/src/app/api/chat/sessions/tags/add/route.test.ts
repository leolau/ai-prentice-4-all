/**
 * BFF route tests for POST /api/chat/sessions/tags/add (add tag to session).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/chat/sessions/tags/add/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const addSessionTag = vi.fn(async () => ({
  tag: { id: "t1", name: "bug", color: "red" },
}));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ addSessionTag }),
}));

function post(body: unknown): Promise<Response> {
  return POST(
    new Request("http://home.test/api/chat/sessions/tags/add", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  ) as unknown as Promise<Response>;
}

describe("POST /api/chat/sessions/tags/add", () => {
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
    const res = await post({ sessionId: "s1", name: "bug" });
    expect(res.status).toBe(401);
  });

  it("returns 400 on missing sessionId", async () => {
    const res = await post({ name: "bug" });
    expect(res.status).toBe(400);
  });

  it("returns 400 on missing name", async () => {
    const res = await post({ sessionId: "s1" });
    expect(res.status).toBe(400);
  });

  it("forwards name + color to addSessionTag", async () => {
    const res = await post({ sessionId: "s1", name: "bug", color: "red" });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.tag.name).toBe("bug");
    expect(addSessionTag).toHaveBeenCalledWith("s1", "bug", "red");
  });

  it("forwards undefined color when omitted (server defaults to blue)", async () => {
    await post({ sessionId: "s1", name: "feature" });
    expect(addSessionTag).toHaveBeenCalledWith("s1", "feature", undefined);
  });
});
