// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
  attachChatStream: vi.fn(),
  cancelChatTurn: vi.fn(),
}));
// Link's useLinkStatus only works inside the App Router's link context.
vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next/link")>();
  return {
    ...actual,
    useLinkStatus: () => ({ pending: false }),
  };
});

import { attachChatStream, cancelChatTurn, streamChatTurn } from "@/lib/chat/stream";
import { LeadChatHost } from "@/components/coral/LeadChatHost";

// jsdom has no PointerEvent constructor; RTL builds one for pointer* events.
class FakePointerEvent extends MouseEvent {
  pointerType = "mouse";
}
(globalThis as Record<string, unknown>).PointerEvent ??= FakePointerEvent;

const openLeadChat = () =>
  fireEvent.click(screen.getByRole("button", { name: /open lead chat/i }));

const panel = () => document.querySelector('[data-component="LeadChatPanel"]') as HTMLElement;

async function sendMessage(text: string) {
  fireEvent.input(screen.getByPlaceholderText(/message your agent/i), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
}

/** Wait until the lead session resolved and the first page landed. */
async function waitForReady() {
  await waitFor(() => {
    expect(screen.getByRole("dialog").textContent).toContain(
      "The lead session starts with your first message",
    );
  });
}

type Body = unknown | ((url: string) => unknown);

/**
 * A fetch stub answering the panel's three reads: which conversation it is,
 * its history, and whether a turn is already in flight. `overrides` replaces
 * the body (or a body function of the URL) for a path prefix.
 */
function leadFetch(overrides: Record<string, Body> = {}) {
  const bodies: Record<string, Body> = {
    "/api/chat/lead": { sessionId: "lead-abc" },
    "/api/chat/messages": { messages: [] },
    "/api/chat/active": { runId: null },
    ...overrides,
  };
  const fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(bodies).find((k) => url.startsWith(k));
    const body = key ? bodies[key] : {};
    return {
      ok: true,
      json: async () => (typeof body === "function" ? body(url) : body),
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const messageCalls = (fetchMock: ReturnType<typeof leadFetch>) =>
  fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/chat/messages"));

/** A narrow viewport: `(min-width: 768px)` does not match. */
function stubPhone() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  );
}

beforeEach(() => {
  vi.mocked(streamChatTurn).mockReset();
  vi.mocked(attachChatStream).mockReset();
  vi.mocked(attachChatStream).mockResolvedValue(undefined);
  vi.mocked(cancelChatTurn).mockReset();
  vi.mocked(cancelChatTurn).mockResolvedValue(undefined);
  leadFetch();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LeadChatHost (SSR)", () => {
  it("renders the FAB closed — no panel in server markup", () => {
    const html = renderToStaticMarkup(<LeadChatHost />);
    expect(html).toContain('data-component="LeadChatHost"');
    expect(html).toContain("Open lead chat");
    expect(html).not.toContain('role="dialog"');
  });
});

describe("LeadChatHost panel", () => {
  it("opens on tap with the lead-session header and a composer", async () => {
    render(<LeadChatHost />);
    openLeadChat();
    const dialog = screen.getByRole("dialog", { name: /lead chat/i });
    expect(dialog.textContent).toContain("Lead chat");
    expect(screen.getByPlaceholderText(/message your agent/i)).toBeTruthy();
    await waitForReady();
  });

  it("offers the lead session in the Chats page", async () => {
    render(<LeadChatHost />);
    openLeadChat();
    await waitFor(() => {
      const link = screen.getByRole("link", { name: /open in chats/i });
      expect(link.getAttribute("href")).toBe("/chat?session=lead-abc");
    });
  });

  it("minimises on the Minimise button", async () => {
    render(<LeadChatHost />);
    openLeadChat();
    expect(screen.queryByRole("dialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^minimise$/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => {
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: /open lead chat/i }),
      );
    });
  });

  it("closes on Escape", () => {
    render(<LeadChatHost />);
    openLeadChat();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("hides the floating button while the panel is open", () => {
    render(<LeadChatHost />);
    openLeadChat();
    expect(screen.getByRole("dialog", { name: /lead chat/i })).toBeTruthy();
    // The FAB is hidden while open — role queries skip hidden elements.
    expect(screen.queryByRole("button", { name: /open lead chat/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^minimise$/i }));
    expect(screen.getByRole("button", { name: /open lead chat/i })).toBeTruthy();
  });

  it("loads the server's lead session, and its first visible page, on open", async () => {
    const fetchMock = leadFetch({
      "/api/chat/messages": {
        messages: [{ id: 1, role: "assistant", content: "Earlier answer" }],
      },
    });

    render(<LeadChatHost />);
    openLeadChat();

    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Earlier answer");
    });
    const urls = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(urls).toContain("/api/chat/lead");
    const page = urls.find((u) => u.startsWith("/api/chat/messages"));
    expect(page).toContain("sessionId=lead-abc");
    expect(page).toContain("visible=1");
    expect(page).toContain("limit=40");
  });

  it("minimise keeps the transcript mounted — reopening does not refetch it", async () => {
    const fetchMock = leadFetch({
      "/api/chat/messages": {
        messages: [{ id: 1, role: "assistant", content: "Earlier answer" }],
      },
    });
    render(<LeadChatHost />);
    openLeadChat();
    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Earlier answer");
    });
    fireEvent.input(screen.getByPlaceholderText(/message your agent/i), {
      target: { value: "half-typed draft" },
    });

    fireEvent.click(screen.getByRole("button", { name: /^minimise$/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // Hidden, not unmounted.
    expect(panel().getAttribute("data-state")).toBe("closed");
    expect(panel().hasAttribute("inert")).toBe(true);
    expect(panel().textContent).toContain("Earlier answer");

    openLeadChat();
    expect(screen.getByRole("dialog").textContent).toContain("Earlier answer");
    expect(
      (screen.getByPlaceholderText(/message your agent/i) as HTMLTextAreaElement).value,
    ).toBe("half-typed draft");
    // Let the reopen's active-run probe settle; still one transcript read.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/chat/active"))
          .length,
      ).toBeGreaterThanOrEqual(2);
    });
    expect(messageCalls(fetchMock)).toHaveLength(1);
    expect(fetchMock.mock.calls.filter((c) => c[0] === "/api/chat/lead")).toHaveLength(1);
  });

  it("shows an unread dot on the FAB when a reply lands while minimised", async () => {
    const history: { id: number; role: string; content: string }[] = [];
    leadFetch({ "/api/chat/messages": () => ({ messages: [...history] }) });
    let finish: (() => void) | null = null;
    vi.mocked(streamChatTurn).mockImplementation(
      (params, handlers: ChatStreamHandlers) =>
        new Promise((resolve) => {
          handlers.onAccepted?.("run-1", "lead-abc");
          finish = () => {
            history.push({ id: 1, role: "user", content: params.message });
            history.push({ id: 2, role: "assistant", content: "Background answer" });
            handlers.onCompleted?.("Background answer", "lead-abc");
            resolve({ sessionId: "lead-abc" });
          };
        }),
    );

    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Long task");
    await waitFor(() => expect(finish).not.toBeNull());

    fireEvent.click(screen.getByRole("button", { name: /^minimise$/i }));
    const fab = screen.getByRole("button", { name: /open lead chat/i });
    expect(fab.getAttribute("aria-label")).not.toMatch(/new reply/i);
    expect(document.querySelector('[data-component="LeadChatUnread"]')).toBeNull();

    await act(async () => finish!());
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /open lead chat — new reply/i }),
      ).toBeTruthy();
    });
    expect(document.querySelector('[data-component="LeadChatUnread"]')).toBeTruthy();

    openLeadChat();
    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Background answer");
    });
    fireEvent.click(screen.getByRole("button", { name: /^minimise$/i }));
    expect(screen.getByRole("button", { name: /open lead chat/i }).getAttribute("aria-label")).toBe(
      "Open lead chat",
    );
    expect(document.querySelector('[data-component="LeadChatUnread"]')).toBeNull();
  });

  it("sends every turn on the server's lead session, never a fresh one", async () => {
    const history: { id: number; role: string; content: string }[] = [];
    leadFetch({ "/api/chat/messages": () => ({ messages: [...history] }) });
    const mocked = vi.mocked(streamChatTurn);
    mocked.mockImplementation(async (params, handlers: ChatStreamHandlers) => {
      history.push({ id: history.length + 1, role: "user", content: params.message });
      history.push({ id: history.length + 1, role: "assistant", content: "Hi there" });
      // A compacted conversation answers under a continuation id; adopting
      // it here would fork this browser off the shared lead session.
      handlers.onCompleted?.("Hi there", "resume-99");
      return { sessionId: "resume-99" };
    });

    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Hello");
    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Hi there");
    });
    expect(mocked.mock.calls[0][0].sessionId).toBe("lead-abc");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^send$/i })).toBeTruthy();
    });

    await sendMessage("More");
    await waitFor(() => {
      expect(mocked).toHaveBeenCalledTimes(2);
    });
    expect(mocked.mock.calls[1][0].sessionId).toBe("lead-abc");
    // The link keeps pointing at the root id, not the continuation.
    expect(
      screen.getByRole("link", { name: /open in chats/i }).getAttribute("href"),
    ).toBe("/chat?session=lead-abc");
  });

  it("re-attaches to a turn another device left running", async () => {
    leadFetch({ "/api/chat/active": { runId: "run-7" } });
    vi.mocked(attachChatStream).mockImplementation(
      (_params, handlers) =>
        new Promise(() => {
          handlers.onDelta?.("…still going");
        }),
    );

    render(<LeadChatHost />);
    openLeadChat();

    await waitFor(() => {
      expect(vi.mocked(attachChatStream)).toHaveBeenCalled();
    });
    expect(vi.mocked(attachChatStream).mock.calls[0][0]).toMatchObject({
      sessionId: "lead-abc",
      runId: "run-7",
    });
    expect(vi.mocked(attachChatStream).mock.calls[0][0].signal).toBeInstanceOf(
      AbortSignal,
    );
    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("…still going");
    });
  });

  it("shows the shared phase indicator: sending until the server ack, then thinking", async () => {
    let handlers: ChatStreamHandlers | null = null;
    vi.mocked(streamChatTurn).mockImplementation(
      (_params, h) =>
        new Promise(() => {
          handlers = h;
        }),
    );

    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Long task");

    const status = () => screen.getByRole("status");
    await waitFor(() => {
      expect(status().getAttribute("data-activity")).toBe("sending");
    });
    act(() => handlers!.onAccepted?.("run-1", "lead-abc"));
    await waitFor(() => {
      expect(status().getAttribute("data-activity")).toBe("thinking");
    });
    act(() => handlers!.onToolStart?.({ id: "t1", name: "web_search" }));
    await waitFor(() => {
      expect(status().getAttribute("data-activity")).toBe("tool");
      expect(status().textContent).toContain("web_search");
    });
    expect(screen.getByRole("button", { name: /stop the agent/i })).toBeTruthy();
  });

  it("renders an approval inline, never as a modal", async () => {
    let handlers: ChatStreamHandlers | null = null;
    vi.mocked(streamChatTurn).mockImplementation(
      (_params, h) =>
        new Promise(() => {
          handlers = h;
        }),
    );
    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Delete it");
    await waitFor(() => expect(handlers).not.toBeNull());
    act(() => {
      handlers!.onAccepted?.("run-1", "lead-abc");
      handlers!.onApproval?.({
        runId: "run-1",
        command: "rm -rf /tmp/x",
        choices: ["once", "deny"],
      });
    });
    await waitFor(() => {
      expect(panel().querySelector('[data-component="ApprovalCard"]')).toBeTruthy();
    });
    // The only dialog is the panel itself.
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("Stop asks the server to cancel the run instead of only closing the stream", async () => {
    let handlers: ChatStreamHandlers | null = null;
    let receivedSignal: AbortSignal | undefined;
    vi.mocked(streamChatTurn).mockImplementation(
      (params, h) =>
        new Promise(() => {
          handlers = h;
          receivedSignal = params.signal;
        }),
    );

    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Long task");
    await waitFor(() => expect(handlers).not.toBeNull());
    act(() => handlers!.onAccepted?.("run-1", "lead-abc"));
    await waitFor(() => {
      expect(screen.getByRole("status").getAttribute("data-activity")).toBe("thinking");
    });

    fireEvent.click(screen.getByRole("button", { name: /stop the agent/i }));
    await waitFor(() => {
      expect(vi.mocked(cancelChatTurn)).toHaveBeenCalledWith(
        expect.objectContaining({ sessionId: "lead-abc", runId: "run-1" }),
      );
    });
    // The stream stays open so the partial reply still lands.
    expect(receivedSignal?.aborted).toBe(false);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /stopping the agent/i })).toBeTruthy();
    });
    act(() => handlers!.onCancelled?.());
    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Stopped");
    });
  });

  it("Stop before the server ack aborts the request", async () => {
    let receivedSignal: AbortSignal | undefined;
    vi.mocked(streamChatTurn).mockImplementation(
      (params) =>
        new Promise((_resolve, reject) => {
          receivedSignal = params.signal;
          params.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );

    render(<LeadChatHost />);
    openLeadChat();
    await waitForReady();
    await sendMessage("Long task");
    await waitFor(() => expect(receivedSignal).toBeDefined());
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /stop the agent/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /stop the agent/i }));
    expect(receivedSignal?.aborted).toBe(true);
    expect(vi.mocked(cancelChatTurn)).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^send$/i })).toBeTruthy();
    });
    // The user row stays.
    expect(screen.getByRole("dialog").textContent).toContain("Long task");
  });

  it("refuses to send rather than fork a private conversation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }),
    );
    const mocked = vi.mocked(streamChatTurn);

    render(<LeadChatHost />);
    openLeadChat();
    await sendMessage("Hello");

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("could not be reached");
    });
    expect(mocked).not.toHaveBeenCalled();
    // The draft is given back.
    expect(
      (screen.getByPlaceholderText(/message your agent/i) as HTMLTextAreaElement).value,
    ).toBe("Hello");
  });
});

