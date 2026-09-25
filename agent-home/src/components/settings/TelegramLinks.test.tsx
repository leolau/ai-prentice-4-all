// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TelegramLinks, telegramIds } from "@/components/settings/TelegramLinks";
import type { Member } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function member(over: Partial<Member>): Member {
  return {
    user_id: "leo_owner",
    display: "Leo",
    role: "owner",
    email: "leo@example.com",
    active: true,
    enrolled: true,
    channels: [],
    is_owner: true,
    invitation: null,
    ...over,
  };
}

const LEO = member({ channels: ["telegram:111", "discord:d1"] });
const MIA = member({
  user_id: "mia",
  display: "Mia",
  role: "member",
  is_owner: false,
});

type Handler = (
  url: string,
  init?: RequestInit,
) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;

function mockFetch(handler: Handler) {
  const calls: Array<[string, string]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push([init?.method ?? "GET", url]);
      const { status = 200, body } = await handler(url, init);
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    },
  );
  return calls;
}

const roster = (members: Member[]) => ({
  body: { configured: true, members, total: members.length, limit: 200, offset: 0 },
});

describe("telegramIds", () => {
  it("keeps only telegram handles, stripped of the platform prefix", () => {
    expect(telegramIds(["telegram:1", "discord:x", "telegram:22"])).toEqual(["1", "22"]);
  });
});

describe("TelegramLinks", () => {
  it("lists enrolled members with their Telegram ids", async () => {
    mockFetch(() => roster([LEO, MIA, member({ user_id: "ghost", enrolled: false })]));
    render(<TelegramLinks />);
    expect(await screen.findByText("Leo")).toBeTruthy();
    expect(screen.getByText("111")).toBeTruthy();
    expect(screen.queryByText("d1")).toBeNull();
    expect(screen.getByText("Mia")).toBeTruthy();
    expect(screen.getByText("No Telegram linked")).toBeTruthy();
    expect(document.querySelector('[data-member="ghost"]')).toBeNull();
    expect(document.querySelector('a[href="/users"]')).toBeNull();
  });

  it("links an id inline, disables the row while pending and updates the list", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const calls = mockFetch(async (_url, init) => {
      if (init?.method === "POST") {
        await gate;
        return { body: { ok: true, member: { user_id: "mia", channels: ["telegram:222"] } } };
      }
      return roster([LEO, MIA]);
    });
    render(<TelegramLinks />);
    const input = await screen.findByLabelText("Telegram user id for Mia");
    fireEvent.change(input, { target: { value: " 222 " } });
    const btn = input.closest("form")!.querySelector("button")!;
    fireEvent.click(btn);
    fireEvent.click(btn);
    await screen.findByText("Linking…");
    expect((input as HTMLInputElement).disabled).toBe(true);
    release();
    expect(await screen.findByText("222")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/222 now reaches Hermes as Mia/);
    const posts = calls.filter(([m]) => m === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][1]).toBe("/api/comms/members/mia/channels");
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("moves an id shown under another member when it is re-linked", async () => {
    mockFetch((_url, init) =>
      init?.method === "POST"
        ? { body: { ok: true, member: { user_id: "mia", channels: ["telegram:111"] } } }
        : roster([LEO, MIA]),
    );
    render(<TelegramLinks />);
    const input = await screen.findByLabelText("Telegram user id for Mia");
    fireEvent.change(input, { target: { value: "111" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => {
      expect(document.querySelector('[data-member="leo_owner"]')!.textContent).toContain(
        "No Telegram linked",
      );
      expect(document.querySelector('[data-member="mia"]')!.textContent).toContain("111");
    });
  });

  it("rejects a non-numeric id without calling the API", async () => {
    const calls = mockFetch(() => roster([MIA]));
    render(<TelegramLinks />);
    const input = await screen.findByLabelText("Telegram user id for Mia");
    fireEvent.change(input, { target: { value: "@mia" } });
    fireEvent.submit(input.closest("form")!);
    expect((await screen.findByRole("alert")).textContent).toMatch(/numeric/);
    expect(calls.filter(([m]) => m === "POST")).toHaveLength(0);
  });

  it("unlinks an id through DELETE and shows the API's error when it fails", async () => {
    let fail = false;
    const calls = mockFetch((_url, init) => {
      if (init?.method === "DELETE") {
        return fail
          ? { status: 403, body: { detail: "Only owner or admin may do this." } }
          : { body: { ok: true, member: { user_id: "leo_owner", channels: ["discord:d1"] } } };
      }
      return roster([LEO]);
    });
    render(<TelegramLinks />);
    fireEvent.click(await screen.findByLabelText("Unlink Telegram 111"));
    await waitFor(() => expect(screen.queryByText("111")).toBeNull());
    expect(screen.getByText("No Telegram linked")).toBeTruthy();
    const del = calls.find(([m]) => m === "DELETE")!;
    expect(del[1]).toBe(
      "/api/comms/members/leo_owner/channels?platform=telegram&channel_user_id=111",
    );

    cleanup();
    fail = true;
    render(<TelegramLinks />);
    fireEvent.click(await screen.findByLabelText("Unlink Telegram 111"));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Only owner or admin may do this.",
    );
    expect(screen.getByText("111")).toBeTruthy();
  });

  it("shows a load error with retry, and a read-only note for non-admins", async () => {
    const calls = mockFetch(() => ({ status: 500, body: { detail: "boom" } }));
    render(<TelegramLinks />);
    expect((await screen.findByRole("alert")).textContent).toContain("boom");
    fireEvent.click(screen.getByText("Retry"));
    await waitFor(() => expect(calls.length).toBe(2));

    cleanup();
    calls.length = 0;
    render(<TelegramLinks canManage={false} />);
    expect(screen.getByText(/Only the owner or an admin/)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });
});
