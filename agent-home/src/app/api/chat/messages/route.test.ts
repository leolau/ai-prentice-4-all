/**
 * BFF route tests for GET /api/chat/messages: the paging params (`visible`,
 * `limit`, `before`) are forwarded when valid and dropped when not, so a
 * garbage value degrades to the legacy read instead of an upstream 422.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/chat/messages/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const sessionMessages = vi.fn<
  (sessionId: string, opts: object) => Promise<Record<string, unknown>>
>(async () => ({ session_id: "s1", messages: [], has_more: false }));
const apiClientForRequest = vi.fn<(opts: object) => Promise<unknown>>(
  async () => ({ sessionMessages }),
);

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: (opts: object) => apiClientForRequest(opts),
}));

const principal: Principal = {
  user_id: "mia",
  display: "Mia",
  role: "member",
  channels: [],
  is_owner: false,
};

const get = (qs: string) => GET(new Request(`http://x/api/chat/messages?${qs}`));

describe("GET /api/chat/messages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue(principal);
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await get("sessionId=s1");
    expect(res.status).toBe(401);
    expect(sessionMessages).not.toHaveBeenCalled();
  });

  it("returns 400 without a session id", async () => {
    const res = await get("visible=1");
    expect(res.status).toBe(400);
  });

  it("sends no paging options for a legacy read", async () => {
    const res = await get("sessionId=s1");
    expect(res.status).toBe(200);
    expect(sessionMessages).toHaveBeenCalledWith("s1", {});
  });

  it("forwards visible, limit and before and returns has_more", async () => {
    sessionMessages.mockResolvedValueOnce({
      session_id: "s1",
      messages: [],
      has_more: true,
    });
    const res = await get("sessionId=s1&visible=1&limit=40&before=123");
    expect(sessionMessages).toHaveBeenCalledWith("s1", {
      visible: true,
      limit: 40,
      before: 123,
    });
    expect(await res.json()).toMatchObject({ has_more: true });
  });

  it("accepts visible=true", async () => {
    await get("sessionId=s1&visible=true");
    expect(sessionMessages).toHaveBeenCalledWith("s1", { visible: true });
  });

  it.each([
    ["limit=0"],
    ["limit=501"],
    ["limit=abc"],
    ["limit=4.5"],
    ["limit=-3"],
    ["before=0"],
    ["before=-1"],
    ["before=x"],
    ["visible=0"],
  ])("ignores an invalid %s", async (qs) => {
    await get(`sessionId=s1&${qs}`);
    expect(sessionMessages).toHaveBeenCalledWith("s1", {});
  });

  it("accepts the limit bounds", async () => {
    await get("sessionId=s1&limit=1");
    expect(sessionMessages).toHaveBeenLastCalledWith("s1", { limit: 1 });
    await get("sessionId=s1&limit=500");
    expect(sessionMessages).toHaveBeenLastCalledWith("s1", { limit: 500 });
  });

  it("binds the client to the requested profile", async () => {
    await get("sessionId=s1&profile=work&visible=1");
    expect(apiClientForRequest).toHaveBeenCalledWith({ profile: "work" });
  });

  it("maps an upstream error to its status", async () => {
    sessionMessages.mockRejectedValueOnce(new HermesApiError(404, "not found"));
    const res = await get("sessionId=s1");
    expect(res.status).toBe(404);
  });
});
