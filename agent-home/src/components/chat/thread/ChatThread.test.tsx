// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
  attachChatStream: vi.fn(),
  cancelChatTurn: vi.fn(),
}));

// Count every MessageBubble render: the real one is memoized, so wrapping it
// in a plain component counts each time the history list itself re-renders.
const bubbleRenders = { n: 0 };
vi.mock("@/components/chat/MessageBubble", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/chat/MessageBubble")>();
  const Real = actual.MessageBubble;
  return {
    MessageBubble: (props: Parameters<typeof Real>[0]) => {
      bubbleRenders.n += 1;
      return <Real {...props} />;
    },
  };
});

import { ChatThread } from "@/components/chat/thread/ChatThread";
import { ChatController } from "@/lib/chat/chat-controller";
import { streamChatTurn } from "@/lib/chat/stream";
import type { ChatMessage } from "@/types";

// Fake layout for the thread scroller (jsdom has none).
const m = { scrollHeight: 1000, clientHeight: 400, scrollTop: 0 };
const isScroller = (el: Element) =>
  (el as HTMLElement).dataset?.component === "ChatThread";
const saved: Record<string, PropertyDescriptor | undefined> = {};
let roCallbacks: Array<() => void> = [];
class FakeResizeObserver {
  private cb: () => void;
  constructor(cb: () => void) {
    this.cb = cb;
    roCallbacks.push(cb);
  }
  observe() {}
  disconnect() {
    roCallbacks = roCallbacks.filter((c) => c !== this.cb);
  }
}

beforeAll(() => {
  for (const key of ["scrollHeight", "clientHeight", "scrollTop"]) {
    saved[key] = Object.getOwnPropertyDescriptor(Element.prototype, key);
  }
  Object.defineProperty(Element.prototype, "scrollHeight", {
    configurable: true,
    get() {
      return isScroller(this) ? m.scrollHeight : 0;
    },
  });
  Object.defineProperty(Element.prototype, "clientHeight", {
    configurable: true,
    get() {
      return isScroller(this) ? m.clientHeight : 0;
    },
  });
  Object.defineProperty(Element.prototype, "scrollTop", {
    configurable: true,
    get() {
      return isScroller(this) ? m.scrollTop : 0;
    },
    set(v: number) {
      if (isScroller(this)) {
        m.scrollTop = Math.max(0, Math.min(v, m.scrollHeight - m.clientHeight));
      }
    },
  });
});

afterAll(() => {
  for (const [key, desc] of Object.entries(saved)) {
    if (desc) Object.defineProperty(Element.prototype, key, desc);
  }
});

beforeEach(() => {
  m.scrollHeight = 1000;
  m.clientHeight = 400;
  m.scrollTop = 0;
  roCallbacks = [];
  bubbleRenders.n = 0;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(streamChatTurn).mockReset();
});

/** Let the controller's rAF notification (and any queued timers) fire. */
const nextFrame = () =>
  act(() => new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 0))));

const history: ChatMessage[] = [
  { id: 10, role: "user", content: "hello there" },
  { id: 11, role: "assistant", content: "General Kenobi" },
];

/** A send whose stream the test drives through the captured handlers. */
function controlledStream() {
  const ctl = {} as {
    handlers: ChatStreamHandlers;
    finish: (v: { sessionId: string }) => void;
  };
  vi.mocked(streamChatTurn).mockImplementationOnce(async (_p, h) => {
    ctl.handlers = h;
    return new Promise((r) => {
      ctl.finish = r;
    });
  });
  return ctl;
}

function setup(hasMore = false) {
  const c = new ChatController();
  c.seed("s1", history, hasMore);
  const utils = render(<ChatThread controller={c} sessionId="s1" empty={<p>empty</p>} />);
  return { c, ...utils };
}

