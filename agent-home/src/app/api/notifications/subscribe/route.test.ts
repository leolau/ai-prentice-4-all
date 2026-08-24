import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DELETE, POST } from "@/app/api/notifications/subscribe/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const addSubscription = vi.fn();
const removeSubscription = vi.fn();
const pushConfigured = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
}));

vi.mock("@/lib/push/store", () => ({
  addSubscription: (rec: unknown) => addSubscription(rec),
  removeSubscription: (endpoint: string) => removeSubscription(endpoint),
  pushConfigured: () => pushConfigured(),
}));

const PRINCIPAL: Principal = {
  user_id: "leo_owner",
  display: "Leo",
  role: "owner",
  channels: [],
  is_owner: true,
};

const VALID_SUB = {
  endpoint: "https://push.example/device-1",
  keys: { p256dh: "p256dh-key", auth: "auth-key" },
};

function call(method: "POST" | "DELETE", body: unknown, secret?: string): Promise<Response> {
  const headers = new Headers({ "content-type": "application/json" });
  if (secret !== undefined) headers.set("x-app-push-secret", secret);
  const handler = method === "POST" ? POST : DELETE;
  return handler(
    new Request("http://localhost/api/notifications/subscribe", {
      method,
      headers,
      body: JSON.stringify(body),
    }),
  ) as unknown as Promise<Response>;
}

describe("POST /api/notifications/subscribe", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue(PRINCIPAL);
    pushConfigured.mockReturnValue(true);
  });

  afterEach(() => vi.unstubAllEnvs());

  it("stores a valid subscription", async () => {
    addSubscription.mockResolvedValue(undefined);
    const res = await call("POST", VALID_SUB);
    expect(res.status).toBe(200);
    expect(addSubscription).toHaveBeenCalledTimes(1);
    const stored = addSubscription.mock.calls[0][0] as Record<string, unknown>;
    expect(stored.endpoint).toBe(VALID_SUB.endpoint);
    expect(stored.keys).toEqual(VALID_SUB.keys);
    expect(typeof stored.created_at).toBe("string");
  });

  it("401s when signed out", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await call("POST", VALID_SUB);
    expect(res.status).toBe(401);
    expect(addSubscription).not.toHaveBeenCalled();
  });

  it("503s when the box has no push store", async () => {
    pushConfigured.mockReturnValue(false);
    const res = await call("POST", VALID_SUB);
    expect(res.status).toBe(503);
  });

  it("rejects malformed subscriptions", async () => {
    for (const bad of [
      {},
      { endpoint: "not-a-url", keys: { p256dh: "k", auth: "a" } },
      { endpoint: VALID_SUB.endpoint, keys: { p256dh: "", auth: "a" } },
      { endpoint: "javascript:alert(1)", keys: { p256dh: "k", auth: "a" } },
    ]) {
      const res = await call("POST", bad);
      expect(res.status).toBe(400);
    }
    expect(addSubscription).not.toHaveBeenCalled();
  });
});

describe("DELETE /api/notifications/subscribe", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue(null);
  });

  afterEach(() => vi.unstubAllEnvs());

  it("lets the signed-in user unsubscribe", async () => {
    getPrincipal.mockResolvedValue(PRINCIPAL);
    removeSubscription.mockResolvedValue(true);
    const res = await call("DELETE", { endpoint: VALID_SUB.endpoint });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "removed" });
  });

  it("lets the Python sender unsubscribe with the shared secret", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    removeSubscription.mockResolvedValue(true);
    const res = await call("DELETE", { endpoint: VALID_SUB.endpoint }, "s3cret");
    expect(res.status).toBe(200);
    expect(removeSubscription).toHaveBeenCalledWith(VALID_SUB.endpoint);
  });

  it("401s with neither principal nor secret", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    const res = await call("DELETE", { endpoint: VALID_SUB.endpoint }, "wrong");
    expect(res.status).toBe(401);
  });

  it("reports not_found when the endpoint was never enrolled", async () => {
    getPrincipal.mockResolvedValue(PRINCIPAL);
    removeSubscription.mockResolvedValue(false);
    const res = await call("DELETE", { endpoint: VALID_SUB.endpoint });
    expect(await res.json()).toEqual({ status: "not_found" });
  });
});
