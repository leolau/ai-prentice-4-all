/**
 * The projects BFF bridge's error convention (F6): upstream statuses pass
 * through with the upstream's OWN copy. The internal path + status must
 * never reach a user — the design's refusal wording (*retire one first*,
 * the budget refusal) arrives in `err.body.detail`, and the bridge
 * forwards it verbatim.
 */
import { describe, expect, it, vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: vi.fn(),
  apiClientForRequest: vi.fn(async () => ({})),
}));

import { getPrincipal } from "@/lib/auth/principal";

import { withPrincipal } from "./hermes-bridge";

const PRINCIPAL = { user_id: "leo", display: "Leo", role: "owner" };

function mockSession() {
  vi.mocked(getPrincipal).mockResolvedValueOnce(PRINCIPAL as never);
}

describe("hermes-bridge withPrincipal", () => {
  it("answers 401 without a session", async () => {
    vi.mocked(getPrincipal).mockResolvedValueOnce(null);
    const res = await withPrincipal(async () => ({}));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "unauthenticated" });
  });

  it("forwards an upstream refusal verbatim — never the internal path", async () => {
    mockSession();
    const res = await withPrincipal(async () => {
      throw new HermesApiError(409, "Retire one first.", {
        detail: "Retire one first.",
      });
    });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body).toEqual({ error: "api_error", detail: "Retire one first." });
    expect(JSON.stringify(body)).not.toContain("/api/registry/");
  });

  it("falls back to generic copy when upstream sent no detail", async () => {
    mockSession();
    const res = await withPrincipal(async () => {
      throw new HermesApiError(500, "Something failed.", { error: "boom" });
    });
    expect(res.status).toBe(500);
    expect(await res.json()).toEqual({
      error: "api_error",
      detail: "That didn't go through.",
    });
  });

  it("maps anything that is not an upstream answer to a 502", async () => {
    mockSession();
    const res = await withPrincipal(async () => {
      throw new Error("socket hang up");
    });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({
      error: "api_unreachable",
      detail: "The AI layer could not be reached.",
    });
  });
});
