/**
 * Tests for the BFF client's request-timing log — the latency signal that
 * attributes slow page loads to specific Python endpoints.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient } from "@/lib/api/client";

afterEach(() => {
  vi.restoreAllMocks();
});

function ok(body: unknown) {
  return () =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
}

function client() {
  return new HermesApiClient({ baseUrl: "http://api.test" });
}

describe("HermesApiClient request timing", () => {
  it("logs one api-timing line per call with method, path and status", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(ok({ ok: true }));
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await client().whoami();

    const lines = [...log.mock.calls, ...warn.mock.calls].map((c) => String(c[0]));
    const timing = lines.find((l) => l.startsWith("api-timing"));
    expect(timing).toBeDefined();
    expect(timing).toContain("GET /api/comms/whoami");
    expect(timing).toContain("status=200");
    expect(timing).toMatch(/elapsed_ms=\d+/);
  });

  it("warns instead of logging when the call is slow (>= 500 ms)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve(
                new Response(JSON.stringify({ ok: true }), { status: 200 }),
              ),
            500,
          ),
        ),
    );
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    await client().whoami();

    expect(warn).toHaveBeenCalled();
    const lines = warn.mock.calls.map((c) => String(c[0]));
    expect(lines.some((l) => l.startsWith("api-timing"))).toBe(true);
    expect(log.mock.calls.find((c) => String(c[0]).startsWith("api-timing"))).toBeUndefined();
  });
});
