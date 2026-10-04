/** BFF route test for the board context read. */
import { describe, expect, it, vi } from "vitest";

const principalState: { principal: unknown } = { principal: { user_id: "leo" } };
const clientState: { client: unknown } = { client: null };

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: async () => principalState.principal,
  apiClientForRequest: async () => clientState.client,
}));

import { GET } from "./route";

const params = { params: Promise.resolve({ slug: "digest" }) };

describe("GET /api/projects/:slug/board/context", () => {
  it("answers 401 without a session", async () => {
    principalState.principal = null;
    expect((await GET(new Request("http://x"), params)).status).toBe(401);
  });

  it("returns the upstream context", async () => {
    principalState.principal = { user_id: "leo" };
    const ctx = { card_runs: { t_1: 2 }, open_run: null };
    clientState.client = { projectBoardContext: vi.fn(async () => ctx) };
    const res = await GET(new Request("http://x"), params);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(ctx);
  });
});
