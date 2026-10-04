// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ConversationList,
  isUnread,
  relativeTime,
  sortNewestFirst,
} from "@/components/chat/ConversationList";
import { NEW_KEY } from "@/lib/chat/chat-controller";
import type { SessionSummary } from "@/types";

const NOW = new Date(2026, 5, 15, 12, 0, 0);
const sec = (d: Date) => Math.floor(d.getTime() / 1000);
const nowS = sec(NOW);

function s(id: string, over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id,
    source: "agent-home",
    title: `Title ${id}`,
    preview: `preview ${id}`,
    started_at: nowS - 3 * 86400,
    last_active: nowS - 3 * 86400,
    message_count: 2,
    archived: false,
    ...over,
  } as SessionSummary;
}

const sessions = [
  s("old", { last_active: nowS - 5 * 86400 }),
  s("cron", { source: "cron", title: "Nightly digest", last_active: nowS - 2 * 3600 }),
  s("fresh", { title: "Groceries", preview: "milk and eggs", last_active: nowS - 120 }),
];

const rowIds = (root: HTMLElement) =>
  [...root.querySelectorAll('[data-component="ConversationRow"]')].map(
    (el) => el.getAttribute("data-session-id") ?? "new",
  );

function renderList(over: Partial<Parameters<typeof ConversationList>[0]> = {}) {
  const onSelect = vi.fn();
  const onSelectNew = vi.fn();
  const utils = render(
    <ConversationList
      sessions={sessions}
      selectedId="old"
      busyKeys={[]}
      lastRead={{}}
      onSelect={onSelect}
      onSelectNew={onSelectNew}
      now={NOW}
      {...over}
    />,
  );
  return { onSelect, onSelectNew, ...utils };
}

afterEach(cleanup);

describe("ConversationList", () => {
  it("defaults to All and lists every conversation newest first", () => {
    const { container } = renderList();
    const all = screen.getByRole("button", { name: /^All/ });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(rowIds(container)).toEqual(["fresh", "cron", "old"]);
  });

  it("filters by category chip and falls back to All when it empties", () => {
    const { container, rerender } = renderList();
    fireEvent.click(screen.getByRole("button", { name: /^Scheduled/ }));
    expect(rowIds(container)).toEqual(["cron"]);
    rerender(
      <ConversationList
        sessions={sessions.filter((x) => x.id !== "cron")}
        selectedId="old"
        busyKeys={[]}
        lastRead={{}}
        onSelect={() => {}}
        onSelectNew={() => {}}
        now={NOW}
      />,
    );
    expect(rowIds(container)).toEqual(["fresh", "old"]);
    expect(screen.getByRole("button", { name: /^All/ }).getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("searches titles and previews, offering a message-text search", () => {
    const onSearchMessages = vi.fn();
    const { container } = renderList({ onSearchMessages });
    const box = screen.getByRole("searchbox", { name: "Search conversations" });
    fireEvent.change(box, { target: { value: "EGGS" } });
    expect(rowIds(container)).toEqual(["fresh"]);
    fireEvent.change(box, { target: { value: "digest" } });
    expect(rowIds(container)).toEqual(["cron"]);
    fireEvent.click(screen.getByRole("button", { name: /Search message text/ }));
    expect(onSearchMessages).toHaveBeenCalledWith("digest");
    fireEvent.change(box, { target: { value: "zzz" } });
    expect(screen.getByText("No conversations match.")).toBeTruthy();
  });

  it("marks the selected row, unread rows and live turns", () => {
    const { container, onSelect } = renderList({
      busyKeys: ["cron"],
      lastRead: { old: nowS, cron: nowS },
    });
    const row = (id: string) =>
      container.querySelector(`[data-session-id="${id}"]`) as HTMLElement;
    expect(row("old").getAttribute("aria-current")).toBe("true");
    expect(row("fresh").getAttribute("aria-current")).toBeNull();
    // Never read → unread; read after its last activity → not.
    expect(row("fresh").querySelector('[data-component="UnreadDot"]')).not.toBeNull();
    expect(row("old").querySelector('[data-component="UnreadDot"]')).toBeNull();
    expect(row("cron").querySelector('[data-component="LiveDot"]')).not.toBeNull();
    expect(within(row("fresh")).getByText("2m")).toBeTruthy();
    fireEvent.click(row("fresh"));
    expect(onSelect).toHaveBeenCalledWith("fresh");
  });

  it("shows the unsaved conversation while it is open or still running", () => {
    const { container, rerender, onSelectNew } = renderList({ selectedId: null });
    expect(rowIds(container)[0]).toBe("new");
    fireEvent.click(screen.getByRole("button", { name: /New conversation/ }));
    expect(onSelectNew).toHaveBeenCalled();
    rerender(
      <ConversationList
        sessions={sessions}
        selectedId="old"
        busyKeys={[]}
        lastRead={{}}
        onSelect={() => {}}
        onSelectNew={() => {}}
        now={NOW}
      />,
    );
    expect(rowIds(container)).not.toContain("new");
    rerender(
      <ConversationList
        sessions={sessions}
        selectedId="old"
        busyKeys={[NEW_KEY]}
        lastRead={{}}
        onSelect={() => {}}
        onSelectNew={() => {}}
        now={NOW}
      />,
    );
    expect(rowIds(container)[0]).toBe("new");
  });

  it("collapses the filters behind a toggle", () => {
    renderList({ filters: <div>tag filters</div>, header: <div>profile picker</div> });
    expect(screen.getByText("profile picker")).toBeTruthy();
    const toggle = screen.getByRole("button", { name: /Filters/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("tag filters")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("tag filters")).toBeTruthy();
  });
});

describe("ConversationList helpers", () => {
  it("orders by last activity, falling back to start, keeping ties stable", () => {
    const list = [
      s("a", { last_active: 10 }),
      s("b", { last_active: null as unknown as number, started_at: 30 }),
      s("c", { last_active: 10 }),
    ];
    expect(sortNewestFirst(list).map((x) => x.id)).toEqual(["b", "a", "c"]);
  });

  it("formats relative times", () => {
    expect(relativeTime(nowS - 5, NOW)).toBe("now");
    expect(relativeTime(nowS - 5 * 60, NOW)).toBe("5m");
    expect(relativeTime(nowS - 3 * 3600, NOW)).toBe("3h");
    expect(relativeTime(nowS - 2 * 86400, NOW)).toBe("2d");
    expect(relativeTime(null, NOW)).toBe("");
  });

  it("is unread only once markers are loaded and activity is newer", () => {
    const x = s("x", { last_active: 100 });
    expect(isUnread(x, null)).toBe(false);
    expect(isUnread(x, {})).toBe(true);
    expect(isUnread(x, { x: 100 })).toBe(false);
    expect(isUnread(x, { x: 99 })).toBe(true);
    expect(isUnread({ ...x, archived: true }, {})).toBe(false);
  });
});
