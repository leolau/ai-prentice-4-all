/**
 * BFF routes for the produced-files read: principal gate, the typed client
 * call, and the content route piping bytes / passing a 404 through.
 */
import { describe, expect, it, vi } from "vitest";

import { HermesApiError } from "@/lib/api/client";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { GET as getContent } from "./[artifactId]/content/route";
import { GET } from "./route";

describe("GET /api/projects/:slug/artifacts", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    const res = await GET(new Request("http://x"), { params: Promise.resolve({ slug: "mou" }) });
    expect(res.status).toBe(401);
  });

  it("returns the upstream list", async () => {
    principalState.principal = { user_id: "leo" };
    const seen: string[] = [];
    clientState.client = {
      projectArtifacts: async (slug: string) => {
        seen.push(slug);
        return [{ id: "d:1", title: "MOU.docx" }];
      },
    };
    const res = await GET(new Request("http://x"), { params: Promise.resolve({ slug: "mou" }) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([{ id: "d:1", title: "MOU.docx" }]);
    expect(seen).toEqual(["mou"]);
  });
});

describe("GET /api/projects/:slug/artifacts/:id/content", () => {
  const params = Promise.resolve({ slug: "mou", artifactId: "f:t_1:abc" });

  it("pipes the bytes with their type", async () => {
    principalState.principal = { user_id: "leo" };
    clientState.client = {
      projectArtifactContent: async () =>
        new Response("hello", { status: 200, headers: { "content-type": "text/markdown" } }),
    };
    const res = await getContent(new Request("http://x"), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/markdown");
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(await res.text()).toBe("hello");
  });

  it("passes a 404 through with plain copy", async () => {
    clientState.client = {
      projectArtifactContent: async () => {
        throw new HermesApiError(404, "GET", { detail: "file not found" });
      },
    };
    const res = await getContent(new Request("http://x"), { params });
    expect(res.status).toBe(404);
    expect(((await res.json()) as { detail: string }).detail).toMatch(/no longer available/);
  });
});
