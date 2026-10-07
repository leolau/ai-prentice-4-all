/** BFF route tests for a card approval: the gate, and only a real decision forwarded. */
import { describe, expect, it, vi } from "vitest";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { POST } from "./route";

const params = { params: Promise.resolve({ slug: "tender", taskId: "t_1" }) };

function req(body: unknown): Request {
  return new Request("http://x/api/projects/tender/cards/t_1/approvals", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

describe("POST /api/projects/:slug/cards/:taskId/approvals", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    clientState.client = { decideProjectCardApproval: vi.fn() };
    expect((await POST(req({ key: "k", decision: "approve" }), params)).status).toBe(401);
  });

  it("forwards a trimmed key and the decision", async () => {
    principalState.principal = { user_id: "leo" };
    const decide = vi.fn(async () => ({ id: "t_1", status: "ready" }));
    clientState.client = { decideProjectCardApproval: decide };
    const res = await POST(req({ key: "  mcp_canva_create_upload_url ", decision: "deny" }), params);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: "t_1", status: "ready" });
    expect(decide.mock.calls).toEqual([["tender", "t_1", "mcp_canva_create_upload_url", "deny"]]);
  });

  it("refuses a missing key or an unknown decision without calling the API", async () => {
    principalState.principal = { user_id: "leo" };
    const decide = vi.fn();
    clientState.client = { decideProjectCardApproval: decide };
    expect((await POST(req({ key: "k", decision: "maybe" }), params)).status).toBe(422);
    expect((await POST(req({ key: "  ", decision: "approve" }), params)).status).toBe(422);
    expect(decide).not.toHaveBeenCalled();
  });
});