describe("ChatThread", () => {
  it("renders the history, then the live reply, then the final row", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ messages: [] }), { status: 500 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const ctl = controlledStream();
    const { c, container } = setup();
    expect(screen.getByText("hello there")).toBeTruthy();
    expect(screen.getByText("General Kenobi")).toBeTruthy();
    expect(screen.queryByText("empty")).toBeNull();
    const indices = [...container.querySelectorAll("[data-msg-index]")].map((el) =>
      el.getAttribute("data-msg-index"),
    );
    expect(indices).toEqual(["0", "1"]);

    await act(async () => {
      void c.send("s1", "next question", []);
    });
    await nextFrame();
    // Accepted, nothing streamed yet: activity line only, no empty bubble.
    expect(screen.getByText("next question")).toBeTruthy();
    expect(container.querySelector('[data-component="ActivityLine"]')).not.toBeNull();
    expect(container.querySelector('[data-component="LiveReply"]')).toBeNull();

    act(() => {
      ctl.handlers.onAccepted?.("r1", "s1");
      ctl.handlers.onDelta?.("**bold** start");
    });
    await nextFrame();
    const live = container.querySelector('[data-component="LiveReply"]');
    expect(live?.querySelector('[data-component="StreamingMarkdown"]')).not.toBeNull();
    expect(live?.textContent).toContain("bold start");

    await act(async () => {
      ctl.handlers.onCompleted?.("**bold** start, done", "s1");
      ctl.finish({ sessionId: "s1" });
    });
    await nextFrame();
    expect(container.querySelector('[data-component="LiveTurn"]')).toBeNull();
    expect(container.querySelectorAll('[data-component="MessageBubble"]')).toHaveLength(4);
    expect(screen.getByText(/start, done/)).toBeTruthy();
  });

  it("streaming 50 deltas does not re-render the history list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    const ctl = controlledStream();
    const { c, container } = setup();
    await act(async () => {
      void c.send("s1", "go", []);
    });
    await nextFrame();
    act(() => ctl.handlers.onAccepted?.("r1", "s1"));
    await nextFrame();
    const before = bubbleRenders.n;
    expect(before).toBeGreaterThan(0);
    for (let i = 0; i < 50; i += 1) {
      act(() => ctl.handlers.onDelta?.(`w${i} `));
      if (i % 5 === 0) await nextFrame();
    }
    await nextFrame();
    expect(container.querySelector('[data-component="LiveReply"]')?.textContent).toContain(
      "w49",
    );
    // At most one history pass (3 rows) for the whole stream.
    expect(bubbleRenders.n - before).toBeLessThanOrEqual(3);
  });

  it("resolves an inline approval through the controller", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const ctl = controlledStream();
    const { c, container } = setup();
    await act(async () => {
      void c.send("s1", "delete it", []);
    });
    act(() => {
      ctl.handlers.onAccepted?.("r1", "s1");
      ctl.handlers.onApproval?.({
        runId: "r1",
        command: "rm -rf /tmp/x",
        choices: ["once", "deny"],
      });
    });
    await nextFrame();
    const card = container.querySelector('[data-component="ApprovalCard"]');
    expect(card?.textContent).toContain("rm -rf /tmp/x");
    expect(document.querySelector('[role="dialog"]')).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    });
    await nextFrame();
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/chat/approval");
    expect(call).toBeTruthy();
    expect(JSON.parse(call![1]!.body as string)).toMatchObject({
      runId: "r1",
      choice: "deny",
    });
    expect(container.querySelector('[data-component="ApprovalCard"]')).toBeNull();
    expect(container.querySelector('[data-component="DecisionNotice"]')?.textContent).toMatch(
      /Denied/,
    );
  });

  it("'Load earlier messages' pages older history in", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({
            messages: [{ id: 5, role: "user", content: "the very first" }],
            has_more: false,
          }),
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { c } = setup(true);
    const spy = vi.spyOn(c, "loadOlder");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Load earlier messages" }));
    });
    expect(spy).toHaveBeenCalledWith("s1");
    expect(String(fetchMock.mock.calls[0][0])).toContain("before=10");
    await nextFrame();
    expect(screen.getByText("the very first")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Load earlier messages" })).toBeNull();
  });

  it("shows the empty slot for a new chat, a skeleton while loading, then the error", async () => {
    let answer!: (r: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (answer = r))));
    const c = new ChatController();
    const { container, rerender } = render(
      <ChatThread controller={c} sessionId={null} empty={<p>empty</p>} density="compact" />,
    );
    expect(screen.getByText("empty")).toBeTruthy();
    rerender(
      <ChatThread controller={c} sessionId="s2" empty={<p>empty</p>} density="compact" />,
    );
    // Not opened yet: a skeleton, never a flash of the empty state.
    expect(container.querySelector('[data-component="ThreadSkeleton"]')).not.toBeNull();
    await act(async () => {
      void c.open("s2");
    });
    await nextFrame();
    expect(container.querySelector('[data-component="ThreadSkeleton"]')).not.toBeNull();
    expect(screen.queryByText("empty")).toBeNull();
    await act(async () => {
      answer(new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    });
    await nextFrame();
    expect(container.querySelector('[data-component="ThreadSkeleton"]')).toBeNull();
    expect(screen.getByRole("alert").textContent).toBe("boom");
  });

  describe("with a ResizeObserver", () => {
    beforeEach(() => vi.stubGlobal("ResizeObserver", FakeResizeObserver));

    it("shows a 'New messages' pill when content grows while scrolled up", async () => {
      const { container } = setup();
      const scroller = container.querySelector('[data-component="ChatThread"]')!;
      // Pinned to the bottom on mount.
      expect(m.scrollTop).toBe(600);
      expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull();

      m.scrollTop = 100;
      fireEvent.scroll(scroller);
      await nextFrame();
      m.scrollHeight = 1400;
      act(() => roCallbacks.forEach((cb) => cb()));
      const pill = screen.getByRole("button", { name: "Jump to latest" });
      expect(pill.textContent).toContain("New messages");
      expect(m.scrollTop).toBe(100);

      act(() => fireEvent.click(pill));
      expect(m.scrollTop).toBe(1000);
      expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull();
    });
  });
});
