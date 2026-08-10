/**
 * Client SSE reader tests (Option A approval surface).
 *
 * The reader must accumulate assistant deltas, surface an `approval.request`
 * mid-stream (the new event that lets agent-home approve gated tools), and
 * finish on the completed content — across arbitrary chunk boundaries.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatTurn } from "@/lib/chat/stream";
import type { ChatApprovalRequest } from "@/types";

function sseResponse(frames: string[], sessionId = "home_stream_1"): Response {
  // Emit the frames in small, awkwardly-split chunks so the parser's buffering
  // across chunk boundaries is exercised, not just whole-frame reads.
  const raw = frames.map((f) => `${f}\n\n`).join("");
  const bytes = new TextEncoder().encode(raw);
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += 7) {
        controller.enqueue(bytes.slice(i, i + 7));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "x-hermes-session-id": sessionId,
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("streamChatTurn", () => {
  it("accumulates deltas and resolves to the landed session id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: run.started\ndata: {"run_id":"run_1"}',
          'event: assistant.delta\ndata: {"delta":"Hello"}',
          'event: assistant.delta\ndata: {"delta":" world"}',
          'event: assistant.completed\ndata: {"content":"Hello world"}',
          'event: run.completed\ndata: {"session_id":"home_landed"}',
          "event: done\ndata: {}",
        ]),
      ),
    );

    const deltas: string[] = [];
    let completed = "";
    const { sessionId } = await streamChatTurn(
      { sessionId: null, message: "hi", attachments: [] },
      {
        onDelta: (d) => deltas.push(d),
        onCompleted: (content) => {
          completed = content;
        },
      },
    );

    expect(deltas.join("")).toBe("Hello world");
    expect(completed).toBe("Hello world");
    expect(sessionId).toBe("home_landed");
  });

  it("surfaces an approval.request with its run_id and choices", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: run.started\ndata: {"run_id":"run_abc"}',
          'event: approval.request\ndata: {"run_id":"run_abc","command":"list_calendars","description":"needs approval","choices":["once","session","always","deny"]}',
          'event: assistant.delta\ndata: {"delta":"Here is your calendar."}',
          'event: assistant.completed\ndata: {"content":"Here is your calendar."}',
          'event: run.completed\ndata: {"session_id":"home_stream_1"}',
          "event: done\ndata: {}",
        ]),
      ),
    );

    let approval: ChatApprovalRequest | null = null;
    await streamChatTurn(
      { sessionId: "home_stream_1", message: "calendar?", attachments: [] },
      { onApproval: (req) => (approval = req) },
    );

    expect(approval).not.toBeNull();
    const req = approval as unknown as ChatApprovalRequest;
    expect(req.runId).toBe("run_abc");
    expect(req.command).toBe("list_calendars");
    expect(req.choices).toEqual(["once", "session", "always", "deny"]);
  });

  it("reports a stream-level error via onError without throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: error\ndata: {"message":"boom"}',
          "event: done\ndata: {}",
        ]),
      ),
    );

    let errorMessage = "";
    await streamChatTurn(
      { sessionId: "home_stream_1", message: "x", attachments: [] },
      { onError: (m) => (errorMessage = m) },
    );

    expect(errorMessage).toBe("boom");
  });

  it("throws a friendly detail on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "The AI layer could not be reached." }), {
          status: 502,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(
      streamChatTurn(
        { sessionId: null, message: "x", attachments: [] },
        {},
      ),
    ).rejects.toThrow("The AI layer could not be reached.");
  });
});
