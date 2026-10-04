// @vitest-environment jsdom
import { StrictMode } from "react";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
  attachChatStream: vi.fn(),
  cancelChatTurn: vi.fn(),
}));

import {
  ChatController,
  useBusyKeys,
  useChatController,
  useLiveTurn,
  useThread,
} from "@/lib/chat/chat-controller";
import { streamChatTurn } from "@/lib/chat/stream";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Let the controller's rAF/timeout notification fire. */
const nextFrame = () =>
  act(() => new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 0))));

describe("chat-controller React bindings", () => {
  it("streaming re-renders only the live subscriber, not the thread list", async () => {
    let handlers!: ChatStreamHandlers;
    let finish!: (v: { sessionId: string }) => void;
    vi.mocked(streamChatTurn).mockImplementationOnce(async (_p, h) => {
      handlers = h;
      return new Promise((r) => {
        finish = r;
      });
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              messages: [
                { id: 1, role: "user", content: "old" },
                { id: 2, role: "user", content: "hi" },
                { id: 3, role: "assistant", content: "abcd!" },
              ],
            }),
          ),
      ),
    );
    const c = new ChatController();
    c.seed("s1", [{ id: 1, role: "user", content: "old" }], false);
    let threadRenders = 0;
    let liveRenders = 0;
    function Thread() {
      const t = useThread(c, "s1");
      threadRenders += 1;
      return <ul>{t.messages.map((m) => <li key={m.id ?? m.clientKey}>{m.content}</li>)}</ul>;
    }
    function Live() {
      const live = useLiveTurn(c, "s1");
      liveRenders += 1;
      return <p data-testid="live">{live?.assistantText ?? ""}</p>;
    }
    const { getByTestId, container } = render(
      <>
        <Thread />
        <Live />
      </>,
    );
    await act(async () => {
      void c.send("s1", "hi", []);
    });
    await nextFrame();
    const threadBefore = threadRenders;
    const liveBefore = liveRenders;
    act(() => {
      for (const d of ["a", "b", "c", "d"]) handlers.onDelta?.(d);
    });
    await nextFrame();
    expect(getByTestId("live").textContent).toBe("abcd");
    expect(liveRenders).toBe(liveBefore + 1);
    expect(threadRenders).toBe(threadBefore);
    await act(async () => {
      handlers.onCompleted?.("abcd!", "s1");
      finish({ sessionId: "s1" });
    });
    await nextFrame();
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(getByTestId("live").textContent).toBe("");
    vi.unstubAllGlobals();
  });

  it("useBusyKeys tracks in-flight turns and useLiveTurn(null) is inert", async () => {
    vi.mocked(streamChatTurn).mockImplementationOnce(() => new Promise(() => {}));
    const c = new ChatController();
    const { result } = renderHook(() => ({
      busy: useBusyKeys(c),
      none: useLiveTurn(c, null),
    }));
    expect(result.current.busy).toEqual([]);
    await act(async () => {
      void c.send(null, "x", []);
    });
    await nextFrame();
    expect(result.current.busy).toEqual(["__new__"]);
    expect(result.current.none).toBeNull();
  });

  it("useChatController keeps one live instance through StrictMode and disposes on unmount", async () => {
    const { result, unmount } = renderHook(() => useChatController({ profile: "p" }), {
      wrapper: StrictMode,
    });
    const c = result.current;
    const dispose = vi.spyOn(c, "dispose");
    await act(() => new Promise((r) => setTimeout(r, 5)));
    expect(dispose).not.toHaveBeenCalled();
    unmount();
    await new Promise((r) => setTimeout(r, 5));
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});
