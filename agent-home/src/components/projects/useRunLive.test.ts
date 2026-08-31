/**
 * The live run poller's contract (§12 live updates, run page edition).
 *
 * These are the properties a person depends on while watching a run: it
 * re-reads the row, it stops the moment the run is over, and a failing poll
 * never becomes an error on the screen or a stopped tail.
 */
import { describe, expect, it, vi } from "vitest";

import {
  createRunPoller,
  isRunLive,
} from "@/components/projects/useRunLive";
import type { ProjectRun } from "@/types";

const RUN = (status: string): ProjectRun =>
  ({ run_no: 3, status }) as unknown as ProjectRun;

const answer = (body: unknown, ok = true): Response =>
  ({ ok, json: async () => body }) as unknown as Response;

describe("isRunLive", () => {
  it("treats only the closed statuses as finished", () => {
    expect(isRunLive("running")).toBe(true);
    expect(isRunLive("waiting")).toBe(true);
    expect(isRunLive("blocked")).toBe(true);
    expect(isRunLive("done")).toBe(false);
    expect(isRunLive("failed")).toBe(false);
    expect(isRunLive("cancelled")).toBe(false);
  });
});

describe("createRunPoller", () => {
  it("reads the run and hands the fresh row over", async () => {
    const fresh = RUN("running");
    const fetchImpl = vi.fn(async () => answer(fresh));
    const onRun = vi.fn();
    const poller = createRunPoller(
      "monday digest",
      3,
      onRun,
      fetchImpl as unknown as typeof fetch,
    );

    expect(await poller.tick()).toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/projects/monday%20digest/runs/3",
    );
    expect(onRun).toHaveBeenCalledWith(fresh);
  });

  it("reports the run finished so the caller stops polling", async () => {
    const onRun = vi.fn();
    const poller = createRunPoller(
      "p",
      1,
      onRun,
      (async () => answer(RUN("done"))) as unknown as typeof fetch,
    );
    expect(await poller.tick()).toBe(false);
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ status: "done" }));
  });

  it("swallows a non-2xx and a network failure, and keeps the tail alive", async () => {
    const onRun = vi.fn();
    const refused = createRunPoller(
      "p",
      1,
      onRun,
      (async () => answer({ detail: "nope" }, false)) as unknown as typeof fetch,
    );
    expect(await refused.tick()).toBe(true);

    const broken = createRunPoller(
      "p",
      1,
      onRun,
      (async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    );
    expect(await broken.tick()).toBe(true);
    expect(onRun).not.toHaveBeenCalled();
  });

  it("ignores a body that is not a run row", async () => {
    const onRun = vi.fn();
    const poller = createRunPoller(
      "p",
      1,
      onRun,
      (async () => answer({ error: "invalid_request" })) as unknown as typeof fetch,
    );
    expect(await poller.tick()).toBe(true);
    expect(onRun).not.toHaveBeenCalled();
  });
});
