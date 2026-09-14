/**
 * The live card poller's contract — same shape as `useRunLive`'s, but for
 * one board-dispatched card. Only `running` has anything to poll for: a
 * worker is on it right now and its heartbeat note / comments can change
 * between reads; every other status is either not-yet-started or already
 * closed and won't move on its own.
 */
import { describe, expect, it, vi } from "vitest";

import {
  createCardPoller,
  isCardLive,
} from "@/components/projects/useCardLive";
import type { ProjectCardDetail } from "@/types";

const CARD = (status: string): ProjectCardDetail =>
  ({ id: "t_1", status }) as unknown as ProjectCardDetail;

const answer = (body: unknown, ok = true): Response =>
  ({ ok, json: async () => body }) as unknown as Response;

describe("isCardLive", () => {
  it("is true only while a worker is actually on the card", () => {
    expect(isCardLive("running")).toBe(true);
    expect(isCardLive("todo")).toBe(false);
    expect(isCardLive("triage")).toBe(false);
    expect(isCardLive("blocked")).toBe(false);
    expect(isCardLive("done")).toBe(false);
  });
});

describe("createCardPoller", () => {
  it("reads the card and hands the fresh row over", async () => {
    const fresh = CARD("running");
    const fetchImpl = vi.fn(async () => answer(fresh));
    const onCard = vi.fn();
    const poller = createCardPoller(
      "monday digest",
      "t_1",
      onCard,
      fetchImpl as unknown as typeof fetch,
    );

    expect(await poller.tick()).toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/projects/monday%20digest/cards/t_1",
    );
    expect(onCard).toHaveBeenCalledWith(fresh);
  });

  it("reports the card finished so the caller stops polling", async () => {
    const onCard = vi.fn();
    const poller = createCardPoller(
      "p",
      "t_1",
      onCard,
      (async () => answer(CARD("done"))) as unknown as typeof fetch,
    );
    expect(await poller.tick()).toBe(false);
    expect(onCard).toHaveBeenCalledWith(
      expect.objectContaining({ status: "done" }),
    );
  });

  it("swallows a non-2xx and a network failure, and keeps the tail alive", async () => {
    const onCard = vi.fn();
    const refused = createCardPoller(
      "p",
      "t_1",
      onCard,
      (async () => answer({ detail: "nope" }, false)) as unknown as typeof fetch,
    );
    expect(await refused.tick()).toBe(true);

    const broken = createCardPoller(
      "p",
      "t_1",
      onCard,
      (async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    );
    expect(await broken.tick()).toBe(true);
    expect(onCard).not.toHaveBeenCalled();
  });

  it("ignores a body that is not a card row", async () => {
    const onCard = vi.fn();
    const poller = createCardPoller(
      "p",
      "t_1",
      onCard,
      (async () => answer({ error: "invalid_request" })) as unknown as typeof fetch,
    );
    expect(await poller.tick()).toBe(true);
    expect(onCard).not.toHaveBeenCalled();
  });
});
