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

async function urlFor(opts?: Parameters<HermesApiClient["sessionMessages"]>[1]) {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(ok({ session_id: "s", messages: [] }));
  const client = new HermesApiClient({ baseUrl: "http://api.test" });
  await client.sessionMessages("home a/b", opts);
  return String(fetchMock.mock.calls[0][0]);
}

describe("HermesApiClient.sessionMessages query", () => {
  it("adds no query string without options (legacy read)", async () => {
    expect(await urlFor()).toBe("http://api.test/api/sessions/home%20a%2Fb/messages");
    vi.restoreAllMocks();
    expect(await urlFor({})).toBe("http://api.test/api/sessions/home%20a%2Fb/messages");
  });

  it("builds visible + limit + before", async () => {
    expect(await urlFor({ visible: true, limit: 40, before: 123 })).toBe(
      "http://api.test/api/sessions/home%20a%2Fb/messages?visible=true&limit=40&before=123",
    );
  });

  it("omits visible=false and non-finite numbers", async () => {
    expect(await urlFor({ visible: false, limit: Number.NaN, before: 7 })).toBe(
      "http://api.test/api/sessions/home%20a%2Fb/messages?before=7",
    );
  });
});