describe("LeadChatHost move & resize (desktop)", () => {
  it("drags on the DOM and persists the new position only on release", async () => {
    render(<LeadChatHost />);
    openLeadChat();
    const grip = document.querySelector(".leadchat-drag") as Element;
    fireEvent.pointerDown(grip, { clientX: 100, clientY: 400, button: 0 });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 500 });
    await waitFor(() => {
      expect(panel().style.transform).toBe("translate(40px, 100px)");
    });
    expect(localStorage.getItem("agent-home:leadchat-rect")).toBeNull();
    fireEvent.pointerUp(window, { clientX: 140, clientY: 500 });

    const stored = JSON.parse(
      localStorage.getItem("agent-home:leadchat-rect") ?? "null",
    ) as { x: number; y: number; w: number; h: number } | null;
    expect(stored).toBeTruthy();
    expect(typeof stored?.x).toBe("number");
    expect(typeof stored?.y).toBe("number");
    expect(stored?.w).toBeGreaterThanOrEqual(260);
    expect(stored?.h).toBeGreaterThanOrEqual(240);
    expect(panel().style.transform).toBe("");
    expect(panel().style.left).toBe(`${stored?.x}px`);
  });

  it("persists a new size after dragging the corner grip", () => {
    render(<LeadChatHost />);
    openLeadChat();
    const handle = document.querySelector(".leadchat-resize") as Element;
    fireEvent.pointerDown(handle, { clientX: 300, clientY: 400, button: 0 });
    fireEvent.pointerMove(window, { clientX: 400, clientY: 480 });
    fireEvent.pointerUp(window, { clientX: 400, clientY: 480 });

    const stored = JSON.parse(
      localStorage.getItem("agent-home:leadchat-rect") ?? "null",
    ) as { w: number; h: number } | null;
    expect(stored).toBeTruthy();
    expect(stored?.w).toBeGreaterThanOrEqual(260);
    expect(stored?.h).toBeGreaterThanOrEqual(240);
  });

  it("persists a new size after dragging the upper-left grip", () => {
    // Seed a known box so the math is deterministic: dragging the top-left
    // corner outward 30x20 grows the panel while anchoring bottom-right.
    localStorage.setItem(
      "agent-home:leadchat-rect",
      JSON.stringify({ x: 100, y: 100, w: 300, h: 300 }),
    );
    render(<LeadChatHost />);
    openLeadChat();
    const handle = document.querySelector(".leadchat-resize-tl") as Element;
    fireEvent.pointerDown(handle, { clientX: 100, clientY: 100, button: 0 });
    fireEvent.pointerMove(window, { clientX: 70, clientY: 80 });
    fireEvent.pointerUp(window, { clientX: 70, clientY: 80 });

    const stored = JSON.parse(
      localStorage.getItem("agent-home:leadchat-rect") ?? "null",
    );
    expect(stored).toEqual({ x: 70, y: 80, w: 330, h: 320 });
  });

  it("restores the persisted rect on the next open", () => {
    localStorage.setItem(
      "agent-home:leadchat-rect",
      JSON.stringify({ x: 33, y: 77, w: 300, h: 330 }),
    );
    render(<LeadChatHost />);
    openLeadChat();
    const dialog = screen.getByRole("dialog", { name: /lead chat/i });
    expect(dialog.style.left).toBe("33px");
    expect(dialog.style.top).toBe("77px");
    expect(dialog.style.width).toBe("300px");
    expect(dialog.style.height).toBe("330px");
  });

  it("pulls a saved box back inside a smaller viewport", async () => {
    localStorage.setItem(
      "agent-home:leadchat-rect",
      JSON.stringify({ x: 900, y: 100, w: 300, h: 330 }),
    );
    render(<LeadChatHost />);
    openLeadChat();
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem("agent-home:leadchat-rect") ?? "null");
      expect(stored.x + stored.w).toBeLessThanOrEqual(window.innerWidth - 8);
    });
  });
});

