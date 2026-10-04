import { describe, expect, it } from "vitest";

import { CHAT_FIRST_PAGE_SIZE } from "@/app/chat/first-page";
import { TRANSCRIPT_PAGE_SIZE } from "@/lib/chat/chat-controller";

describe("chat first page", () => {
  it("matches the controller's page so the next page starts where SSR stopped", () => {
    expect(CHAT_FIRST_PAGE_SIZE).toBe(TRANSCRIPT_PAGE_SIZE);
  });
});
