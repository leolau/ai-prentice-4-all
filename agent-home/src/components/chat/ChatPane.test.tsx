// @vitest-environment jsdom
/**
 * The Chats page on the shared engine: phone list → thread navigation,
 * optimistic send + streaming, turns that keep running while another
 * conversation is open, and the default "All" category.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers, ChatStreamParams } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
  attachChatStream: vi.fn(),
  cancelChatTurn: vi.fn(),
}));

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn(), refresh: vi.fn() }),
}));

import { ChatPane, type ChatPaneProps } from "@/components/chat/ChatPane";
import { cancelChatTurn, streamChatTurn } from "@/lib/chat/stream";
import type { ChatMessage, SessionSummary } from "@/types";

const nowS = Math.floor(Date.now() / 1000);

function session(id: string, title: string, ago: number): SessionSummary {
  return {
    id,
    source: "agent-home",
    title,
    preview: `${title} preview`,
    started_at: nowS - ago,
    last_active: nowS - ago,
    message_count: 2,
    archived: false,
  } as SessionSummary;
}

const sessions = [session("s1", "Trip plan", 60), session("s2", "Recipes", 3600)];
const transcripts: Record<string, ChatMessage[]> = {
  s1: [
    { id: 1, role: "user", content: "plan a trip" },
    { id: 2, role: "assistant", content: "Where to?" },
  ],
  s2: [
    { id: 3, role: "user", content: "a soup recipe" },
    { id: 4, role: "assistant", content: "Try minestrone." },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/chat");
  window.localStorage.clear();
  routerPush.mockReset();
  vi.mocked(streamChatTurn).mockReset();
  vi.mocked(cancelChatTurn).mockReset().mockResolvedValue(undefined);
  fetchMock = vi.fn(async (input: string) => {
    const url = new URL(String(input), "http://x");
    if (url.pathname === "/api/chat/messages") {
      const id = url.searchParams.get("sessionId") ?? "";
      return json({ session_id: id, messages: transcripts[id] ?? [], has_more: false });
    }
    if (url.pathname === "/api/chat/active") return json({ runId: null });
    if (url.pathname === "/api/chat/sessions") return json({ sessions });
    if (url.pathname === "/api/chat/sessions/tags") return json({ tags: [] });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Below lg: jsdom has no matchMedia, which ChatPane reads as a phone. */
function renderPane(over: Partial<ChatPaneProps> = {}) {
  return render(
    <ChatPane
      initialSessions={sessions}
      initialSessionId="s1"
      initialMessages={transcripts.s1}
      initialHasMore={false}
      storageEnabled={false}
      profiles={[]}
      profile="default"
      {...over}
    />,
  );
}

/** Let the controller's rAF notifications and queued fetches land. */
const flush = () =>
  act(
    () =>
      new Promise<void>((r) =>
        requestAnimationFrame(() => setTimeout(() => requestAnimationFrame(() => r()), 0)),
      ),
  );

interface Turn {
  params: ChatStreamParams;
  handlers: ChatStreamHandlers;
  finish(sessionId: string): void;
}

/** Arm the next send; the test drives its stream. */
function armTurn(): Turn {
  const turn = {} as Turn;
  vi.mocked(streamChatTurn).mockImplementationOnce(async (params, handlers) => {
    turn.params = params;
    turn.handlers = handlers;
    return new Promise((resolve) => {
      turn.finish = (sessionId) => resolve({ sessionId });
    });
  });
  return turn;
}

const pane = (c: HTMLElement) =>
  c.querySelector('[data-component="ChatPane"]') as HTMLElement;
const row = (c: HTMLElement, id: string) =>
  c.querySelector(`[data-component="ConversationRow"][data-session-id="${id}"]`) as HTMLElement;
const thread = (c: HTMLElement) =>
  c.querySelector('[data-component="ChatThread"]') as HTMLElement;

async function sendText(text: string) {
  const box = screen.getByPlaceholderText(/Message your agent/);
  fireEvent.change(box, { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
  });
}

