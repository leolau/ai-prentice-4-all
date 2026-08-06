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

describe("HermesApiClient Wave C1 chat (BFF forwarding)", () => {
  it("sessions GETs /api/sessions with source + recent order and replays the token", async () => {
    const payload = { sessions: [], total: 0, limit: 30, offset: 0 };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({
      hermesToken: "tok-abc",
      baseUrl: "http://api.test",
    });
    const res = await client.sessions({ source: "agent_home", order: "recent" });

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://api.test/api/sessions?source=agent_home&limit=30&order=recent",
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("cookie")).toBe("hermes_session_at=tok-abc");
    expect(init?.cache).toBe("no-store");
  });

  it("sessionMessages GETs the encoded transcript path", async () => {
    const payload = { session_id: "home a/b", messages: [] };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.sessionMessages("home a/b");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/sessions/home%20a%2Fb/messages",
    );
  });

  it("createSession POSTs an empty body when no id is supplied", async () => {
    const payload = { session_id: "home_1", source: "agent_home" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.createSession();

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({});
  });

  it("createSession POSTs the supplied session_id", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ session_id: "s1", source: "agent_home" }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.createSession("s1");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      session_id: "s1",
    });
  });

  it("sendChat POSTs the message to the session chat endpoint", async () => {
    const payload = {
      session_id: "s1",
      message: { role: "assistant", content: "hi there" },
      usage: { total_tokens: 12 },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok(payload));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    const res = await client.sendChat("s1", "hello");

    expect(res).toEqual(payload);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/sessions/s1/chat");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ message: "hello" });
  });

  it("sendChat forwards attachments so the box can read the upload", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ session_id: "s1", message: { role: "assistant", content: "ok" } }));

    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.sendChat("s1", "summarize", [
      { name: "r.pdf", content_type: "application/pdf", size: 9, url: "https://p.supabase.co/x" },
    ]);

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      message: "summarize",
      attachments: [
        { name: "r.pdf", content_type: "application/pdf", size: 9, url: "https://p.supabase.co/x" },
      ],
    });
  });

  it("sendChat omits attachments when none are given", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(ok({ session_id: "s1", message: { role: "assistant", content: "ok" } }));
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.sendChat("s1", "hi", []);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ message: "hi" });
  });

  it("openChatStream forwards attachments in the streamed turn body", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("event: done\ndata: {}\n\n", { status: 200 }));
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await client.openChatStream("s1", "read this", [
      { name: "d.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size: 3, url: "https://p.supabase.co/y" },
    ]);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.message).toBe("read this");
    expect(body.attachments).toHaveLength(1);
    expect(body.attachments[0].url).toBe("https://p.supabase.co/y");
  });

  it("throws HermesApiError on a non-2xx upstream", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "nope" }), { status: 404 }),
    );
    const client = new HermesApiClient({ baseUrl: "http://api.test" });
    await expect(client.sendChat("missing", "x")).rejects.toBeInstanceOf(
      HermesApiError,
    );
  });
});
