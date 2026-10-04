/**
 * The projects BFF bridge's error convention (F6): upstream statuses pass
 * through with the upstream's OWN copy. The internal path + status must
 * never reach a user — the design's refusal wording (*retire one first*,
 * the budget refusal) arrives in `err.body.detail`, and the bridge
 * forwards it verbatim.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { HermesApiClient, HermesApiError } from "@/lib/api/client";

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: vi.fn(),
  apiClientForRequest: vi.fn(async () => ({})),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(async () => {
    throw new Error("outside a request scope");
  }),
}));

import { headers } from "next/headers";

import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

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

describe("hermes-bridge Idempotency-Key forwarding", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function bridgeRequest(incoming: Record<string, string>) {
    vi.mocked(getPrincipal).mockResolvedValueOnce(PRINCIPAL as never);
    vi.mocked(headers).mockResolvedValueOnce(new Headers(incoming) as never);
    vi.mocked(apiClientForRequest).mockResolvedValueOnce(
      new HermesApiClient({ baseUrl: "http://hermes.test", hermesToken: "tok" }),
    );
    const upstream = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    vi.stubGlobal("fetch", upstream);
    return upstream;
  }

  function sentHeaders(upstream: ReturnType<typeof bridgeRequest>): Headers {
    expect(upstream).toHaveBeenCalledTimes(1);
    return new Headers(upstream.mock.calls[0][1].headers);
  }

  it.each(["POST", "PATCH", "DELETE"] as const)(
    "forwards the browser's key upstream on %s",
    async (method) => {
      const upstream = bridgeRequest({ "Idempotency-Key": "key-123" });
      const res = await withPrincipal((client) =>
        client.request("/api/registry/projects/p/runs", {
          method,
          json: method === "DELETE" ? undefined : {},
        }),
      );
      expect(res.status).toBe(200);
      const sent = sentHeaders(upstream);
      expect(sent.get("idempotency-key")).toBe("key-123");
      // The session replay is untouched by the extra header.
      expect(sent.get("authorization")).toBe("Bearer tok");
    },
  );

  it.each(["POST", "PATCH", "DELETE"] as const)(
    "never invents a key when the browser sent none (%s)",
    async (method) => {
      const upstream = bridgeRequest({});
      await withPrincipal((client) =>
        client.request("/api/registry/projects/p", { method }),
      );
      expect(sentHeaders(upstream).has("idempotency-key")).toBe(false);
    },
  );

  it("does not put the key on reads", async () => {
    const upstream = bridgeRequest({ "Idempotency-Key": "key-123" });
    await withPrincipal((client) => client.request("/api/registry/projects/p"));
    expect(sentHeaders(upstream).has("idempotency-key")).toBe(false);
  });

  it("forwards through the typed client methods too", async () => {
    const upstream = bridgeRequest({ "Idempotency-Key": "run-9" });
    await withPrincipal((client) => client.startProjectRun("p", {}));
    const sent = sentHeaders(upstream);
    expect(upstream.mock.calls[0][0]).toContain("/api/registry/projects/p/runs");
    expect(sent.get("idempotency-key")).toBe("run-9");
  });

  it("ignores a blank key", async () => {
    const upstream = bridgeRequest({ "Idempotency-Key": "   " });
    await withPrincipal((client) =>
      client.request("/api/registry/projects/p", { method: "POST" }),
    );
    expect(sentHeaders(upstream).has("idempotency-key")).toBe(false);
  });
});
