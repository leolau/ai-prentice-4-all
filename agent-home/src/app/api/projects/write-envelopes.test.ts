/**
 * Block 5 — the BFF half of the F1 class: the projects mirror routes must
 * hand the upstream write envelope to the panel UNTOUCHED. A reshape here
 * (or a status swallowed into a 200) silently breaks the panel's state
 * update tested in components/projects/envelopes.test.ts.
 */
import { describe, expect, it, vi } from "vitest";

const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => ({ user_id: "leo" }),
  apiClientForRequest: async () => clientState.client,
}));

import { POST as acceptPost } from "./[slug]/outputs/[outputId]/accept/route";
import { POST as continuePost } from "./[slug]/runs/[runNo]/continue/route";
import { POST as directivesPost } from "./[slug]/directives/route";

const NOW = Math.floor(Date.now() / 1000);

describe("POST /api/projects/:slug/outputs/:outputId/accept", () => {
  it("passes the upstream envelope through — row, actor, closure offer", async () => {
    // Exactly what projects_api.accept_output answers.
    const envelope = {
      output: { id: "out_1", status: "accepted", accepted_at: NOW, accepted_by: "leo" },
      accepted: "out_1",
      by: "leo",
      offers_closure: true,
    };
    clientState.client = { acceptProjectOutput: async () => envelope };
    const res = await acceptPost(new Request("http://x/api/projects/digest/outputs/out_1/accept", { method: "POST" }), {
      params: Promise.resolve({ slug: "digest", outputId: "out_1" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(envelope);
  });
});

describe("POST /api/projects/:slug/runs/:runNo/continue", () => {
  it("passes the {run, promoted, budget_gate} envelope through", async () => {
    // Exactly what projects_run.continue_run answers.
    const envelope = {
      run: { run_no: 14, status: "running", outcome: null },
      promoted: ["task_3"],
      budget_gate: "Run 14 has spent $9.80 of a $10.00 budget — continue?",
    };
    clientState.client = { continueProjectRun: async () => envelope };
    const res = await continuePost(new Request("http://x/api/projects/digest/runs/14/continue", { method: "POST" }), {
      params: Promise.resolve({ slug: "digest", runNo: "14" }),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(envelope);
  });

  it("rejects a non-integer run number before touching upstream", async () => {
    clientState.client = null;
    const res = await continuePost(new Request("http://x/api/projects/digest/runs/x/continue", { method: "POST" }), {
      params: Promise.resolve({ slug: "digest", runNo: "x" }),
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { detail: string }).detail).toContain("integer");
  });
});

describe("POST /api/projects/:slug/directives", () => {
  it("passes the full created row through, applies_from flat beside it", async () => {
    // Exactly what projects_api.add_directive_route answers.
    const envelope = {
      id: "dir_9",
      project_id: "prj_1",
      kind: "directive",
      body: "Never email before 9am",
      scope: "project",
      target_ref: null,
      rating: null,
      author_user_id: "leo",
      created_at: NOW,
      active: 1,
      retired_at: null,
      superseded_by: null,
      applies_from: "next run",
    };
    clientState.client = { addProjectDirective: async () => envelope };
    const res = await directivesPost(
      new Request("http://x/api/projects/digest/directives", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ body: "Never email before 9am" }),
      }),
      { params: Promise.resolve({ slug: "digest" }) },
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(envelope);
  });

  it("rejects empty guidance before touching upstream", async () => {
    clientState.client = null;
    const res = await directivesPost(
      new Request("http://x/api/projects/digest/directives", {
        method: "POST",
        body: JSON.stringify({ body: "   " }),
      }),
      { params: Promise.resolve({ slug: "digest" }) },
    );
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe("invalid_request");
  });
});
