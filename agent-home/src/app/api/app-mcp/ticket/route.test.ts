import { createHmac } from "crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST, signTicket, TICKET_TTL_MS } from "@/app/api/app-mcp/ticket/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
}));

function post(): Promise<Response> {
  return POST() as unknown as Promise<Response>;
}

describe("POST /api/app-mcp/ticket", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "leo_owner",
      display: "Leo",
      role: "owner",
      channels: [],
      is_owner: true,
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("mints a signed 60s ticket for the signed-in principal", async () => {
    vi.stubEnv("AGENT_HOME_APP_MCP_SECRET", "shared-secret");
    const res = await post();
    expect(res.status).toBe(200);
    const body = (await res.json()) as { ticket: string; expires: number };
    const [userId, expires, sig] = body.ticket.split(".");
    expect(userId).toBe("leo_owner");
    expect(Number(expires)).toBeGreaterThan(Date.now());
    expect(Number(expires)).toBeLessThanOrEqual(Date.now() + TICKET_TTL_MS + 1000);
    // The service verifies with the same shared secret — check the signature.
    expect(sig).toBe(signTicket(`${userId}.${expires}`, "shared-secret"));
    expect(
      createHmac("sha256", "shared-secret")
        .update(`${userId}.${expires}`)
        .digest("base64url"),
    ).toBe(sig);
  });

  it("answers 401 without a principal", async () => {
    vi.stubEnv("AGENT_HOME_APP_MCP_SECRET", "shared-secret");
    getPrincipal.mockResolvedValue(null);
    const res = await post();
    expect(res.status).toBe(401);
  });

  it("answers 503 when the shared secret is not configured", async () => {
    vi.stubEnv("AGENT_HOME_APP_MCP_SECRET", "");
    const res = await post();
    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe("app_mcp_not_configured");
  });
});
