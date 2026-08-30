import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChatTurn, type ChatToolEvent } from "@/lib/chat/stream";

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "x-hermes-session-id": "sess_1" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChatTurn reasoning and tool events", () => {
  it("dispatches reasoning.delta, tool.start and tool.complete frames", async () => {
    const body = [
      "event: reasoning.delta\ndata: {\"text\":\"checking the run…\"}",
      "event: tool.start\ndata: {\"tool_id\":\"tc1\",\"name\":\"execute_code\"}",
      "event: tool.complete\ndata: {\"tool_id\":\"tc1\",\"name\":\"execute_code\"}",
      "event: reasoning.delta\ndata: {\"text\":\"\"}",
      "event: assistant.delta\ndata: {\"delta\":\"The run \"}",
      "event: assistant.completed\ndata: {\"content\":\"The run is healthy.\"}",
      "event: run.completed\ndata: {\"session_id\":\"sess_1\"}",
      "event: done\ndata: {}",
    ].join("\n\n") + "\n\n";
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse(body)));

    const reasoning: string[] = [];
    const starts: ChatToolEvent[] = [];
    const completes: ChatToolEvent[] = [];
    const deltas: string[] = [];
    await streamChatTurn(
      { sessionId: "sess_1", message: "hi", attachments: [] },
      {
        onReasoning: (t) => reasoning.push(t),
        onToolStart: (t) => starts.push(t),
        onToolComplete: (t) => completes.push(t),
        onDelta: (d) => deltas.push(d),
      },
    );

    expect(reasoning).toEqual(["checking the run…"]);
    expect(starts).toEqual([{ id: "tc1", name: "execute_code" }]);
    expect(completes).toEqual([{ id: "tc1", name: "execute_code" }]);
    expect(deltas).toEqual(["The run "]);
  });

  it("delivers reasoning before completion and completes once", async () => {
    const body =
      "event: reasoning.delta\ndata: {\"text\":\"thinking\"}\n\n" +
      "event: assistant.completed\ndata: {\"content\":\"done\"}\n\n";
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse(body)));

    const order: string[] = [];
    await streamChatTurn(
      { sessionId: "sess_1", message: "hi", attachments: [] },
      {
        onReasoning: () => order.push("reasoning"),
        onCompleted: () => order.push("completed"),
      },
    );
    expect(order).toEqual(["reasoning", "completed"]);
  });
});
