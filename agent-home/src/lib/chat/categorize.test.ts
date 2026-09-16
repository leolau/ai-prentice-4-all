/**
 * Behaviour of chat session categorization: a session lands in exactly one
 * of "kanban" / "daily" / "scheduled" / "others", by priority order —
 * a kanban-worker session that also happens to have run today stays
 * "kanban", not "daily"; a cron session from today stays "scheduled".
 */
import { describe, expect, it } from "vitest";

import {
  categorizeSession,
  groupSessionsByCategory,
} from "@/lib/chat/categorize";
import type { SessionSummary } from "@/types";

const NOW = new Date("2026-09-16T12:00:00Z");

function session(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "s1",
    source: "cli",
    title: null,
    preview: null,
    message_count: 1,
    started_at: Math.floor(NOW.getTime() / 1000),
    last_active: null,
    ended_at: null,
    cwd: null,
    ...overrides,
  };
}

describe("categorizeSession", () => {
  it("classifies a kanban-worker cwd as kanban, default board", () => {
    const s = session({
      cwd: "/opt/data/hermes-home-staging/kanban/workspaces/t_5bd01798",
    });
    expect(categorizeSession(s, NOW)).toBe("kanban");
  });

  it("classifies a kanban-worker cwd as kanban, named board", () => {
    const s = session({
      cwd: "/opt/data/hermes-home/kanban/boards/ops/workspaces/t_abc",
    });
    expect(categorizeSession(s, NOW)).toBe("kanban");
  });

  it("classifies a cron-sourced session as scheduled", () => {
    const s = session({ source: "cron", cwd: "/home/hermes" });
    expect(categorizeSession(s, NOW)).toBe("scheduled");
  });

  it("classifies a same-day non-kanban, non-cron session as daily", () => {
    const s = session({ source: "cli", cwd: "/home/hermes" });
    expect(categorizeSession(s, NOW)).toBe("daily");
  });

  it("classifies an older session as others", () => {
    const s = session({
      source: "cli",
      cwd: "/home/hermes",
      started_at: Math.floor(NOW.getTime() / 1000) - 2 * 86400,
    });
    expect(categorizeSession(s, NOW)).toBe("others");
  });

  it("kanban wins over cron when (hypothetically) both signals are present", () => {
    const s = session({
      source: "cron",
      cwd: "/opt/data/hermes-home/kanban/workspaces/t_x",
    });
    expect(categorizeSession(s, NOW)).toBe("kanban");
  });

  it("cron wins over same-day when both apply", () => {
    const s = session({ source: "cron" });
    expect(categorizeSession(s, NOW)).toBe("scheduled");
  });

  it("treats a null started_at as not-today (falls to others)", () => {
    const s = session({ source: "cli", cwd: "/home/hermes", started_at: null });
    expect(categorizeSession(s, NOW)).toBe("others");
  });

  it("respects local calendar day boundaries, not a 24h window", () => {
    // All constructed with the local-time Date(y, m, d, h, m) form (no "Z"),
    // so the comparison is deterministic regardless of the test machine's
    // timezone — the point under test is the calendar-day boundary itself,
    // not any particular UTC offset.
    const justAfterMidnight = new Date(2026, 8, 16, 0, 30, 0); // Sep 16, local
    const sixHoursEarlierSameDay = new Date(2026, 8, 15, 18, 30, 0); // Sep 15, 18:30 local
    const s = session({
      source: "cli",
      cwd: "/home/hermes",
      started_at: Math.floor(sixHoursEarlierSameDay.getTime() / 1000),
    });
    // 6 hours before "just after midnight" on the 16th is still the 15th —
    // a different calendar day, so NOT "daily" relative to `justAfterMidnight`.
    expect(categorizeSession(s, justAfterMidnight)).toBe("others");

    const sameCalendarDay = new Date(2026, 8, 16, 0, 5, 0); // Sep 16, 00:05 local
    const s2 = session({
      source: "cli",
      cwd: "/home/hermes",
      started_at: Math.floor(sameCalendarDay.getTime() / 1000),
    });
    expect(categorizeSession(s2, justAfterMidnight)).toBe("daily");
  });
});

describe("groupSessionsByCategory", () => {
  it("buckets sessions into all four groups, preserving relative order", () => {
    const kanban1 = session({
      id: "k1",
      cwd: "/x/kanban/workspaces/t_1",
    });
    const cron1 = session({ id: "c1", source: "cron" });
    const today1 = session({ id: "d1" });
    const older1 = session({
      id: "o1",
      started_at: Math.floor(NOW.getTime() / 1000) - 30 * 86400,
    });
    const today2 = session({ id: "d2" });

    const groups = groupSessionsByCategory(
      [kanban1, cron1, today1, older1, today2],
      NOW,
    );
    expect(groups.kanban.map((s) => s.id)).toEqual(["k1"]);
    expect(groups.scheduled.map((s) => s.id)).toEqual(["c1"]);
    expect(groups.daily.map((s) => s.id)).toEqual(["d1", "d2"]);
    expect(groups.others.map((s) => s.id)).toEqual(["o1"]);
  });

  it("returns empty arrays for categories with no matches", () => {
    const groups = groupSessionsByCategory([]);
    expect(groups).toEqual({ kanban: [], daily: [], scheduled: [], others: [] });
  });
});
