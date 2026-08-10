import { describe, expect, it } from "vitest";

import { mediaRef, mediaRefPath } from "@/lib/chat/media-ref";

describe("media refs", () => {
  it("round-trips an object path through the BFF read route", () => {
    const path = "leo_owner/home_1/abc-photo.png";
    const ref = mediaRef(path);
    expect(ref.startsWith("/api/chat/media?path=")).toBe(true);
    expect(ref).not.toContain(" ");
    expect(mediaRefPath(ref)).toBe(path);
  });

  it("never emits a bucket or signed URL", () => {
    expect(mediaRef("u/s/a.png")).not.toContain("http");
  });

  it("returns null for a plain external URL", () => {
    expect(mediaRefPath("https://cdn.test/a.png")).toBeNull();
    expect(mediaRefPath("/api/chat/messages?sessionId=1")).toBeNull();
  });
});
