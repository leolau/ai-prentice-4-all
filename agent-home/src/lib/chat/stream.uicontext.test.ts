import { afterEach, describe, expect, it, vi } from "vitest";

import { setUiContext } from "@/lib/app-mcp/state";
import { streamChatTurn } from "@/lib/chat/stream";

function emptyStreamResponse(): Response {
  return new Response("event: done\ndata: {}\n\n", {
    status: 200,
    headers: { "x-hermes-session-id": "sess_1" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setUiContext({ path: "", element: null });
});

describe("streamChatTurn app-mcp awareness", () => {
  it("attaches the formatted UI context when the bridge knows the state", async () => {
    setUiContext({ path: "/todos", element: { role: "button", name: "Filter" } });
    const fetchMock = vi.fn(async (...args: [url: string, init?: RequestInit]) => {
      void args;
      return emptyStreamResponse();
    });
    vi.stubGlobal("fetch", fetchMock);

    await streamChatTurn(
      { sessionId: "sess_1", message: "hello", attachments: [] },
      {},
    );

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)) as {
      uiContext?: string;
    };
    expect(body.uiContext).toBe(
      '[app context: page /todos · last active: button "Filter"]',
    );
  });

  it("omits uiContext before the bridge has reported anything", async () => {
    const fetchMock = vi.fn(async (...args: [url: string, init?: RequestInit]) => {
      void args;
      return emptyStreamResponse();
    });
    vi.stubGlobal("fetch", fetchMock);

    await streamChatTurn(
      { sessionId: "sess_1", message: "hello", attachments: [] },
      {},
    );

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)) as {
      uiContext?: string;
    };
    expect(body.uiContext).toBeUndefined();
  });
});
