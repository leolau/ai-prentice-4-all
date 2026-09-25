/**
 * BFF route tests for POST /api/files/import (Folder Bridge import).
 *
 * The contract that matters: the bytes handed to Storage are exactly the
 * bytes posted (binary-safe), the returned sha256 is of those bytes, and the
 * registry row is mandatory (a failed registration is a failed import).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/files/import/route";
import type { Principal } from "@/types";

const getPrincipal = vi.fn<() => Promise<Principal | null>>();
const storageAvailable = vi.fn(() => true);
const uploadChatMedia = vi.fn(
  async (
    _principal: Principal,
    sessionId: string,
    file: { name: string; contentType: string; bytes: ArrayBuffer },
  ) => ({
    path: `mia/${sessionId}/uuid-${file.name}`,
    name: file.name,
    content_type: file.contentType,
    size: file.bytes.byteLength,
  }),
);
const registerFile = vi.fn(async (payload: Record<string, unknown>) => ({
  id: "asset-1",
  ...payload,
}));

vi.mock("@/lib/auth/principal", () => ({
  getPrincipal: () => getPrincipal(),
  apiClientForRequest: async () => ({ registerFile }),
}));
vi.mock("@/lib/env", () => ({ mediaBucket: () => "agent-home-media" }));
vi.mock("@/lib/supabase/storage", () => ({
  storageAvailable: () => storageAvailable(),
  uploadChatMedia: (...args: unknown[]) =>
    (uploadChatMedia as (...a: unknown[]) => unknown)(...args),
}));
vi.mock("@/lib/chat/upload-limit", () => ({
  uploadMaxBytes: () => 64,
  uploadTooLargeDetail: (n: number) => `File exceeds the ${n} B limit.`,
}));

const principal: Principal = {
  user_id: "mia",
  display: "Mia",
  role: "member",
  channels: [],
  is_owner: false,
};

const PDF_BYTES = new Uint8Array([
  0x25, 0x50, 0x44, 0x46, 0x2d, 0xff, 0xfe, 0x00, 0xc3, 0x0a,
]);

async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function post(body: BodyInit): Promise<Response> {
  return POST(
    new Request("http://home.test/api/files/import", { method: "POST", body }),
  ) as unknown as Promise<Response>;
}

function formWith(file?: File): FormData {
  const form = new FormData();
  if (file) form.set("file", file);
  form.set("sourcePath", "2026/invoice.pdf");
  form.set("folderLabel", "Invoices");
  return form;
}

describe("POST /api/files/import", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPrincipal.mockResolvedValue(principal);
    storageAvailable.mockReturnValue(true);
  });

  it("401s when unauthenticated", async () => {
    getPrincipal.mockResolvedValue(null);
    const res = await post(formWith(new File([PDF_BYTES], "a.pdf")));
    expect(res.status).toBe(401);
    expect(uploadChatMedia).not.toHaveBeenCalled();
  });

  it("501s when storage is not configured", async () => {
    storageAvailable.mockReturnValue(false);
    const res = await post(formWith(new File([PDF_BYTES], "a.pdf")));
    expect(res.status).toBe(501);
  });

  it("400s without a file field", async () => {
    const res = await post(formWith());
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "missing_file" });
  });

  it("413s over the size cap", async () => {
    const res = await post(formWith(new File([new Uint8Array(65)], "big.bin")));
    expect(res.status).toBe(413);
    expect(uploadChatMedia).not.toHaveBeenCalled();
  });

  it("stores the exact bytes, registers the row, and returns the hash", async () => {
    const res = await post(
      formWith(
        new File([PDF_BYTES], "invoice.pdf", { type: "application/pdf" }),
      ),
    );
    expect(res.status).toBe(200);
    const body = await res.json();
    const expected = await sha256(PDF_BYTES);

    const stored = uploadChatMedia.mock.calls[0];
    expect(stored[1]).toBe("folder-bridge");
    expect(new Uint8Array(stored[2].bytes)).toEqual(PDF_BYTES);

    expect(registerFile).toHaveBeenCalledWith({
      filename: "invoice.pdf",
      content_type: "application/pdf",
      byte_size: 10,
      sha256: expected,
      storage_bucket: "agent-home-media",
      storage_path: "mia/folder-bridge/uuid-invoice.pdf",
      conversation: "Invoices/2026/invoice.pdf",
    });
    expect(body).toMatchObject({
      asset: { id: "asset-1" },
      sha256: expected,
      size: 10,
      storage_bucket: "agent-home-media",
      storage_path: "mia/folder-bridge/uuid-invoice.pdf",
    });
  });

  it("502s when registration fails (a stored-but-unregistered file is not an import)", async () => {
    registerFile.mockRejectedValueOnce(new Error("registry down"));
    const res = await post(formWith(new File([PDF_BYTES], "a.pdf")));
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({
      error: "import_failed",
      detail: "registry down",
    });
  });
});
