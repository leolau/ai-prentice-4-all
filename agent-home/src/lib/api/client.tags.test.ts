/**
 * API client tests for session tag and search methods (PR #155).
 *
 * Mocks `globalThis.fetch` to verify URL, method, headers, and body for
 * each new HermesApiClient method added for the tagging/search features.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("HermesApiClient session tags + search", () => {
  it("listTags GETs /api/sessions/tags", async () => {
    const payload = { tags: [{ id: "t1", name: "bug", color: "red", session_count: 2 }] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.listTags();

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/sessions/tags");
  });

  it("createTag POSTs name + color to /api/sessions/tags", async () => {
    const payload = { tag: { id: "t1", name: "bug", color: "red" } };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.createTag("bug", "red");

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions/tags");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ name: "bug", color: "red" });
  });

  it("createTag defaults color to blue when omitted", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ tag: { id: "t1", name: "x", color: "blue" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.createTag("x");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      name: "x",
      color: "blue",
    });
  });

  it("getSessionTags GETs the encoded path", async () => {
    const payload = { tags: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.getSessionTags("home a/b");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/sessions/home%20a%2Fb/tags",
    );
  });

  it("addSessionTag POSTs name + color", async () => {
    const payload = { tag: { id: "t1", name: "bug", color: "red" } };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.addSessionTag("s1", "bug", "red");

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions/s1/tags");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ name: "bug", color: "red" });
  });

  it("addSessionTag defaults color to blue", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ tag: { id: "t1", name: "x", color: "blue" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.addSessionTag("s1", "x");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      name: "x",
      color: "blue",
    });
  });

  it("removeSessionTag DELETEs the encoded path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.removeSessionTag("s1", "tag a/b");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions/s1/tags/tag%20a%2Fb");
    expect(init?.method).toBe("DELETE");
  });

  it("deleteTag DELETEs the encoded tag path", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ ok: true }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.deleteTag("t1");

    expect(fetchMock.mock.calls[0][0]).toBe("http://api.test/api/sessions/tags/t1");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
  });

  it("suggestSessionTags POSTs to the suggest endpoint", async () => {
    const payload = {
      suggestions: [{ tag_name: "bug", is_new: true, reason: "about bugs", confidence: 0.8 }],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.suggestSessionTags("s1");

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions/s1/tags/suggest");
    expect(init?.method).toBe("POST");
  });

  it("searchSessions GETs search with q param", async () => {
    const payload = { results: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.searchSessions("hello", 5);

    expect(res).toEqual(payload);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/sessions/search?q=hello&limit=5",
    );
  });

  it("sessions forwards tag filter params in URL", async () => {
    const payload = { sessions: [], total: 0, limit: 30, offset: 0 };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.sessions({
      source: "agent_home",
      tags: "bug,urgent",
      excludeTags: "closed",
      tagMatch: "all",
    });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("tags=bug%2Curgent");
    expect(url).toContain("exclude_tags=closed");
    expect(url).toContain("tag_match=all");
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "nope" }), { status: 404 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.listTags()).rejects.toBeInstanceOf(HermesApiError);
  });
});
