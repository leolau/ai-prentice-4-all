/**
 * The live-update poller contract (E3): the cursor is seeded from the first
 * response, a moving head refreshes exactly once, an unchanged head never
 * does, and every failure — network or non-2xx — is swallowed rather than
 * surfaced. Behaviour tests against the pure poller; the hook is the thin
 * `setInterval` + `router.refresh()` shell around it.
 */
import { describe, expect, it, vi } from "vitest";

import { createProjectEventsPoller } from "@/components/projects/useProjectEvents";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("createProjectEventsPoller", () => {
  it("seeds the cursor from the first answer without refreshing", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ latest_event_id: 7 }));
    const onMovement = vi.fn();
    const poller = createProjectEventsPoller("digest", onMovement, fetchImpl);

    await poller.tick();

    expect(onMovement).not.toHaveBeenCalled();
    // The next poll carries the seeded cursor — never since=0.
    await poller.tick();
    expect(fetchImpl).toHaveBeenLastCalledWith(
      "/api/projects/digest/events?since=7",
    );
    expect(onMovement).not.toHaveBeenCalled();
  });

  it("refreshes once when the head moves, and not again until it moves", async () => {
    const answers = [7, 7, 9, 9];
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ latest_event_id: answers.shift() }),
    );
    const onMovement = vi.fn();
    const poller = createProjectEventsPoller("digest", onMovement, fetchImpl);

    await poller.tick(); // seed at 7
    await poller.tick(); // still 7
    expect(onMovement).not.toHaveBeenCalled();
    await poller.tick(); // moved to 9
    expect(onMovement).toHaveBeenCalledTimes(1);
    await poller.tick(); // still 9
    expect(onMovement).toHaveBeenCalledTimes(1);
  });

  it("swallows a failed poll instead of surfacing it", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("network down");
    });
    const onMovement = vi.fn();
    const poller = createProjectEventsPoller("digest", onMovement, fetchImpl);

    await expect(poller.tick()).resolves.toBeUndefined();
    expect(onMovement).not.toHaveBeenCalled();
  });

  it("swallows a non-2xx answer — a poll error is never rendered", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "boom" }, 502),
    );
    const onMovement = vi.fn();
    const poller = createProjectEventsPoller("digest", onMovement, fetchImpl);

    await poller.tick();
    await poller.tick();
    expect(onMovement).not.toHaveBeenCalled();
  });
});
