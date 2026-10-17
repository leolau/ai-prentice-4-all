// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  VpnConnections,
  buildPayload,
  formatLocalTime,
  groupFingerprint,
  parseRoutes,
} from "@/components/settings/VpnConnections";
import type { VpnView } from "@/types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SHA = "9f2c7a41b0e5d3c86a1f04e2b97d58c3e0a4b6f1c2d39e875b0a6c4d2e1f3a90";

function vpn(
  over: Omit<Partial<VpnView>, "status"> & { status?: Partial<VpnView["status"]> } = {},
): VpnView {
  const { status, ...rest } = over;
  return {
    name: "Office files",
    slug: "office-files",
    host: "vpn.example-office.com",
    port: 443,
    realm: null,
    routes: ["10.20.0.0/16"],
    share_path: "\\\\fileserver01\\Shared",
    mount_path: "/opt/data/vpn-files/office-files",
    auto_connect: true,
    idle_minutes: 30,
    visibility: "private:alice",
    owner_user_id: "alice",
    trusted_cert: null,
    ...rest,
    status: {
      state: "disconnected",
      since: null,
      local_ip: null,
      error: null,
      last_used: null,
      last_check: null,
      ...status,
    },
  };
}

const STORED = {
  host: "vpn.example-office.com",
  port: 443,
  username: "leolau",
  routes: ["10.20.0.0/16"],
  share_path: "\\\\fileserver01\\Shared",
  trusted_cert: SHA,
  auto_connect: true,
  idle_minutes: 45,
  share_domain: "OFFICE",
};

type Call = { method: string; url: string; body: unknown };

function mockApi(
  list: VpnView[],
  handlers: Partial<Record<string, (call: Call) => unknown>> = {},
) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const call = { method, url, body };
      calls.push(call);
      const key = `${method} ${url}`;
      const handler = handlers[key];
      let data: unknown;
      if (handler) data = handler(call);
      else if (url === "/api/vpn") data = { vpns: list };
      else if (method === "GET" && url.startsWith("/api/credentials/fortinet/")) {
        data = { credential: { payload: STORED } };
      } else if (method === "POST" && url.startsWith("/api/vpn/")) {
        data = { vpn: list[0] };
      } else data = {};
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  );
  return calls;
}

describe("parseRoutes", () => {
  it("accepts comma/newline separated CIDRs and bare hosts", () => {
    expect(parseRoutes("10.20.0.0/16,\n192.168.50.0/24 10.1.2.3")).toEqual({
      routes: ["10.20.0.0/16", "192.168.50.0/24", "10.1.2.3/32"],
      error: null,
    });
  });

  it.each([
    ["", /at least one/],
    ["0.0.0.0/0", /all traffic/],
    ["10.0.0.0/0", /all traffic/],
    ["10.20.1.0/16", /did you mean 10\.20\.0\.0\/16/],
    ["300.1.1.1/32", /not a valid/],
    ["10.0.0.0/33", /not a valid/],
    ["fileserver01", /not an IPv4 range/],
    ["fd00::/8", /not an IPv4 range/],
  ])("rejects %j", (text, msg) => {
    const { routes, error } = parseRoutes(text);
    expect(routes).toEqual([]);
    expect(error).toMatch(msg);
  });
});

