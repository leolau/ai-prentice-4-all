import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("HermesApiClient user management (FG-26 BFF forwarding)", () => {
  it("members() GETs /api/comms/members with the page window and replays the token", async () => {
    const payload = {
      configured: true,
      members: [],
      total: 0,
      limit: 25,
      offset: 0,
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-abc",
      baseUrl: "http://api.test",
    });
    const res = await client.members({ limit: 25, offset: 50, q: "mi a", active: false });

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://api.test/api/comms/members?limit=25&offset=50&q=mi+a&active=false",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-abc");
    expect(init?.cache).toBe("no-store");
  });

  it("createMember() sends the required profile and never a password", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, member: { user_id: "u", display: "M", role: "member" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.createMember({
      email: "m@x.io",
      profile: "acme",
      display: "Mia",
      role: "member",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      email: "m@x.io",
      profile: "acme",
      display: "Mia",
      role: "member",
    });
    // The account is created banned with a server-side random password; the
    // browser has no business choosing or seeing one.
    expect(body).not.toHaveProperty("password");
  });

  it("exposes no password-setting method at all", () => {
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    expect("setMemberPassword" in client).toBe(false);
  });

  it("issueMemberInvitation() POSTs to the encoded invitation path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, activation_path: "/activate/abc" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.issueMemberInvitation("u/1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members/u%2F1/invitation");
    expect(init?.method).toBe("POST");
  });

  it("revokeMemberInvitation() DELETEs the invitation path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, revoked: 1 }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.revokeMemberInvitation("u1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members/u1/invitation");
    expect(init?.method).toBe("DELETE");
  });

  it("deleteMember() requires a strategy and passes the successor", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, strategy: "transfer" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.deleteMember("u1", { strategy: "transfer", transferTo: "u2" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://api.test/api/comms/members/u1?strategy=transfer&transfer_to=u2",
    );
    expect(init?.method).toBe("DELETE");
  });

  it("redeemInvitation() POSTs the token unauthenticated", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ ok: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.redeemInvitation({ token: "raw", password: "correct horse b" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/auth/invitations/redeem");
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBeNull();
  });

  it("setMemberRole() PUTs the role to the encoded member path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true, member: { user_id: "u/1", role: "admin" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.setMemberRole("u/1", "admin");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/comms/members/u%2F1/role");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ role: "admin" });
  });

  it("deactivateMember()/activateMember() POST to the right paths", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => ok({ ok: true, active: false }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.deactivateMember("u1");
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/comms/members/u1/deactivate",
    );
    await client.activateMember("u1");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://api.test/api/comms/members/u1/activate",
    );
  });
});
