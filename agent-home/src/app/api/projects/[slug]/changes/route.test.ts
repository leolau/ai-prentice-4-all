/**
 * BFF routes for the change-requirements flow: principal gate, validation,
 * the Idempotency-Key forwarded upstream, and upstream refusals passed
 * through so the sheet can show the backend's own sentence.
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

import { GET, POST } from "./route";
import { POST as APPROVE } from "./[changeId]/approve/route";
import { POST as REDRAFT } from "./[changeId]/draft/route";

const slugParams = { params: Promise.resolve({ slug: "digest" }) };
const changeParams = { params: Promise.resolve({ slug: "digest", changeId: "d_1" }) };

function post(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("http://x/api/projects/digest/changes", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

describe("/api/projects/:slug/changes", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    const res = await POST(post({ text: "x", apply: "record" }), slugParams);
    expect(res.status).toBe(401);
    principalState.principal = { user_id: "leo" };
  });

  it("validates text and apply before calling upstream", async () => {
    const calls: unknown[] = [];
    clientState.client = { createProjectChange: async (...a: unknown[]) => calls.push(a) };
    expect((await POST(post({ text: " ", apply: "now" }), slugParams)).status).toBe(400);
    expect((await POST(post({ text: "x", apply: "later" }), slugParams)).status).toBe(400);
    expect(calls).toEqual([]);
  });

  it("forwards the change and its idempotency key", async () => {
    const calls: unknown[][] = [];
    clientState.client = {
      createProjectChange: async (...a: unknown[]) => {
        calls.push(a);
        return { change: { id: "d_1" }, draft: { status: "running" }, stopped_run: 2 };
      },
    };
    const res = await POST(
      post({ text: " Add Chinese ", kinds: ["add"], apply: "now" }, { "Idempotency-Key": "k1" }),
      slugParams,
    );
    expect(res.status).toBe(200);
    expect((await res.json()).stopped_run).toBe(2);
    expect(calls[0]).toEqual(["digest", { text: "Add Chinese", kinds: ["add"], apply: "now" }, "k1"]);
  });

  it("reads the history", async () => {
    clientState.client = { projectChanges: async () => ({ changes: [], runs: [] }) };
    const res = await GET(new Request("http://x"), slugParams);
    expect(await res.json()).toEqual({ changes: [], runs: [] });
  });
});

describe("/api/projects/:slug/changes/:id/approve", () => {
  it("requires a revision", async () => {
    clientState.client = { approveProjectChange: async () => ({}) };
    expect((await APPROVE(post({}), changeParams)).status).toBe(400);
  });

  it("forwards rev, start:false and supersedes", async () => {
    const calls: unknown[][] = [];
    clientState.client = {
      approveProjectChange: async (...a: unknown[]) => {
        calls.push(a);
        return { rev: 4, run: null };
      },
    };
    await APPROVE(post({ rev: 4, start: false, supersedes: ["d_0"] }, { "Idempotency-Key": "k" }), changeParams);
    expect(calls[0]).toEqual(["digest", "d_1", { rev: 4, start: false, supersedes: ["d_0"] }, "k"]);
  });

  it("passes the 409 sentence through", async () => {
    clientState.client = {
      approveProjectChange: async () => {
        throw new HermesApiError(409, "POST", { detail: "Run 2 is still open." });
      },
    };
    const res = await APPROVE(post({ rev: 4 }), changeParams);
    expect(res.status).toBe(409);
    expect((await res.json()).detail).toBe("Run 2 is still open.");
  });
});

describe("/api/projects/:slug/changes/:id/draft", () => {
  it("re-drafts", async () => {
    clientState.client = {
      redraftProjectChange: async (slug: string, id: string) => ({ draft: { status: "running" }, slug, id }),
    };
    const res = await REDRAFT(new Request("http://x", { method: "POST" }), changeParams);
    expect(await res.json()).toMatchObject({ slug: "digest", id: "d_1" });
  });
});
