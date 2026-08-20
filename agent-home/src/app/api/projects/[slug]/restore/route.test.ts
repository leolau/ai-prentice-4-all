/**
 * BFF route tests for the restore act (§13): principal gate and upstream
 * statuses passed through — restore is the unblocking act, so its refusals
 * must reach the dialog verbatim.
 */
import { describe, expect, it, vi } from "vitest";

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

function req(): Request {
  return new Request("http://x/api/projects/digest/restore", { method: "POST" });
}

describe("POST /api/projects/:slug/restore", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    const res = await POST(req(), params("digest"));
    expect(res.status).toBe(401);
  });

  it("forwards the upstream's refusal wording on a 409", async () => {
    principalState.principal = { user_id: "leo" };
    clientState.client = {
      restoreProject: async () => {
        throw new HermesApiError(409, "POST /restore", {
          detail: "project digest is not archived",
        });
      },
    };
    const res = await POST(req(), params("digest"));
    expect(res.status).toBe(409);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body.detail).toBe("project digest is not archived");
  });

  it("returns the updated row on success", async () => {
    principalState.principal = { user_id: "leo" };
    clientState.client = {
      restoreProject: async (slug: string) => ({ slug, status: "paused" }),
    };
    const res = await POST(req(), params("digest"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ slug: "digest", status: "paused" });
  });
});
