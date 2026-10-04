/**
 * BFF route tests for Approve all N: the principal gate, an empty or
 * duplicate-laden id list cleaned before it reaches Python, and the
 * upstream's refusal passed through.
 */
import { describe, expect, it, vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { POST } from "./route";

const params = { params: Promise.resolve({ slug: "digest" }) };

function req(body: unknown): Request {
  return new Request("http://x/api/projects/digest/cards/approve", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

describe("POST /api/projects/:slug/cards/approve", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    expect((await POST(req({ task_ids: ["t_1"] }), params)).status).toBe(401);
  });

  it("refuses an empty list without calling upstream", async () => {
    principalState.principal = { user_id: "leo" };
    const approve = vi.fn();
    clientState.client = { approveProjectCards: approve };
    const res = await POST(req({ task_ids: [" ", null] }), params);
    expect(res.status).toBe(400);
    expect(approve).not.toHaveBeenCalled();
  });

  it("sends each card once, trimmed, and returns the results", async () => {
    principalState.principal = { user_id: "leo" };
    const approve = vi.fn(async () => ({ results: [], approved: 2, failed: 0 }));
    clientState.client = { approveProjectCards: approve };
    const res = await POST(req({ task_ids: ["t_1", " t_2 ", "t_1"] }), params);
    expect(res.status).toBe(200);
    expect(approve).toHaveBeenCalledWith("digest", ["t_1", "t_2"]);
    expect(await res.json()).toEqual({ results: [], approved: 2, failed: 0 });
  });

  it("passes the upstream refusal through", async () => {
    principalState.principal = { user_id: "vic" };
    clientState.client = {
      approveProjectCards: async () => {
        throw new HermesApiError(403, "POST /cards/approve", { detail: "viewers cannot write" });
      },
    };
    const res = await POST(req({ task_ids: ["t_1"] }), params);
    expect(res.status).toBe(403);
    expect(((await res.json()) as { detail: string }).detail).toBe("viewers cannot write");
  });
});
