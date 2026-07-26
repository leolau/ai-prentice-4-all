/**
 * BFF media read-route tests (FG-20 multi-user PR-5) — the C2 storage gate.
 *
 * The isolation these assert is the whole point of PR-5: a member must never be
 * able to obtain a signed URL for another principal's object, and a crafted
 * path must be refused **before** anything is signed.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/chat/media/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
interface SignResult {
  data: { signedUrl: string };
  error: { message: string } | null;
}
const createSignedUrl = vi.fn(
  async (path: string, ttl: number): Promise<SignResult> => ({
    data: { signedUrl: `https://sb.test/object/sign/${path}?exp=${ttl}` },
    error: null,
  }),
);

vi.mock("@/lib/auth/principal", () => ({ getPrincipal: () => getPrincipal() }));
vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({ storage: { from: () => ({ createSignedUrl }) } }),
}));

function principal(user_id: string, role: Principal["role"]): Principal {
  return {
    user_id,
    display: user_id,
    role,
    channels: [],
    is_owner: role === "owner",
  };
}

function get(path: string | null): Promise<Response> {
  const url = new URL("http://home.test/api/chat/media");
  if (path !== null) url.searchParams.set("path", path);
  return GET(new Request(url)) as unknown as Promise<Response>;
}

const OWN = "mia_member/home_2/u1-a.png";
const OTHER = "leo_owner/home_1/abc-photo.png";

describe("GET /api/chat/media", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.SUPABASE_URL = "https://sb.test";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "service-role";
    delete process.env.AGENT_HOME_MEDIA_URL_TTL;
    getPrincipal.mockResolvedValue(principal("mia_member", "member"));
  });

  it("401s an unauthenticated request", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await get(OWN);
    expect(res.status).toBe(401);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("400s without a path", async () => {
    expect((await get(null)).status).toBe(400);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("signs the caller's own object with the short TTL", async () => {
    process.env.AGENT_HOME_MEDIA_URL_TTL = "30";
    const res = await get(OWN);
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({
      path: OWN,
      url: `https://sb.test/object/sign/${OWN}?exp=30`,
      expires_in: 30,
    });
    expect(createSignedUrl).toHaveBeenCalledWith(OWN, 30);
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("defaults to a 60s TTL when unconfigured", async () => {
    await get(OWN);
    expect(createSignedUrl).toHaveBeenCalledWith(OWN, 60);
  });

  it("403s a member asking for another principal's object", async () => {
    const res = await get(OTHER);
    expect(res.status).toBe(403);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("403s an owner asking for a member's object (fail-closed default)", async () => {
    getPrincipal.mockResolvedValue(principal("leo_owner", "owner"));
    const res = await get(OWN);
    expect(res.status).toBe(403);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("403s crafted / traversal paths without signing", async () => {
    for (const bad of [
      "mia_member/../leo_owner/home_1/abc-photo.png",
      "../leo_owner/home_1/abc-photo.png",
      "/mia_member/home_2/u1-a.png",
      "mia_member/%2e%2e/leo_owner/abc.png",
      "mia_member",
      "mia_member//home_2/u1-a.png",
    ]) {
      const res = await get(bad);
      expect(res.status, bad).toBe(403);
    }
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it("404s when the object cannot be signed", async () => {
    createSignedUrl.mockResolvedValueOnce({
      data: { signedUrl: "" },
      error: { message: "Object not found" },
    });
    expect((await get(OWN)).status).toBe(404);
  });

  it("501s when Storage is unconfigured on the box", async () => {
    delete process.env.SUPABASE_SERVICE_ROLE_KEY;
    const res = await get(OWN);
    expect(res.status).toBe(501);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });
});
