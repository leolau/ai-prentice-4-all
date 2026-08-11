/**
 * BFF route tests for GET/POST /api/chat/sessions/tags.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/chat/sessions/tags/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const listTags = vi.fn(async () => ({
  tags: [{ id: "t1", name: "bug", color: "red", session_count: 2 }],
}));
const createTag = vi.fn(async () => ({
  tag: { id: "t1", name: "bug", color: "red" },
}));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ listTags, createTag }),
}));

describe("GET /api/chat/sessions/tags", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "mia",
      display: "Mia",
      role: "member",
      channels: [],
      is_owner: false,
    });
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await GET(new Request("http://x/api/chat/sessions/tags"));
    expect(res.status).toBe(401);
  });

  it("forwards to client.listTags and returns tags", async () => {
    const res = await GET(new Request("http://x/api/chat/sessions/tags"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.tags).toHaveLength(1);
    expect(data.tags[0].name).toBe("bug");
    expect(listTags).toHaveBeenCalledOnce();
  });

  it("returns 502 when the AI layer is unreachable", async () => {
    const { HermesApiError } = await import("@/lib/api/client");
    listTags.mockRejectedValueOnce(new HermesApiError(502, "down"));
    const res = await GET(new Request("http://x/api/chat/sessions/tags"));
    expect(res.status).toBe(502);
  });
});

describe("POST /api/chat/sessions/tags", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "mia",
      display: "Mia",
      role: "member",
      channels: [],
      is_owner: false,
    });
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const req = new Request("http://localhost/api/chat/sessions/tags", {
      method: "POST",
      body: JSON.stringify({ name: "bug" }),
    });
    const res = await POST(req as any);
    expect(res.status).toBe(401);
  });

  it("returns 400 when name is missing", async () => {
    const req = new Request("http://localhost/api/chat/sessions/tags", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const res = await POST(req as any);
    expect(res.status).toBe(400);
  });

  it("forwards to client.createTag and returns the tag", async () => {
    const req = new Request("http://localhost/api/chat/sessions/tags", {
      method: "POST",
      body: JSON.stringify({ name: "bug", color: "red" }),
    });
    const res = await POST(req as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.tag.name).toBe("bug");
    expect(createTag).toHaveBeenCalledWith("bug", "red");
  });

  it("forwards undefined color when omitted", async () => {
    const req = new Request("http://localhost/api/chat/sessions/tags", {
      method: "POST",
      body: JSON.stringify({ name: "feature" }),
    });
    const res = await POST(req as any);
    expect(res.status).toBe(200);
    expect(createTag).toHaveBeenCalledWith("feature", undefined);
  });

  it("returns 502 when the AI layer is unreachable", async () => {
    const { HermesApiError } = await import("@/lib/api/client");
    createTag.mockRejectedValueOnce(new HermesApiError(502, "down"));
    const req = new Request("http://localhost/api/chat/sessions/tags", {
      method: "POST",
      body: JSON.stringify({ name: "bug" }),
    });
    const res = await POST(req as any);
    expect(res.status).toBe(502);
  });
});
