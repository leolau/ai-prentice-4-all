/**
 * The live run stream's contract (§12 live updates, push edition).
 *
 * These are the properties a person depends on while watching a run: only
 * an `update` frame with a real row updates the page, a `gone`/`end` frame
 * (or any frame once the run is terminal) is not treated as a fresh row,
 * and a run past its terminal status has nothing left to watch.
 */
import { describe, expect, it } from "vitest";

import { applyRunFrame, isRunLive } from "@/components/projects/useRunLive";
import type { StreamFrame } from "@/lib/chat/stream";
import type { ProjectRun } from "@/types";

const RUN = (status: string): ProjectRun =>
  ({ run_no: 3, status }) as unknown as ProjectRun;

describe("isRunLive", () => {
  it("treats only the closed statuses as finished", () => {
    expect(isRunLive("running")).toBe(true);
    expect(isRunLive("waiting")).toBe(true);
    expect(isRunLive("blocked")).toBe(true);
    expect(isRunLive("done")).toBe(false);
    expect(isRunLive("failed")).toBe(false);
    expect(isRunLive("cancelled")).toBe(false);
  });

  it("stays live past a terminal status while a card is still active", () => {
    // A terminal run row (e.g. auto-failed by the stale-run sweep) can
    // still have a card actively working — the stream must not close on
    // the row alone (see the backend's mirroring `_run_is_terminal`).
    expect(isRunLive("done", true)).toBe(true);
    expect(isRunLive("failed", true)).toBe(true);
    expect(isRunLive("cancelled", true)).toBe(true);
    expect(isRunLive("done", false)).toBe(false);
  });
});

describe("applyRunFrame", () => {
  it("reads the row out of an update frame", () => {
    const frame: StreamFrame = { event: "update", data: RUN("running") as unknown as Record<string, unknown> };
    expect(applyRunFrame(frame)).toEqual(RUN("running"));
  });

  it("ignores gone/end frames — they carry no row", () => {
    expect(applyRunFrame({ event: "gone", data: {} })).toBeNull();
    expect(applyRunFrame({ event: "end", data: {} })).toBeNull();
  });

  it("ignores a body that is not a run row", () => {
    expect(
      applyRunFrame({ event: "update", data: { error: "invalid_request" } }),
    ).toBeNull();
  });
});
