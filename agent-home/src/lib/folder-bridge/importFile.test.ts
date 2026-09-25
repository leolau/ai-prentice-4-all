import { describe, expect, it, vi } from "vitest";

import { importFile, readFileContent } from "@/lib/folder-bridge/fsOps";
import { snapshotFromFileList } from "@/lib/folder-bridge/snapshotHandle";

function fakeFile(
  relPath: string,
  bytes: Uint8Array | string,
  type = "",
): File {
  const name = relPath.split("/").pop() ?? relPath;
  const file = new File([bytes as BlobPart], name, {
    type,
    lastModified: 1_700_000_000_000,
  });
  Object.defineProperty(file, "webkitRelativePath", { value: relPath });
  return file;
}

async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// %PDF header followed by bytes that are not valid UTF-8 (0xff 0xfe, lone 0xc3).
const PDF_BYTES = new Uint8Array([
  0x25, 0x50, 0x44, 0x46, 0x2d, 0xff, 0xfe, 0x00, 0xc3, 0x0a,
]);

const root = () =>
  snapshotFromFileList([
    fakeFile("Invoices/2026-01.pdf", PDF_BYTES, "application/pdf"),
    fakeFile("Invoices/notes.txt", "héllo wörld"),
  ])!;

describe("readFileContent binary handling", () => {
  it("refuses non-UTF-8 files instead of decoding them lossily", async () => {
    const result = await readFileContent(root(), "2026-01.pdf", {
      maxBytes: 200_000,
    });
    expect(result).toEqual({
      content: "",
      truncated: false,
      size: 10,
      binary: true,
    });
  });

  it("still returns text and does not flag a truncated multi-byte tail as binary", async () => {
    const full = await readFileContent(root(), "notes.txt", {
      maxBytes: 200_000,
    });
    expect(full).toEqual({
      content: "héllo wörld",
      truncated: false,
      size: 13,
    });
    // "hé" is 3 bytes; a cut at 2 splits the é.
    const cut = await readFileContent(root(), "notes.txt", { maxBytes: 2 });
    expect(cut.binary).toBeUndefined();
    expect(cut.truncated).toBe(true);
  });
});

describe("importFile", () => {
  it("posts the original bytes as multipart and verifies the server hash", async () => {
    const expected = await sha256(PDF_BYTES);
    let received: FormData | null = null;
    const fetchImpl = vi.fn(
      async (_url: string | URL | Request, init?: RequestInit) => {
        received = init?.body as FormData;
        const file = received.get("file") as File;
        const digest = await sha256(new Uint8Array(await file.arrayBuffer()));
        return new Response(
          JSON.stringify({
            asset: { id: "asset-1", filename: file.name },
            sha256: digest,
            size: file.size,
            storage_bucket: "agent-home-media",
            storage_path: "u/folder-bridge/x-2026-01.pdf",
          }),
          { status: 200 },
        );
      },
    );

    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
    );

    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/files/import",
      expect.objectContaining({ method: "POST" }),
    );
    const form = received!;
    expect(form.get("sourcePath")).toBe("2026-01.pdf");
    expect(form.get("folderLabel")).toBe("Invoices");
    const sent = form.get("file") as File;
    expect(new Uint8Array(await sent.arrayBuffer())).toEqual(PDF_BYTES);
    expect(result).toEqual({
      folderId: "f1",
      path: "2026-01.pdf",
      assetId: "asset-1",
      filename: "2026-01.pdf",
      size: 10,
      sha256: expected,
      storageBucket: "agent-home-media",
      storagePath: "u/folder-bridge/x-2026-01.pdf",
      verified: true,
    });
  });

  it("reports verified=false when the stored hash differs from the original", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            asset: { id: "asset-2" },
            sha256: "00".repeat(32),
            size: 10,
          }),
          { status: 200 },
        ),
    );
    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
    );
    expect(result.verified).toBe(false);
  });

  it("surfaces the BFF's error detail", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: "too_large",
            detail: "File exceeds the 100 MB limit.",
          }),
          {
            status: 413,
          },
        ),
    );
    await expect(
      importFile(root(), "f1", "Invoices", "2026-01.pdf", fetchImpl),
    ).rejects.toThrow("File exceeds the 100 MB limit.");
  });
});
