/**
 * BFF route tests for "ask the project": principal gate, validation before
 * any upstream call, history trimmed, the idempotency key forwarded, and
 * upstream refusals passed through with their own copy.
 */
import { describe, expect, it } from "vitest";
import { vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { POST } from "./route";

function params(slug: string) {
  return { params: Promise.resolve({ slug }) };
}

function req(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("http://x/api/projects/digest/ask", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("POST /api/projects/:slug/ask", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    const res = await POST(req({ question: "q" }), params("digest"));
    expect(res.status).toBe(401);
    principalState.principal = { user_id: "leo" };
  });

  it("refuses an empty or too-long question without calling upstream", async () => {
    const askProject = vi.fn();
    clientState.client = { askProject };
    expect((await POST(req({ question: "  " }), params("digest"))).status).toBe(400);
    expect(
      (await POST(req({ question: "x".repeat(2001) }), params("digest"))).status,
    ).toBe(400);
    expect(askProject).not.toHaveBeenCalled();
  });

  it("forwards the question, the last six history turns and the key", async () => {
    const askProject = vi.fn(async () => ({ answer: "Two drafts left.", sources: [] }));
    clientState.client = { askProject };
    const history = Array.from({ length: 9 }, (_, i) => ({ q: `q${i}`, a: `a${i}` }));
    const res = await POST(
      req({ question: " What's left? ", history: [...history, { q: "x" }, null] }, {
        "Idempotency-Key": "k-1",
      }),
      params("digest"),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ answer: "Two drafts left.", sources: [] });
    expect(askProject).toHaveBeenCalledWith(
      "digest",
      { question: "What's left?", history: history.slice(-6) },
      "k-1",
    );
  });

  it("passes an upstream refusal through with its copy", async () => {
    clientState.client = {
      askProject: async () => {
        throw new HermesApiError(429, "POST", {
          detail: "You're asking a lot quickly — wait a minute and try again.",
        });
      },
    };
    const res = await POST(req({ question: "q" }), params("digest"));
    expect(res.status).toBe(429);
    const body = (await res.json()) as { detail: string };
    expect(body.detail).toMatch(/wait a minute/);
  });
});