describe("helpers", () => {
  it("groups the fingerprint in 16-char chunks", () => {
    expect(groupFingerprint(SHA)).toBe(
      "9f2c7a41b0e5d3c8 6a1f04e2b97d58c3 e0a4b6f1c2d39e87 5b0a6c4d2e1f3a90",
    );
    expect(groupFingerprint("AB:CD")).toBe("abcd");
  });

  it("shows time only for today, date + time otherwise", () => {
    const now = new Date(2026, 9, 16, 12, 0);
    expect(formatLocalTime(new Date(2026, 9, 16, 10, 42).toISOString(), now)).toMatch(
      /^10:42( \S+)?$/,
    );
    expect(formatLocalTime(new Date(2026, 9, 1, 16, 20).toISOString(), now)).toMatch(
      /^2026-10-01 16:20( \S+)?$/,
    );
    expect(formatLocalTime(null, now)).toBeNull();
  });

  it("keeps unshown stored fields and drops blank secrets", () => {
    const payload = buildPayload(
      {
        name: "Office files",
        host: "vpn.example-office.com",
        port: "443",
        username: "leolau",
        password: "",
        realm: "",
        routes: "10.20.0.0/16",
        share_path: "",
        share_username: "",
        share_password: "",
        visibility: "private",
      },
      { ...STORED, realm: "old", password: "leaked?" },
    );
    expect(payload).not.toHaveProperty("password");
    expect(payload).not.toHaveProperty("share_password");
    expect(payload).not.toHaveProperty("realm");
    expect(payload).not.toHaveProperty("share_path");
    expect(payload.trusted_cert).toBe(SHA);
    expect(payload.idle_minutes).toBe(45);
    expect(payload.share_domain).toBe("OFFICE");
  });
});

