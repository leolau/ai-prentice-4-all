/**
 * The live card stream's contract — same shape as `useRunLive`'s, but for
 * one board-dispatched card. Only `running` has anything to watch: a
 * worker is on it right now and its heartbeat note / comments can change
 * between frames; every other status is either not-yet-started or already
 * closed and won't move on its own.
 */
import { describe, expect, it } from "vitest";

import { applyCardFrame, isCardLive } from "@/components/projects/useCardLive";
import type { StreamFrame } from "@/lib/chat/stream";
import type { ProjectCardDetail } from "@/types";

const CARD = (status: string): ProjectCardDetail =>
  ({ id: "t_1", status }) as unknown as ProjectCardDetail;

describe("isCardLive", () => {
  it("is true only while a worker is actually on the card", () => {
    expect(isCardLive("running")).toBe(true);
    expect(isCardLive("todo")).toBe(false);
    expect(isCardLive("triage")).toBe(false);
    expect(isCardLive("blocked")).toBe(false);
    expect(isCardLive("done")).toBe(false);
  });
});

describe("applyCardFrame", () => {
  it("reads the row out of an update frame", () => {
    const frame: StreamFrame = {
      event: "update",
      data: CARD("running") as unknown as Record<string, unknown>,
    };
    expect(applyCardFrame(frame)).toEqual(CARD("running"));
  });

  it("ignores gone/end frames — they carry no row", () => {
    expect(applyCardFrame({ event: "gone", data: {} })).toBeNull();
    expect(applyCardFrame({ event: "end", data: {} })).toBeNull();
  });

  it("ignores a body that is not a card row", () => {
    expect(
      applyCardFrame({ event: "update", data: { error: "invalid_request" } }),
    ).toBeNull();
  });
});
