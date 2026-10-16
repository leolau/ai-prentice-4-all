/**
 * BFF route tests for Settings → In&Out → Private networks (VPN): every
 * route requires a principal, forwards under the bridged client, passes the
 * Python layer's HTTP errors through, and keeps connection failures (200 +
 * status.state "error") as plain successes.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PUT as credentialPUT } from "@/app/api/credentials/[provider]/[name]/route";
import { POST as actionPOST } from "@/app/api/vpn/[name]/[action]/route";
import { GET } from "@/app/api/vpn/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const vpns = vi.fn();
const vpnAction = vi.fn();
const trustVpnCert = vi.fn();
const putCredential = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ vpns, vpnAction, trustVpnCert, putCredential }),
}));

const ALICE: Principal = {
  user_id: "alice",
  display: "Alice",
  role: "member",
  channels: [],
  is_owner: false,
};

const SHA = "9f2c7a41b0e5d3c86a1f04e2b97d58c3e0a4b6f1c2d39e875b0a6c4d2e1f3a90";

const VIEW = {
  name: "Office files",
  slug: "office-files",
  status: { state: "error", error: "Login failed: wrong username or password" },
};

function actionCtx(name: string, action: string) {
  return { params: Promise.resolve({ name, action }) };
}

function post(body?: unknown): Request {
  return new Request("http://localhost/api/vpn/x/y", {
    method: "POST",
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  getPrincipal.mockResolvedValue(ALICE);
});

describe("GET /api/vpn", () => {
  it("401s when unauthenticated and never calls Hermes", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await GET();
    expect(res.status).toBe(401);
    expect(vpns).not.toHaveBeenCalled();
  });

  it("proxies the caller's VPN list", async () => {
    vpns.mockResolvedValue({ vpns: [VIEW] });
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ vpns: [VIEW] });
  });

  it("passes an upstream HTTP error through", async () => {
    vpns.mockRejectedValue(new HermesApiError(403, "forbidden"));
    const res = await GET();
    expect(res.status).toBe(403);
  });

  it("502s when Hermes is unreachable", async () => {
    vpns.mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await GET();
    expect(res.status).toBe(502);
  });
});

describe("POST /api/vpn/{name}/{action}", () => {
  it("401s when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await actionPOST(post(), actionCtx("Office files", "connect"));
    expect(res.status).toBe(401);
    expect(vpnAction).not.toHaveBeenCalled();
  });

  it.each(["connect", "disconnect", "test"])("forwards %s", async (action) => {
    vpnAction.mockResolvedValue({ vpn: VIEW });
    const res = await actionPOST(post(), actionCtx("Office files", action));
    expect(res.status).toBe(200);
    expect(vpnAction).toHaveBeenCalledWith("Office files", action);
    // A failed login is a 200 carrying the error state, not an HTTP error.
    expect((await res.json()).vpn.status.state).toBe("error");
  });

  it("404s an unknown action without calling Hermes", async () => {
    const res = await actionPOST(post(), actionCtx("Office files", "rm -rf"));
    expect(res.status).toBe(404);
    expect(vpnAction).not.toHaveBeenCalled();
    expect(trustVpnCert).not.toHaveBeenCalled();
  });

  it("forwards trust with the lowercased fingerprint", async () => {
    trustVpnCert.mockResolvedValue({ vpn: VIEW });
    const res = await actionPOST(
      post({ sha256: SHA.toUpperCase() }),
      actionCtx("Office files", "trust"),
    );
    expect(res.status).toBe(200);
    expect(trustVpnCert).toHaveBeenCalledWith("Office files", SHA);
  });

  it.each([undefined, {}, { sha256: "abc" }, { sha256: `${SHA}00` }, { sha256: 12 }])(
    "400s trust with a bad fingerprint %j",
    async (body) => {
      const res = await actionPOST(post(body), actionCtx("Office files", "trust"));
      expect(res.status).toBe(400);
      expect(trustVpnCert).not.toHaveBeenCalled();
    },
  );

  it("passes a 404 for an unknown VPN through", async () => {
    vpnAction.mockRejectedValue(new HermesApiError(404, "unknown vpn"));
    const res = await actionPOST(post(), actionCtx("Nope", "connect"));
    expect(res.status).toBe(404);
    expect((await res.json()).detail).toContain("unknown vpn");
  });
});

describe("PUT /api/credentials/{provider}/{name}", () => {
  const ctx = { params: Promise.resolve({ provider: "fortinet", name: "Office files" }) };
  function put(body: unknown): Request {
    return new Request("http://localhost/api/credentials/fortinet/Office%20files", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  const payload = { host: "vpn.example.com", username: "leo", password: "pw" };

  it("401s when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await credentialPUT(put({ kind: "fortinet-vpn", payload }), ctx);
    expect(res.status).toBe(401);
    expect(putCredential).not.toHaveBeenCalled();
  });

  it.each([{}, { kind: "fortinet-vpn" }, { payload }, { kind: "fortinet-vpn", payload: [] }])(
    "400s a malformed body %j",
    async (body) => {
      const res = await credentialPUT(put(body), ctx);
      expect(res.status).toBe(400);
      expect(putCredential).not.toHaveBeenCalled();
    },
  );

  it("pins 'private' to the caller", async () => {
    putCredential.mockResolvedValue({ credential: {} });
    const res = await credentialPUT(
      put({ kind: "fortinet-vpn", payload, visibility: "private" }),
      ctx,
    );
    expect(res.status).toBe(200);
    expect(putCredential).toHaveBeenCalledWith("fortinet", "Office files", {
      kind: "fortinet-vpn",
      payload,
      visibility: "private:alice",
      services: undefined,
    });
  });

  it("forwards an explicit visibility unchanged", async () => {
    putCredential.mockResolvedValue({ credential: {} });
    await credentialPUT(put({ kind: "fortinet-vpn", payload, visibility: "shared" }), ctx);
    expect(putCredential.mock.calls[0][2].visibility).toBe("shared");
  });

  it("passes the store's 400 through", async () => {
    putCredential.mockRejectedValue(new HermesApiError(400, "missing field: host"));
    const res = await credentialPUT(put({ kind: "fortinet-vpn", payload: {} }), ctx);
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toContain("missing field");
  });
});
