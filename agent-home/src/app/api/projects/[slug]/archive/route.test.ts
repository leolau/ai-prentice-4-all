/**
 * BFF route tests for the archive act (§13): principal gate, upstream
 * refusal text passed through verbatim, and the optional `reason`
 * forwarded.
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

function req(body: unknown): Request {
  return new Request("http://x/api/projects/digest/archive", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

describe("POST /api/projects/:slug/archive", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    const res = await POST(req({}), params("digest"));
    expect(res.status).toBe(401);
  });

  it("forwards the upstream's refusal wording on a 409", async () => {
    principalState.principal = { user_id: "leo" };
    clientState.client = {
      archiveProject: async () => {
        throw new HermesApiError(409, "POST /archive", {
          detail: "refused: needs_completion — goal is blank",
        });
      },
    };
    const res = await POST(req({}), params("digest"));
    expect(res.status).toBe(409);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body.error).toBe("api_error");
    expect(body.detail).toBe("refused: needs_completion — goal is blank");
  });

  it("forwards the reason and returns the updated row", async () => {
    principalState.principal = { user_id: "leo" };
    const calls: Array<[string, string | undefined]> = [];
    clientState.client = {
      archiveProject: async (slug: string, reason?: string) => {
        calls.push([slug, reason]);
        return { slug, archived: true, status: "archived" };
      },
    };
    const res = await POST(req({ reason: "  winding down  " }), params("digest"));
    expect(res.status).toBe(200);
    expect(calls).toEqual([["digest", "winding down"]]);
    expect(await res.json()).toEqual({
      slug: "digest",
      archived: true,
      status: "archived",
    });
  });
});
