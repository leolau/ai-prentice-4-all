import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

// SessionTabs is "use client" but its initial render (no hooks that fire in
// SSR besides useState) is deterministic from props.
import { SessionTabs } from "@/components/chat/SessionTabs";
import type { SessionSummary } from "@/types";

const sessions: SessionSummary[] = [
  { id: "s1", source: "agent_home", title: "First chat", preview: null, message_count: 0, started_at: null, last_active: null, ended_at: null },
  { id: "s2", source: "agent_home", title: "Second chat", preview: null, message_count: 0, started_at: null, last_active: null, ended_at: null },
  { id: "s3", source: "agent_home", title: "Third chat", preview: null, message_count: 0, started_at: null, last_active: null, ended_at: null },
];

describe("SessionTabs", () => {
  it("renders the data-component root", () => {
    const html = renderToStaticMarkup(
      <SessionTabs
        sessions={sessions}
        activeId="s1"
        busyKeys={[]}
        onSelect={() => {}}
        onOpenDetails={() => {}}
        onNew={() => {}}
        onReorder={() => {}}
      />,
    );
    expect(html).toContain('data-component="SessionTabs"');
  });

  it("renders all session titles as chips", () => {
    const html = renderToStaticMarkup(
      <SessionTabs
        sessions={sessions}
        activeId="s1"
        busyKeys={[]}
        onSelect={() => {}}
        onOpenDetails={() => {}}
        onNew={() => {}}
        onReorder={() => {}}
      />,
    );
    expect(html).toContain("First chat");
    expect(html).toContain("Second chat");
    expect(html).toContain("Third chat");
  });

  it("does NOT render Archived or + New buttons (they moved to the header)", () => {
    const html = renderToStaticMarkup(
      <SessionTabs
        sessions={sessions}
        activeId="s1"
        busyKeys={[]}
        onSelect={() => {}}
        onOpenDetails={() => {}}
        onNew={() => {}}
        onReorder={() => {}}
      />,
    );
    expect(html).not.toContain("Archived");
    expect(html).not.toContain("+ New");
  });

  it("uses a full-width scroll container (overflow-x-auto on the root)", () => {
    const html = renderToStaticMarkup(
      <SessionTabs
        sessions={sessions}
        activeId="s1"
        busyKeys={[]}
        onSelect={() => {}}
        onOpenDetails={() => {}}
        onNew={() => {}}
        onReorder={() => {}}
      />,
    );
    expect(html).toContain("overflow-x-auto");
  });
});