describe("LeadChatHost phone sheet", () => {
  it("renders a bottom sheet with a snap handle and no corner grips", async () => {
    stubPhone();
    localStorage.setItem(
      "agent-home:leadchat-rect",
      JSON.stringify({ x: 33, y: 77, w: 300, h: 330 }),
    );
    render(<LeadChatHost />);
    openLeadChat();
    const sheet = screen.getByRole("dialog", { name: /lead chat/i });
    expect(sheet.getAttribute("data-layout")).toBe("sheet");
    expect(sheet.classList.contains("leadchat-sheet")).toBe(true);
    expect(sheet.getAttribute("data-snap")).toBe("half");
    // No free drag/resize, and the desktop box is not applied.
    expect(document.querySelector(".leadchat-resize")).toBeNull();
    expect(document.querySelector(".leadchat-resize-tl")).toBeNull();
    expect(document.querySelector(".leadchat-drag")).toBeNull();
    expect(sheet.style.left).toBe("");

    fireEvent.click(screen.getByRole("button", { name: /expand lead chat/i }));
    expect(sheet.getAttribute("data-snap")).toBe("full");
    fireEvent.click(screen.getByRole("button", { name: /shrink lead chat/i }));
    expect(sheet.getAttribute("data-snap")).toBe("half");

    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitForReadyHidden();
  });

  it("snaps to full height when the handle is dragged up, and closes when dragged down", () => {
    stubPhone();
    render(<LeadChatHost />);
    openLeadChat();
    const sheet = screen.getByRole("dialog", { name: /lead chat/i });
    const handle = screen.getByRole("button", { name: /expand lead chat/i });

    fireEvent.pointerDown(handle, { clientX: 100, clientY: 400, button: 0 });
    fireEvent.pointerMove(window, { clientX: 100, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 100, clientY: 100 });
    expect(sheet.getAttribute("data-snap")).toBe("full");
    expect(sheet.style.height).toBe("");

    fireEvent.pointerDown(handle, { clientX: 100, clientY: 100, button: 0 });
    fireEvent.pointerMove(window, { clientX: 100, clientY: 700 });
    fireEvent.pointerUp(window, { clientX: 100, clientY: 700 });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

/** The hidden sheet still holds the resolved conversation. */
async function waitForReadyHidden() {
  await waitFor(() => {
    expect(panel().textContent).toContain(
      "The lead session starts with your first message",
    );
  });
}
