import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

// SessionTabs is "use client" but its initial render (no hooks that fire in
// SSR besides useState/useMemo) is deterministic from props.
import { SessionTabs } from "@/components/chat/SessionTabs";
import type { SessionSummary } from "@/types";

const NOW_SECONDS = Math.floor(Date.now() / 1000);

const sessions: SessionSummary[] = [
  {
    id: "s1",
    source: "agent_home",
    title: "First chat",
    preview: null,
    message_count: 0,
    started_at: NOW_SECONDS,
    last_active: null,
    ended_at: null,
  },
  {
    id: "s2",
    source: "agent_home",
    title: "Second chat",
    preview: null,
    message_count: 0,
    started_at: NOW_SECONDS,
    last_active: null,
    ended_at: null,
  },
  {
    id: "s3",
    source: "cron",
    title: "Third chat",
    preview: null,
    message_count: 0,
    started_at: NOW_SECONDS,
    last_active: null,
    ended_at: null,
  },
  {
    id: "s4",
    source: "cli",
    cwd: "/opt/data/hermes-home/kanban/workspaces/t_abc",
    title: "Fourth chat",
    preview: null,
    message_count: 0,
    started_at: NOW_SECONDS,
    last_active: null,
    ended_at: null,
  },
];

const baseProps = {
  sessions,
  activeId: "s1",
  busyKeys: [],
  onSelect: () => {},
  onOpenDetails: () => {},
  onNew: () => {},
  onReorder: () => {},
};

describe("SessionTabs", () => {
  it("renders the data-component root", () => {
    const html = renderToStaticMarkup(<SessionTabs {...baseProps} />);
    expect(html).toContain('data-component="SessionTabs"');
  });

  it("renders all session titles as chips", () => {
    const html = renderToStaticMarkup(<SessionTabs {...baseProps} />);
    expect(html).toContain("First chat");
    expect(html).toContain("Second chat");
    expect(html).toContain("Third chat");
    expect(html).toContain("Fourth chat");
  });

  it("groups sessions into labelled category rows", () => {
    const html = renderToStaticMarkup(<SessionTabs {...baseProps} />);
    // s4 (kanban cwd) -> Kanban row; s3 (cron) -> Scheduled row; s1/s2 -> Daily row.
    expect(html).toContain('data-category="kanban"');
    expect(html).toContain('data-category="scheduled"');
    expect(html).toContain('data-category="daily"');
    // No "others" row, since no session in this set is old/non-kanban/non-cron.
    expect(html).not.toContain('data-category="others"');
    expect(html).toContain("Kanban");
    expect(html).toContain("Scheduled");
    expect(html).toContain("Daily");
  });

  it("does NOT render Archived or + New buttons (they moved to the header)", () => {
    const html = renderToStaticMarkup(<SessionTabs {...baseProps} />);
    expect(html).not.toContain("Archived");
    expect(html).not.toContain("+ New");
  });

  it("makes each category row independently horizontally scrollable", () => {
    const html = renderToStaticMarkup(<SessionTabs {...baseProps} />);
    expect(html).toContain("overflow-x-auto");
  });

  it("omits empty categories entirely", () => {
    const onlyDaily: SessionSummary[] = [sessions[0]];
    const html = renderToStaticMarkup(
      <SessionTabs {...baseProps} sessions={onlyDaily} />,
    );
    expect(html).toContain('data-category="daily"');
    expect(html).not.toContain('data-category="kanban"');
    expect(html).not.toContain('data-category="scheduled"');
    expect(html).not.toContain('data-category="others"');
  });
});
