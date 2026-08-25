// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectedAccounts } from "@/components/settings/ConnectedAccounts";

afterEach(cleanup);

const ENTRY = {
  owner_user_id: "alice",
  provider: "google",
  name: "alice@gmail.com",
  kind: "google-oauth2",
  visibility: "private:alice",
  services: ["calendar"],
  payload: { client_id: "cid" },
  created_at: null,
  updated_at: null,
};

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      return new Response(JSON.stringify(handler(url, init)), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
}

describe("ConnectedAccounts", () => {
  it("lists the caller's entries with a disconnect action", async () => {
    mockFetch(() => ({ credentials: [ENTRY] }));
    render(<ConnectedAccounts />);
    expect(await screen.findByText(/alice@gmail\.com/)).toBeTruthy();
    expect(screen.getByText("Disconnect")).toBeTruthy();
  });

  it("runs the consent flow end to end", async () => {
    const calls: Array<[string, string]> = [];
    mockFetch((url, init) => {
      const method = init?.method ?? "GET";
      calls.push([method, url]);
      if (url.endsWith("/api/credentials/google/start")) {
        return { auth_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1", state: "s" };
      }
      if (url.endsWith("/api/credentials/google/complete")) {
        return {
          credential: { ...ENTRY, services: ["calendar", "email"] },
          account_email: "alice@gmail.com",
          granted_scopes: ["https://mail.google.com/"],
        };
      }
      return { credentials: [] };
    });
    render(<ConnectedAccounts />);
    fireEvent.click(await screen.findByText("Connect Google account"));
    const link = await screen.findByText("consent link");
    expect(link.getAttribute("href")).toContain("accounts.google.com");
    fireEvent.change(screen.getByPlaceholderText("paste code or redirect URL"), {
      target: { value: "http://localhost:1/?code=abc&state=s" },
    });
    fireEvent.click(screen.getByText("Complete"));
    await waitFor(() =>
      expect(screen.getByText(/Connected alice@gmail\.com/)).toBeTruthy(),
    );
    expect(calls.some(([m, u]) => m === "POST" && u.endsWith("/google/start"))).toBe(true);
    expect(
      calls.some(([m, u]) => m === "POST" && u.endsWith("/google/complete")),
    ).toBe(true);
  });

  it("patches services when a toggle flips", async () => {
    const patches: unknown[] = [];
    mockFetch((_url, init) => {
      if (init?.method === "PATCH") {
        patches.push(JSON.parse(String(init.body)));
        return { credential: ENTRY };
      }
      return { credentials: [ENTRY] };
    });
    render(<ConnectedAccounts />);
    await screen.findByText(/alice@gmail\.com/);
    const emailToggle = screen.getAllByRole("checkbox")[0];
    fireEvent.click(emailToggle);
    await waitFor(() =>
      expect(patches).toEqual([{ services: ["calendar", "email"] }]),
    );
  });
});