describe("ChatPane", () => {
  it("defaults the conversation list to the All category, newest first", () => {
    const { container } = renderPane();
    const all = screen.getByRole("button", { name: /^All/ });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    const ids = [...container.querySelectorAll('[data-component="ConversationRow"]')].map(
      (el) => el.getAttribute("data-session-id"),
    );
    expect(ids).toEqual(["s1", "s2"]);
  });

  it("on a phone goes list → thread → back to list, mirroring ?session=", async () => {
    const { container } = renderPane();
    expect(pane(container).dataset.screen).toBe("list");

    act(() => fireEvent.click(row(container, "s2")));
    expect(pane(container).dataset.screen).toBe("thread");
    expect(new URL(window.location.href).searchParams.get("session")).toBe("s2");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Recipes");
    await flush();
    await waitFor(() => expect(thread(container).textContent).toContain("Try minestrone."));
    // No server navigation for the selection.
    expect(routerPush).not.toHaveBeenCalled();

    act(() => fireEvent.click(screen.getByRole("button", { name: "Back to conversations" })));
    await waitFor(() => expect(pane(container).dataset.screen).toBe("list"));
  });

  it("opens a deep link straight on the thread", () => {
    const { container } = renderPane({ initialScreen: "thread" });
    expect(pane(container).dataset.screen).toBe("thread");
    expect(thread(container).textContent).toContain("Where to?");
  });

  it("sends with an optimistic row, then streams the reply", async () => {
    const turn = armTurn();
    const { container } = renderPane({ initialScreen: "thread" });
    await sendText("and the budget?");
    await flush();
    expect(turn.params.sessionId).toBe("s1");
    expect(thread(container).textContent).toContain("and the budget?");
    expect(screen.getByRole("button", { name: "Stop the agent" })).toBeTruthy();

    act(() => {
      turn.handlers.onAccepted?.("r1", "s1");
      turn.handlers.onDelta?.("About **500** euros");
    });
    await flush();
    const live = container.querySelector('[data-component="LiveReply"]');
    expect(live?.textContent).toContain("About 500 euros");
    // The list shows the turn as live.
    expect(row(container, "s1").querySelector('[data-component="LiveDot"]')).not.toBeNull();

    await act(async () => {
      turn.handlers.onCompleted?.("About **500** euros.", "s1");
      turn.finish("s1");
    });
    await flush();
    expect(container.querySelector('[data-component="LiveTurn"]')).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop the agent" })).toBeNull();
  });

  it("keeps a streaming turn alive while another conversation is open", async () => {
    const turn = armTurn();
    const { container } = renderPane({ initialScreen: "thread" });
    await sendText("long question");
    act(() => {
      turn.handlers.onAccepted?.("r1", "s1");
      turn.handlers.onDelta?.("first half");
    });
    await flush();

    // Switch to s2 mid-stream (back to the list, then the other row).
    act(() => fireEvent.click(screen.getByRole("button", { name: "Back to conversations" })));
    await waitFor(() => expect(pane(container).dataset.screen).toBe("list"));
    act(() => fireEvent.click(row(container, "s2")));
    await flush();
    await waitFor(() => expect(thread(container).textContent).toContain("Try minestrone."));
    expect(thread(container).textContent).not.toContain("first half");
    // s2 is idle: a fresh composer; s1 still shows live in the list.
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(row(container, "s1").querySelector('[data-component="LiveDot"]')).not.toBeNull();
    expect(vi.mocked(cancelChatTurn)).not.toHaveBeenCalled();

    // The background turn keeps streaming.
    act(() => turn.handlers.onDelta?.(", second half"));
    await flush();

    act(() => fireEvent.click(screen.getByRole("button", { name: "Back to conversations" })));
    await waitFor(() => expect(pane(container).dataset.screen).toBe("list"));
    act(() => fireEvent.click(row(container, "s1")));
    await flush();
    const live = container.querySelector('[data-component="LiveReply"]');
    expect(live?.textContent).toContain("first half, second half");
    expect(screen.getByRole("button", { name: "Stop the agent" })).toBeTruthy();

    await act(async () => {
      turn.handlers.onCompleted?.("first half, second half", "s1");
      turn.finish("s1");
    });
    await flush();
    expect(container.querySelector('[data-component="LiveTurn"]')).toBeNull();
  });

  it("loads every page before searching inside the conversation", async () => {
    const { container } = renderPane({ initialScreen: "thread", initialHasMore: true });
    act(() =>
      fireEvent.click(screen.getByRole("button", { name: "Search in conversation" })),
    );
    expect(container.querySelector('[data-component="InSessionSearch"]')).not.toBeNull();
    await flush();
    const pages = fetchMock.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.startsWith("/api/chat/messages"));
    expect(pages.length).toBeGreaterThan(0);
  });
});
