/**
 * BFF route tests for POST /api/chat/upload.
 *
 * The route is unlimited by default; `AGENT_HOME_UPLOAD_MAX_BYTES` reinstates
 * a cap when a deploy wants one. The 413 case stubs that env var.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/chat/upload/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const uploadChatMedia = vi.fn(
  async (
    _principal: Principal,
    sessionId: string,
    file: { name: string; contentType: string; bytes: ArrayBuffer },
  ) => ({
    path: `media/${sessionId || "new"}/${file.name}`,
    name: file.name,
    content_type: file.contentType,
    size: file.bytes.byteLength,
  }),
);
const registerFile = vi.fn(async () => ({ ok: true }));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ registerFile }),
}));
vi.mock("@/lib/env", () => ({ mediaBucket: () => "hermes-media" }));
vi.mock("@/lib/supabase/storage", () => ({
  storageAvailable: () => true,
  uploadChatMedia: (...args: unknown[]) =>
    (uploadChatMedia as (...a: unknown[]) => unknown)(...args),
}));

const principal: Principal = {
  user_id: "mia",
  display: "Mia",
  role: "member",
  channels: [],
  is_owner: false,
};

function post(file: File): Promise<Response> {
  const form = new FormData();
  form.set("file", file);
  form.set("sessionId", "s1");
  return POST(
    new Request("http://home.test/api/chat/upload", {
      method: "POST",
      body: form,
    }),
  ) as unknown as Promise<Response>;
}

describe("POST /api/chat/upload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    getPrincipal.mockResolvedValue(principal);
  });

  it("returns 401 when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await post(new File([new Uint8Array(8)], "a.txt"));
    expect(res.status).toBe(401);
  });

  it("accepts a file above the old 10 MB cap", async () => {
    const elevenMb = new File(
      [new Uint8Array(11 * 1024 * 1024)],
      "big.bin",
      { type: "application/octet-stream" },
    );
    const res = await post(elevenMb);
    expect(res.status).toBe(200);
    expect(uploadChatMedia).toHaveBeenCalledOnce();
  });

  it("is unlimited by default", async () => {
    const big = new File(
      [new Uint8Array(101 * 1024 * 1024)],
      "huge.bin",
      { type: "application/octet-stream" },
    );
    const res = await post(big);
    expect(res.status).toBe(200);
    expect(uploadChatMedia).toHaveBeenCalledOnce();
  });

  it("refuses a file above a configured cap with 413", async () => {
    vi.stubEnv("AGENT_HOME_UPLOAD_MAX_BYTES", "104857600");
    const oversize = new File(
      [new Uint8Array(100 * 1024 * 1024 + 1)],
      "huge.bin",
      { type: "application/octet-stream" },
    );
    const res = await post(oversize);
    expect(res.status).toBe(413);
    expect(uploadChatMedia).not.toHaveBeenCalled();
    const body = (await res.json()) as { detail?: string };
    expect(body.detail).toMatch(/100 MB/);
  });
});
