// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatStreamHandlers } from "@/lib/chat/stream";

vi.mock("@/lib/chat/stream", () => ({
  streamChatTurn: vi.fn(),
}));

import { streamChatTurn } from "@/lib/chat/stream";
import { LeadChatHost } from "@/components/coral/LeadChatHost";

const openLeadChat = () =>
  fireEvent.click(screen.getByRole("button", { name: /open lead chat/i }));

beforeEach(() => {
  vi.mocked(streamChatTurn).mockReset();
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
  it("opens on tap with the lead-session header and a composer", () => {
    render(<LeadChatHost />);
    openLeadChat();
    const panel = screen.getByRole("dialog", { name: /lead chat/i });
    expect(panel.textContent).toContain("Lead chat");
    expect(panel.textContent).toContain(
      "The lead session starts with your first message",
    );
    expect(screen.getByPlaceholderText(/message your agent/i)).toBeTruthy();
  });

  it("closes on backdrop click", () => {
    render(<LeadChatHost />);
    openLeadChat();
    expect(screen.queryByRole("dialog")).toBeTruthy();
    fireEvent.click(document.querySelector(".coral-backdrop") as Element);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on Escape", () => {
    render(<LeadChatHost />);
    openLeadChat();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("pins the session id returned by the first turn", async () => {
    // Pinning the session re-runs the history-load effect; stub fetch so it
    // resolves to the just-finished exchange instead of failing and wiping
    // the rendered messages.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          messages: [{ role: "assistant", content: "Hi there" }],
        }),
      }),
    );
    const mocked = vi.mocked(streamChatTurn);
    mocked.mockImplementation(async (_params, handlers: ChatStreamHandlers) => {
      handlers.onDelta?.("Hi ");
      handlers.onCompleted?.("Hi there", "sess-42");
      return { sessionId: "sess-42" };
    });

    render(<LeadChatHost />);
    openLeadChat();
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/message your agent/i)).toBeTruthy();
    });
    fireEvent.input(screen.getByPlaceholderText(/message your agent/i), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Hi there");
    });
    expect(mocked).toHaveBeenCalledTimes(1);
    // First turn starts with no session; the returned id is pinned for reuse.
    expect(mocked.mock.calls[0][0].sessionId).toBeNull();
    expect(JSON.parse(localStorage.getItem("agent-home:lead-session") ?? "null")).toBe(
      "sess-42",
    );
  });

  it("loads the pinned session's history when reopened", async () => {
    localStorage.setItem("agent-home:lead-session", JSON.stringify("sess-42"));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        session_id: "sess-42",
        messages: [{ role: "assistant", content: "Earlier answer" }],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<LeadChatHost />);
    openLeadChat();

    await waitFor(() => {
      expect(screen.getByRole("dialog").textContent).toContain("Earlier answer");
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/messages?sessionId=sess-42",
    );
    vi.unstubAllGlobals();
  });

  it("reuses the pinned session on later turns", async () => {
    localStorage.setItem("agent-home:lead-session", JSON.stringify("sess-42"));
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ messages: [] }) }),
    );
    const mocked = vi.mocked(streamChatTurn);
    mocked.mockImplementation(async (_params, handlers: ChatStreamHandlers) => {
      handlers.onCompleted?.("Again", "sess-42");
      return { sessionId: "sess-42" };
    });

    render(<LeadChatHost />);
    openLeadChat();
    // Wait out the history-load effect before typing.
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/message your agent/i)).toBeTruthy();
    });
    fireEvent.input(screen.getByPlaceholderText(/message your agent/i), {
      target: { value: "More" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => {
      expect(mocked).toHaveBeenCalledTimes(1);
    });
    expect(mocked.mock.calls[0][0].sessionId).toBe("sess-42");
  });
});
