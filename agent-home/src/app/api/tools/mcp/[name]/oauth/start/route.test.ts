/**
 * BFF route tests for POST /api/tools/mcp/[name]/oauth/start — kicks off the
 * browser-driven MCP OAuth flow. Contract: auth gate and upstream error
 * mapping (a 409 from the AI layer when another flow is in flight must reach
 * the UI intact, not collapse to 500).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/tools/mcp/[name]/oauth/start/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const mcpOauthStart = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ mcpOauthStart }),
}));

function post(): Promise<Response> {
  const req = new Request("http://x/api/tools/mcp/canva/oauth/start", {
    method: "POST",
  });
  return POST(req, {
    params: Promise.resolve({ name: "canva" }),
  }) as unknown as Promise<Response>;
}

describe("POST /api/tools/mcp/[name]/oauth/start", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "leo",
      display: "Leo",
      role: "owner",
      channels: [],
      is_owner: true,
    });
    mcpOauthStart.mockResolvedValue({ status: "starting", name: "canva" });
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await post();
    expect(res.status).toBe(401);
    expect(mcpOauthStart).not.toHaveBeenCalled();
  });

  it("returns the flow state", async () => {
    const res = await post();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "starting", name: "canva" });
    expect(mcpOauthStart).toHaveBeenCalledWith("canva");
  });

  it("preserves a 409 when another flow is running", async () => {
    mcpOauthStart.mockRejectedValue(
      new HermesApiError(409, "An OAuth flow for 'figma' is already running"),
    );
    const res = await post();
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.detail).toContain("figma");
  });
});
