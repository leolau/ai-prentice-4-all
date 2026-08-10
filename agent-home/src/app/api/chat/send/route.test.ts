/**
 * Send-route attachment handling (FG-20 multi-user PR-5).
 *
 * The persisted turn must carry the durable **path-bearing** media ref (the
 * bucket is private), and a client-supplied path outside the caller's own
 * prefix must never make it into the transcript.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/chat/send/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const sendChat = vi.fn(async (...args: [sessionId: string, message: string]) => {
  void args;
  return {
    session_id: "home_2",
    message: { role: "assistant" as const, content: "ok" },
  };
});

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ sendChat, createSession: async () => ({}) }),
}));

function post(body: unknown): Promise<Response> {
  return POST(
    new Request("http://home.test/api/chat/send", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  ) as unknown as Promise<Response>;
}

describe("POST /api/chat/send attachments", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue({
      user_id: "mia_member",
      display: "Mia",
      role: "member",
      channels: [],
      is_owner: false,
    });
  });

  it("persists the BFF media ref, not a storage URL", async () => {
    const res = await post({
      sessionId: "home_2",
      message: "look",
      attachments: [
        {
          path: "mia_member/home_2/u1-a.png",
          name: "a.png",
          content_type: "image/png",
          size: 4,
        },
      ],
    });
    expect(res.status).toBe(200);
    const [, message] = sendChat.mock.calls[0];
    expect(message).toContain(
      "![a.png](/api/chat/media?path=mia_member%2Fhome_2%2Fu1-a.png)",
    );
    expect(message).not.toContain("http");
  });

  it("drops an attachment path owned by another principal", async () => {
    const res = await post({
      sessionId: "home_2",
      message: "look",
      attachments: [
        {
          path: "leo_owner/home_1/abc-photo.png",
          name: "steal.png",
          content_type: "image/png",
          size: 4,
        },
      ],
    });
    expect(res.status).toBe(200);
    const [, message] = sendChat.mock.calls[0];
    expect(message).toBe("look");
    expect(message).not.toContain("leo_owner");
  });

  it("rejects an attachment-only send whose paths are all foreign", async () => {
    const res = await post({
      sessionId: "home_2",
      message: "",
      attachments: [{ path: "leo_owner/home_1/abc.png", name: "x.png" }],
    });
    expect(res.status).toBe(400);
    expect(sendChat).not.toHaveBeenCalled();
  });
});
