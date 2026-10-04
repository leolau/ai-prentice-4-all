/** BFF route tests for Unblock: the gate, and the optional reason trimmed. */
import { describe, expect, it, vi } from "vitest";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { POST } from "./route";

const params = { params: Promise.resolve({ slug: "digest", taskId: "t_1" }) };

function req(body: unknown): Request {
  return new Request("http://x/api/projects/digest/cards/t_1/unblock", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

describe("POST /api/projects/:slug/cards/:taskId/unblock", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    expect((await POST(req({}), params)).status).toBe(401);
  });

  it("forwards a trimmed reason, and none when blank", async () => {
    principalState.principal = { user_id: "leo" };
    const unblock = vi.fn(async () => ({ id: "t_1", status: "ready" }));
    clientState.client = { unblockProjectCard: unblock };
    const res = await POST(req({ reason: "  list attached  " }), params);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: "t_1", status: "ready" });
    await POST(req({ reason: "   " }), params);
    expect(unblock.mock.calls).toEqual([
      ["digest", "t_1", "list attached"],
      ["digest", "t_1", undefined],
    ]);
  });
});
