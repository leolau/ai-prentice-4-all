// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import {
  countUnreadSessions,
  markSessionRead,
  readLastReadMap,
} from "@/lib/chat/last-read";

afterEach(() => localStorage.clear());

describe("last-read markers", () => {
  it("starts empty and survives a full write/read round-trip", () => {
    expect(readLastReadMap()).toEqual({});
    markSessionRead("s1");
    const map = readLastReadMap();
    expect(typeof map.s1).toBe("number");
    // The marker is "now" in unix seconds, not milliseconds.
    expect(map.s1).toBeGreaterThan(Date.now() / 1000 - 5);
    expect(map.s1).toBeLessThan(Date.now() / 1000 + 5);
  });

  it("ignores empty session ids", () => {
    markSessionRead("");
    expect(readLastReadMap()).toEqual({});
  });
});

describe("countUnreadSessions", () => {
  const now = Math.floor(Date.now() / 1000);

  it("counts sessions with activity newer than the last read", () => {
    const sessions = [
      { id: "read", last_active: now - 100, archived: false },
      { id: "new", last_active: now + 10, archived: false },
      { id: "never-read", last_active: now - 5, archived: false },
    ];
    expect(countUnreadSessions(sessions, { read: now })).toBe(2);
  });

  it("skips archived sessions and null activity", () => {
    const sessions = [
      { id: "a", last_active: now + 10, archived: true },
      { id: "b", last_active: null, archived: false },
    ];
    expect(countUnreadSessions(sessions, {})).toBe(0);
  });

  it("a read marker later than the activity clears the unread", () => {
    const sessions = [{ id: "s", last_active: now - 10, archived: false }];
    expect(countUnreadSessions(sessions, { s: now })).toBe(0);
  });
});
