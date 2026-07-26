import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  canReadMediaPath,
  createMediaSignedUrl,
  scopedMediaPath,
  uploadChatMedia,
} from "@/lib/supabase/storage";
import type { Principal } from "@/types";

const upload = vi.fn(async () => ({ error: null }));
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
const getPublicUrl = vi.fn(() => ({ data: { publicUrl: "https://sb.test/public" } }));

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    storage: { from: () => ({ upload, createSignedUrl, getPublicUrl }) },
  }),
}));

function principal(over: Partial<Principal> = {}): Principal {
  return {
    user_id: "leo_owner",
    display: "Leo",
    role: "owner",
    channels: [],
    is_owner: true,
    ...over,
  };
}

describe("scopedMediaPath", () => {
  it("prefixes the object key with the principal's user_id", () => {
    const path = scopedMediaPath(principal(), "home_1", "photo.png", "abc");
    expect(path).toBe("leo_owner/home_1/abc-photo.png");
  });

  it("uses a fresh-conversation prefix when there is no session yet", () => {
    const path = scopedMediaPath(principal(), "", "a.jpg", "u1");
    expect(path.startsWith("leo_owner/new/")).toBe(true);
  });

  it("neutralises path traversal in every segment", () => {
    const path = scopedMediaPath(
      principal({ user_id: "../../etc" }),
      "../../../root",
      "../../evil.sh",
      "x/y",
    );
    expect(path).not.toContain("..");
    expect(path.split("/")).toHaveLength(3);
    // The crafted user id can never escape its own prefix segment.
    expect(path.startsWith("etc/")).toBe(true);
  });
});

describe("canReadMediaPath", () => {
  const member = principal({ user_id: "mia_member", role: "member", is_owner: false });

  it("accepts the principal's own object", () => {
    expect(canReadMediaPath(member, "mia_member/home_2/u1-a.png")).toBe(true);
  });

  it("rejects another principal's object (negative access)", () => {
    expect(canReadMediaPath(member, "leo_owner/home_1/abc-photo.png")).toBe(false);
  });

  it("rejects cross-user reads for an owner too (fail-closed default)", () => {
    expect(canReadMediaPath(principal(), "mia_member/home_2/u1-a.png")).toBe(false);
  });

  it("rejects prefix look-alikes", () => {
    expect(canReadMediaPath(member, "mia_member_evil/s/a.png")).toBe(false);
    expect(canReadMediaPath(member, "not_mia_member/s/a.png")).toBe(false);
  });

  it("rejects crafted paths", () => {
    for (const bad of [
      "",
      "mia_member",
      "mia_member/../leo_owner/home_1/abc-photo.png",
      "../leo_owner/home_1/abc.png",
      "/mia_member/s/a.png",
      "mia_member//s/a.png",
      "mia_member/./a.png",
      "mia_member/s/..",
      "mia_member\\s\\a.png",
      "mia_member/%2e%2e/leo_owner/a.png",
      "mia_member/s/a b.png",
      `mia_member/${"x".repeat(600)}/a.png`,
    ]) {
      expect(canReadMediaPath(member, bad), bad).toBe(false);
    }
  });
});

describe("uploadChatMedia", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.SUPABASE_URL = "https://sb.test";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "service-role";
  });

  it("returns the object path and never a public URL", async () => {
    const attachment = await uploadChatMedia(principal(), "home_1", {
      name: "photo.png",
      contentType: "image/png",
      bytes: new ArrayBuffer(4),
    });
    expect(attachment.path.startsWith("leo_owner/home_1/")).toBe(true);
    expect(attachment).not.toHaveProperty("url");
    expect(getPublicUrl).not.toHaveBeenCalled();
    expect(upload).toHaveBeenCalledOnce();
  });
});

describe("createMediaSignedUrl", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.SUPABASE_URL = "https://sb.test";
    process.env.SUPABASE_SERVICE_ROLE_KEY = "service-role";
  });

  it("signs the path with the requested short TTL", async () => {
    const signed = await createMediaSignedUrl("leo_owner/home_1/abc-photo.png", 45);
    expect(createSignedUrl).toHaveBeenCalledWith("leo_owner/home_1/abc-photo.png", 45);
    expect(signed).toEqual({
      url: "https://sb.test/object/sign/leo_owner/home_1/abc-photo.png?exp=45",
      expires_in: 45,
    });
  });

  it("defaults to the configured TTL", async () => {
    delete process.env.AGENT_HOME_MEDIA_URL_TTL;
    await createMediaSignedUrl("leo_owner/home_1/abc-photo.png");
    expect(createSignedUrl).toHaveBeenCalledWith(
      "leo_owner/home_1/abc-photo.png",
      60,
    );
  });

  it("returns null when Storage cannot sign the object", async () => {
    createSignedUrl.mockResolvedValueOnce({
      data: { signedUrl: "" },
      error: { message: "Object not found" },
    });
    expect(await createMediaSignedUrl("leo_owner/home_1/missing.png")).toBeNull();
  });

  it("throws when Storage is unconfigured", async () => {
    delete process.env.SUPABASE_SERVICE_ROLE_KEY;
    await expect(
      createMediaSignedUrl("leo_owner/home_1/abc-photo.png"),
    ).rejects.toThrow(/not configured/);
  });
});
