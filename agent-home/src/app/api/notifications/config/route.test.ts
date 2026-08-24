import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/notifications/config/route";

const getVapid = vi.fn();
const listSubscriptions = vi.fn();
const pushConfigured = vi.fn();

vi.mock("@/lib/push/store", () => ({
  getVapid: () => getVapid(),
  listSubscriptions: () => listSubscriptions(),
  pushConfigured: () => pushConfigured(),
}));

function get(secret?: string): Promise<Response> {
  const headers = new Headers();
  if (secret !== undefined) headers.set("x-app-push-secret", secret);
  return GET(new Request("http://localhost/api/notifications/config", { headers })) as unknown as Promise<Response>;
}

describe("GET /api/notifications/config", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pushConfigured.mockReturnValue(true);
  });

  afterEach(() => vi.unstubAllEnvs());

  it("401s without the shared secret", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    const res = await get();
    expect(res.status).toBe(401);
  });

  it("401s on a wrong secret and never touches the store", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    const res = await get("nope");
    expect(res.status).toBe(401);
    expect(getVapid).not.toHaveBeenCalled();
  });

  it("401s when the box has no secret configured", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "");
    const res = await get("anything");
    expect(res.status).toBe(401);
  });

  it("hands the sender the private key and subscriptions", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    getVapid.mockResolvedValue({ private_key: "PEM", public_key: "PUB" });
    listSubscriptions.mockResolvedValue([
      { endpoint: "https://push.example/a", keys: { p256dh: "k", auth: "a" } },
    ]);
    const res = await get("s3cret");
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.vapid_private_key).toBe("PEM");
    expect(body.subscriptions).toEqual([
      { endpoint: "https://push.example/a", keys: { p256dh: "k", auth: "a" } },
    ]);
  });

  it("404s before any device has enrolled", async () => {
    vi.stubEnv("AGENT_HOME_APP_PUSH_SECRET", "s3cret");
    getVapid.mockResolvedValue(null);
    const res = await get("s3cret");
    expect(res.status).toBe(404);
  });
});
