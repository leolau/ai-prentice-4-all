/**
 * The live-update stream's cursor contract (E3, §12 push edition): the
 * cursor is seeded from the first frame without refreshing, a moving head
 * reports a movement exactly once, and an unchanged head never does.
 * Behaviour tests against the pure fold; the hook is the thin
 * `useRowStream` + `router.refresh()` shell around it.
 */
import { describe, expect, it } from "vitest";

import { applyProjectEventsFrame } from "@/components/projects/useProjectEvents";
import type { StreamFrame } from "@/lib/chat/stream";

const update = (latest_event_id: number): StreamFrame => ({
  event: "update",
  data: { latest_event_id },
});

describe("applyProjectEventsFrame", () => {
  it("seeds the cursor from the first frame without reporting a movement", () => {
    const result = applyProjectEventsFrame(null, update(7));
    expect(result).toEqual({ seen: 7, moved: false });
  });

  it("reports a movement once when the head increases, and not again until it does", () => {
    let seen: number | null = 7;
    let result = applyProjectEventsFrame(seen, update(7));
    expect(result.moved).toBe(false);
    seen = result.seen;

    result = applyProjectEventsFrame(seen, update(9));
    expect(result).toEqual({ seen: 9, moved: true });
    seen = result.seen;

    result = applyProjectEventsFrame(seen, update(9));
    expect(result.moved).toBe(false);
  });

  it("ignores a non-update frame and a body with no numeric cursor", () => {
    expect(applyProjectEventsFrame(7, { event: "gone", data: {} })).toEqual({
      seen: 7,
      moved: false,
    });
    expect(
      applyProjectEventsFrame(7, { event: "update", data: { error: "boom" } }),
    ).toEqual({ seen: 7, moved: false });
  });
});
