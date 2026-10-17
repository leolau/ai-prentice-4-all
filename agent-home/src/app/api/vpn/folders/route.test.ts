/**
 * BFF route tests for Files → Bridge → Network folders: every route needs a
 * principal, forwards only the contract's fields under the bridged client,
 * passes Hermes' HTTP errors (400 validation, 404 another user's folder)
 * through, and keeps a failed test (200 + last_check.ok false) a success.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST as actionPOST } from "@/app/api/vpn/folders/[name]/[action]/route";
import { DELETE, PUT } from "@/app/api/vpn/folders/[name]/route";
import { GET, POST } from "@/app/api/vpn/folders/route";
import { HermesApiError } from "@/lib/api/client";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const networkFolders = vi.fn();
const createNetworkFolder = vi.fn();
const updateNetworkFolder = vi.fn();
const deleteNetworkFolder = vi.fn();
const networkFolderAction = vi.fn();

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({
    networkFolders,
    createNetworkFolder,
    updateNetworkFolder,
    deleteNetworkFolder,
    networkFolderAction,
  }),
}));

const ALICE: Principal = {
  user_id: "alice",
  display: "Alice",
  role: "member",
  channels: [],
  is_owner: false,
};

const VIEW = {
  id: "net-tender-documents",
  name: "Tender documents",
  vpn: "Office files",
  status: {
    vpn_state: "connected",
    mounted: false,
    error: "File share login failed: check the share username and password",
    last_check: { at: "2026-10-17T02:42:00Z", ok: false, file_count: null, message: "x" },
  },
};

const BODY = {
  name: "Tender documents",
  vpn: "Office files",
  share_path: "//fs01.office.local/Projects/Tenders",
  trusted_for_import: true,
};

function json(method: string, body: unknown): Request {
  return new Request("http://localhost/api/vpn/folders", {
    method,
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

const nameCtx = (name: string) => ({ params: Promise.resolve({ name }) });
const actionCtx = (name: string, action: string) => ({
  params: Promise.resolve({ name, action }),
});

beforeEach(() => {
  vi.clearAllMocks();
  getPrincipal.mockResolvedValue(ALICE);
});

describe("auth", () => {
  it("401s every route when unauthenticated and never calls Hermes", async () => {
    getPrincipal.mockResolvedValue(null);
    const responses = await Promise.all([
      GET(),
      POST(json("POST", BODY)),
      PUT(json("PUT", BODY), nameCtx("Tender documents")),
      DELETE(json("DELETE", {}), nameCtx("Tender documents")),
      actionPOST(json("POST", {}), actionCtx("Tender documents", "test")),
    ]);
    expect(responses.map((r) => r.status)).toEqual([401, 401, 401, 401, 401]);
    for (const fn of [
      networkFolders,
      createNetworkFolder,
      updateNetworkFolder,
      deleteNetworkFolder,
      networkFolderAction,
    ]) {
      expect(fn).not.toHaveBeenCalled();
    }
  });
});

describe("GET /api/vpn/folders", () => {
  it("proxies the caller's folders", async () => {
    networkFolders.mockResolvedValue({ folders: [VIEW] });
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ folders: [VIEW] });
  });

  it("502s when Hermes is unreachable", async () => {
    networkFolders.mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await GET();
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("api_unreachable");
  });
});

describe("POST /api/vpn/folders", () => {
  it("forwards only contract fields and answers 201", async () => {
    createNetworkFolder.mockResolvedValue(VIEW);
    const res = await POST(
      json("POST", {
        ...BODY,
        share_username: " alice ",
        share_password: "s3cret",
        share_domain: "",
        owner_user_id: "mallory",
        visibility: "shared",
      }),
    );
    expect(res.status).toBe(201);
    expect(createNetworkFolder).toHaveBeenCalledWith({
      ...BODY,
      share_username: "alice",
      share_password: "s3cret",
      share_domain: null,
    });
  });

  it.each([
    ["not json", "{"],
    ["an array", []],
    ["no name", { ...BODY, name: "  " }],
    ["no vpn", { ...BODY, vpn: undefined }],
    ["no share path", { ...BODY, share_path: "" }],
    ["a non-string username", { ...BODY, share_username: 7 }],
    ["a non-string password", { ...BODY, share_password: { a: 1 } }],
    ["a non-boolean trust flag", { ...BODY, trusted_for_import: "yes" }],
  ])("400s %s without calling Hermes", async (_label, body) => {
    const res = await POST(json("POST", body));
    expect(res.status).toBe(400);
    expect(createNetworkFolder).not.toHaveBeenCalled();
  });

  it("passes Hermes' validation 400 through with its detail", async () => {
    createNetworkFolder.mockRejectedValue(
      new HermesApiError(400, "File server 10.30.0.5 is outside the VPN routes"),
    );
    const res = await POST(json("POST", BODY));
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toContain("outside the VPN routes");
  });
});

describe("PUT/DELETE /api/vpn/folders/{name}", () => {
  it("forwards an edit to the old name, dropping an empty password", async () => {
    updateNetworkFolder.mockResolvedValue({ ...VIEW, name: "Tenders 2026" });
    const res = await PUT(
      json("PUT", { ...BODY, name: "Tenders 2026", share_password: "" }),
      nameCtx("Tender documents"),
    );
    expect(res.status).toBe(200);
    expect(updateNetworkFolder).toHaveBeenCalledWith("Tender documents", {
      ...BODY,
      name: "Tenders 2026",
    });
    expect(updateNetworkFolder.mock.calls[0][1]).not.toHaveProperty("share_password");
  });

  it("400s a malformed edit", async () => {
    const res = await PUT(json("PUT", { name: "x" }), nameCtx("Tender documents"));
    expect(res.status).toBe(400);
    expect(updateNetworkFolder).not.toHaveBeenCalled();
  });

  it("passes another user's folder 404 through", async () => {
    updateNetworkFolder.mockRejectedValue(new HermesApiError(404, "unknown network folder"));
    const res = await PUT(json("PUT", BODY), nameCtx("Bob's share"));
    expect(res.status).toBe(404);
  });

  it("deletes", async () => {
    deleteNetworkFolder.mockResolvedValue({ ok: true });
    const res = await DELETE(json("DELETE", {}), nameCtx("Tender documents"));
    expect(res.status).toBe(200);
    expect(deleteNetworkFolder).toHaveBeenCalledWith("Tender documents");
    expect(await res.json()).toEqual({ ok: true });
  });

  it("passes a delete 404 through", async () => {
    deleteNetworkFolder.mockRejectedValue(new HermesApiError(404, "unknown network folder"));
    const res = await DELETE(json("DELETE", {}), nameCtx("Nope"));
    expect(res.status).toBe(404);
  });
});

describe("POST /api/vpn/folders/{name}/{action}", () => {
  it.each(["test", "disconnect"])("forwards %s", async (action) => {
    networkFolderAction.mockResolvedValue(VIEW);
    const res = await actionPOST(json("POST", {}), actionCtx("Tender documents", action));
    expect(res.status).toBe(200);
    expect(networkFolderAction).toHaveBeenCalledWith("Tender documents", action);
    // A failed test is a 200 carrying the failure, not an HTTP error.
    expect((await res.json()).status.last_check.ok).toBe(false);
  });

  it("404s an unknown action without calling Hermes", async () => {
    const res = await actionPOST(json("POST", {}), actionCtx("Tender documents", "mount"));
    expect(res.status).toBe(404);
    expect(networkFolderAction).not.toHaveBeenCalled();
  });

  it("502s when Hermes is unreachable", async () => {
    networkFolderAction.mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await actionPOST(json("POST", {}), actionCtx("Tender documents", "test"));
    expect(res.status).toBe(502);
  });
});
