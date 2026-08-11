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

function sse() {
  return new Response("event: assistant.completed\ndata: {}\n\n", {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function bound() {
  return new HermesApiClient({
    hermesToken: "tok",
    baseUrl: "http://api.test",
    profile: "maintenance",
  });
}

describe("HermesApiClient profile binding (FG-28)", () => {
  it("scopes reads by query so the list comes from that profile's store", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ sessions: [], total: 0, limit: 30, offset: 0 }));

    await bound().sessions({ source: "agent_home" });

    expect(String(fetchMock.mock.calls[0][0])).toContain("profile=maintenance");
  });

  it("scopes a turn by body so the requested profile's brain answers", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ session_id: "s1", message: {} }));

    await bound().sendChat("s1", "hello");

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ message: "hello", profile: "maintenance" });
  });

  it("scopes a streamed turn too — streams bypass the request wrapper", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sse());

    await bound().openChatStream("s1", "hello");

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ message: "hello", profile: "maintenance" });
  });

  it("leaves a single-profile deployment's requests untouched", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ session_id: "s1", message: {} }));

    // An explicit "default" is the box's own home, so it must not be sent —
    // otherwise every ordinary request looks like a cross-profile one.
    const client = new HermesApiClient({
      baseUrl: "http://api.test",
      profile: "default",
    });
    expect(client.boundProfile()).toBeUndefined();
    await client.sendChat("s1", "hello");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).not.toContain("profile=");
    expect(JSON.parse(String(init?.body))).toEqual({ message: "hello" });
  });
});