describe("VpnConnections", () => {
  it("renders a connected VPN with routes, mount and last check", async () => {
    mockApi([
      vpn({
        status: {
          state: "connected",
          since: new Date().toISOString(),
          local_ip: "10.212.134.5",
          last_check: {
            at: new Date().toISOString(),
            ok: true,
            message: "File server reachable, 1,284 files listed.",
            file_count: 1284,
          },
        },
      }),
    ]);
    render(<VpnConnections />);
    const card = await screen.findByText(/Office files/, { selector: "span" });
    const li = card.closest("li")!;
    expect(li.getAttribute("data-state")).toBe("connected");
    const q = within(li);
    expect(q.getByText(/Connected/)).toBeTruthy();
    expect(li.textContent).toContain("address 10.212.134.5");
    expect(q.getByText("10.20.0.0/16")).toBeTruthy();
    expect(q.getByText("/opt/data/vpn-files/office-files")).toBeTruthy();
    expect(li.textContent).toContain("File server reachable, 1,284 files listed.");
    expect(q.getByText("Disconnect")).toBeTruthy();
    expect(q.getByText("Test again")).toBeTruthy();
    expect(q.getByText("Edit")).toBeTruthy();
    expect(li.textContent).toContain("Disconnects after 30 minutes");
  });

  it("shows the last error in red and a Connect button", async () => {
    mockApi([
      vpn({
        name: "Supplier portal",
        slug: "supplier-portal",
        status: { state: "error", error: "Login failed: wrong username or password" },
      }),
    ]);
    render(<VpnConnections />);
    const err = await screen.findByText(/Login failed: wrong username or password/);
    expect(err.className).toContain("text-red-400");
    expect(screen.getByText("Connect")).toBeTruthy();
    expect(screen.getByText(/Error/)).toBeTruthy();
  });

  it("connects and shows the connecting state", async () => {
    const connecting = vpn({ status: { state: "connecting" } });
    const calls = mockApi([vpn()], {
      "POST /api/vpn/Office%20files/connect": () => ({ vpn: connecting }),
    });
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("Connect"));
    await waitFor(() => expect(screen.getByText(/○ Connecting…/)).toBeTruthy());
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/vpn/Office%20files/connect")).toBe(true);
  });

  it("polls only every 30 s when nothing is connecting", async () => {
    vi.useFakeTimers();
    try {
      const calls = mockApi([vpn()]);
      render(<VpnConnections />);
      await vi.advanceTimersByTimeAsync(10);
      const first = calls.filter((c) => c.url === "/api/vpn").length;
      await vi.advanceTimersByTimeAsync(29_000);
      expect(calls.filter((c) => c.url === "/api/vpn").length).toBe(first);
      await vi.advanceTimersByTimeAsync(1_000);
      expect(calls.filter((c) => c.url === "/api/vpn").length).toBe(first + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("validates the add form before saving", async () => {
    const calls = mockApi([]);
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("+ Add a Fortinet VPN"));
    const form = screen.getByText("Add a Fortinet VPN").closest("div")!;
    const q = within(form);
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/Give the VPN a name/)).toBeTruthy();

    fireEvent.change(q.getByPlaceholderText("Office files"), { target: { value: "Office files" } });
    fireEvent.change(q.getByPlaceholderText("vpn.example-office.com"), {
      target: { value: "https://vpn.example.com/remote" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/no https:\/\//)).toBeTruthy();

    fireEvent.change(q.getByPlaceholderText("vpn.example-office.com"), {
      target: { value: "vpn.example.com" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/VPN username/)).toBeTruthy();

    fireEvent.change(q.getByText("Username").parentElement!.querySelector("input")!, {
      target: { value: "leolau" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/VPN password/)).toBeTruthy();

    fireEvent.change(q.getByText("Password").parentElement!.querySelector("input")!, {
      target: { value: "pw" },
    });
    fireEvent.change(q.getByPlaceholderText("10.20.0.0/16, 192.168.50.0/24"), {
      target: { value: "0.0.0.0/0" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/all traffic/)).toBeTruthy();

    fireEvent.change(q.getByPlaceholderText("10.20.0.0/16, 192.168.50.0/24"), {
      target: { value: "10.20.0.0/16" },
    });
    fireEvent.change(q.getByPlaceholderText("\\\\fileserver01\\Shared"), {
      target: { value: "fileserver01/Shared" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    expect(q.getByText(/Windows share like/)).toBeTruthy();

    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("saves a new VPN then tests it", async () => {
    const calls = mockApi([], {
      "POST /api/vpn/Office%20files/test": () => ({
        vpn: vpn({ status: { state: "error", error: "Login failed: wrong username or password" } }),
      }),
    });
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("+ Add a Fortinet VPN"));
    const form = screen.getByText("Add a Fortinet VPN").closest("div")!;
    const q = within(form);
    fireEvent.change(q.getByPlaceholderText("Office files"), { target: { value: "Office files" } });
    fireEvent.change(q.getByPlaceholderText("vpn.example-office.com"), {
      target: { value: "vpn.example-office.com" },
    });
    fireEvent.change(q.getByText("Username").parentElement!.querySelector("input")!, {
      target: { value: "leolau" },
    });
    fireEvent.change(q.getByText("Password").parentElement!.querySelector("input")!, {
      target: { value: "s3cret" },
    });
    fireEvent.change(q.getByPlaceholderText("10.20.0.0/16, 192.168.50.0/24"), {
      target: { value: "10.20.0.0/16\n192.168.50.0/24" },
    });
    fireEvent.change(q.getByPlaceholderText("\\\\fileserver01\\Shared"), {
      target: { value: "\\\\fileserver01\\Shared" },
    });
    fireEvent.click(q.getByText("Save & test connection"));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/test"))).toBe(true),
    );
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/credentials/fortinet/Office%20files");
    expect(put.body).toEqual({
      kind: "fortinet-vpn",
      visibility: "private",
      payload: {
        host: "vpn.example-office.com",
        port: 443,
        username: "leolau",
        password: "s3cret",
        routes: ["10.20.0.0/16", "192.168.50.0/24"],
        share_path: "\\\\fileserver01\\Shared",
        auto_connect: true,
        idle_minutes: 30,
      },
    });
    const putIdx = calls.indexOf(put);
    const testIdx = calls.findIndex((c) => c.url === "/api/vpn/Office%20files/test");
    expect(testIdx).toBeGreaterThan(putIdx);
    // The test result (a failed login) shows on the new card.
    expect(await screen.findByText(/Login failed: wrong username or password/)).toBeTruthy();
  });

  it("Edit pre-fills, hides the name and sends no secrets when left blank", async () => {
    const calls = mockApi([vpn()]);
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("Edit"));
    const header = await screen.findByText('Edit "Office files"');
    const form = header.closest("div")!;
    const q = within(form);
    expect(q.queryByPlaceholderText("Office files")).toBeNull();
    const user = q.getByText("Username").parentElement!.querySelector("input")!;
    expect(user.value).toBe("leolau");
    const pw = q.getByText("Password").parentElement!.querySelector("input")!;
    expect(pw.value).toBe("");
    expect(pw.getAttribute("placeholder")).toBe("unchanged");
    expect(q.getByLabelText("Share password").getAttribute("placeholder")).toBe("unchanged");
    fireEvent.click(q.getByText("Save & test connection"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    const payload = (put.body as { payload: Record<string, unknown> }).payload;
    expect(payload).not.toHaveProperty("password");
    expect(payload).not.toHaveProperty("share_password");
    expect(payload.username).toBe("leolau");
    expect(payload.trusted_cert).toBe(SHA);
    expect(payload.idle_minutes).toBe(45);
  });

  it("saves the auto-connect toggle without secrets", async () => {
    const calls = mockApi([vpn()]);
    render(<VpnConnections />);
    const box = (await screen.findByText(/Let agents connect on their own/))
      .closest("label")!
      .querySelector("input")!;
    fireEvent.click(box);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    const body = put.body as { kind: string; visibility: string; payload: Record<string, unknown> };
    expect(body.kind).toBe("fortinet-vpn");
    expect(body.visibility).toBe("private:alice");
    expect(body.payload.auto_connect).toBe(false);
    expect(body.payload).not.toHaveProperty("password");
  });

  it("shows the certificate for approval and POSTs trust with its fingerprint", async () => {
    const cert = {
      subject: "FGT60F-Office",
      issuer: "Fortinet_CA_SSL",
      not_after: "2027-03-31T00:00:00Z",
      sha256: SHA,
    };
    const calls = mockApi([vpn({ status: { state: "needs_cert", cert } })]);
    render(<VpnConnections />);
    expect(await screen.findByText(/Trust this VPN gateway/)).toBeTruthy();
    expect(screen.getByText("FGT60F-Office")).toBeTruthy();
    expect(screen.getByText("Fortinet_CA_SSL")).toBeTruthy();
    expect(screen.getByText("2027-03-31")).toBeTruthy();
    expect(screen.getByText(groupFingerprint(SHA))).toBeTruthy();
    expect(screen.getByText("vpn.example-office.com:443")).toBeTruthy();
    fireEvent.click(screen.getByText("Trust and connect"));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/vpn/Office%20files/trust")).toBe(true),
    );
    const trust = calls.find((c) => c.url === "/api/vpn/Office%20files/trust")!;
    expect(trust.method).toBe("POST");
    expect(trust.body).toEqual({ sha256: SHA });
  });

  it("Don't trust dismisses the panel without calling Hermes", async () => {
    const cert = { subject: "x", issuer: "y", not_after: null, sha256: SHA };
    const calls = mockApi([vpn({ status: { state: "needs_cert", cert } })]);
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("Don't trust"));
    expect(screen.queryByText(/Trust this VPN gateway/)).toBeNull();
    expect(screen.getByText(/certificate isn.t trusted/)).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/trust"))).toBe(false);
  });

  it("removes after confirmation", async () => {
    const calls = mockApi([vpn()]);
    render(<VpnConnections />);
    fireEvent.click(await screen.findByText("Remove"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByText("Remove"));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "DELETE" && c.url === "/api/credentials/fortinet/Office%20files",
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Edit")).toBeNull());
  });

  it("polls every 5 s while connecting", async () => {
    // Only the poll interval is faked: the first load (setTimeout + fetch
    // body read) must really finish so the component sees "connecting".
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const calls = mockApi([vpn({ status: { state: "connecting" } })]);
      render(<VpnConnections />);
      for (let i = 0; i < 20 && !screen.queryByText(/○ Connecting…/); i++) {
        await new Promise((r) => setTimeout(r, 10));
      }
      expect(screen.getByText(/○ Connecting…/)).toBeTruthy();
      const first = calls.filter((c) => c.url === "/api/vpn").length;
      await vi.advanceTimersByTimeAsync(5_000);
      expect(calls.filter((c) => c.url === "/api/vpn").length).toBe(first + 1);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(calls.filter((c) => c.url === "/api/vpn").length).toBe(first + 2);
    } finally {
      vi.useRealTimers();
    }
  });
});
