import { afterEach, describe, expect, it, vi } from "vitest";

import {
  __setImportChunkSizeForTest,
  __setStreamingBodySupportForTest,
  importFile,
  readFileContent,
} from "@/lib/folder-bridge/fsOps";
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
  afterEach(() => {
    __setStreamingBodySupportForTest(undefined);
  });

  it("posts the file as the raw body and trusts the server hash", async () => {
    __setStreamingBodySupportForTest(true);
    const expected = await sha256(PDF_BYTES);
    let receivedBody: BodyInit | null = null;
    let receivedHeaders: Headers | null = null;
    const fetchImpl = vi.fn(
      async (_url: string | URL | Request, init?: RequestInit) => {
        receivedBody = init?.body ?? null;
        receivedHeaders = new Headers(init?.headers);
        return new Response(
          JSON.stringify({
            asset: { id: "asset-1", filename: "2026-01.pdf" },
            sha256: expected,
            size: 10,
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
    // The body is a stream of the file's bytes (through a byte-counting
    // TransformStream for upload progress), not the bare File and not
    // buffered — read it back to confirm the bytes are untouched.
    expect(receivedBody).toBeInstanceOf(ReadableStream);
    const chunks: Uint8Array[] = [];
    const reader = (receivedBody as unknown as ReadableStream<Uint8Array>).getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
    const sentBytes = new Uint8Array(chunks.reduce((n, c) => n + c.byteLength, 0));
    let offset = 0;
    for (const chunk of chunks) {
      sentBytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    expect(sentBytes).toEqual(PDF_BYTES);
    // Metadata travels in headers, not multipart form fields.
    expect(receivedHeaders!.get("x-file-name")).toBe("2026-01.pdf");
    expect(receivedHeaders!.get("x-source-path")).toBe("2026-01.pdf");
    expect(receivedHeaders!.get("x-folder-label")).toBe("Invoices");
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

  it("reports verified=true when the server confirms the upload", async () => {
    __setStreamingBodySupportForTest(true);
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
    expect(result.verified).toBe(true);
  });

  it("surfaces the BFF's error detail", async () => {
    __setStreamingBodySupportForTest(true);
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

  it("falls back to posting the bare File when the browser can't stream a request body (Safari/WebKit)", async () => {
    // Safari throws `NotSupportedError: "ReadableStream uploading is not
    // supported"` for any streaming fetch body, regardless of file size —
    // this must never be the path that breaks an import.
    __setStreamingBodySupportForTest(false);
    const expected = await sha256(PDF_BYTES);
    let receivedBody: BodyInit | null = null;
    let duplexSeen = false;
    const fetchImpl = vi.fn(
      async (_url: string | URL | Request, init?: RequestInit) => {
        receivedBody = init?.body ?? null;
        duplexSeen = "duplex" in (init ?? {});
        return new Response(
          JSON.stringify({
            asset: { id: "asset-1", filename: "2026-01.pdf" },
            sha256: expected,
            size: 10,
          }),
          { status: 200 },
        );
      },
    );

    const progressCalls: Array<[number, number]> = [];
    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
      (sent, total) => progressCalls.push([sent, total]),
    );

    expect(receivedBody).toBeInstanceOf(File);
    expect(duplexSeen).toBe(false);
    expect(result.verified).toBe(true);
    // No live progress on this path — just an initial 0% and a final 100%.
    expect(progressCalls).toEqual([
      [0, 10],
      [10, 10],
    ]);
  });
});

describe("importFile (chunked)", () => {
  afterEach(() => {
    __setImportChunkSizeForTest(undefined);
    __setStreamingBodySupportForTest(undefined);
  });

  function chunkServer(opts: {
    overrides?: Record<number, () => Promise<Response> | Response>;
    startReceived?: number;
  } = {}) {
    const calls: Array<{ offset: number; total: number; size: number }> = [];
    let received = opts.startReceived ?? 0;
    const fetchImpl = vi.fn(
      async (_url: string | URL | Request, init?: RequestInit) => {
        const h = new Headers(init?.headers);
        const offset = Number(h.get("x-import-offset"));
        const total = Number(h.get("x-import-total"));
        const blob = init?.body as Blob | null;
        const size = blob?.size ?? 0;
        calls.push({ offset, total, size });
        const override = opts.overrides?.[calls.length - 1];
        if (override) return override();
        if (offset !== received) {
          return new Response(
            JSON.stringify({ error: "offset_mismatch", received }),
            { status: 409 },
          );
        }
        received += size;
        if (offset < total) {
          return new Response(JSON.stringify({ received }), { status: 200 });
        }
        return new Response(
          JSON.stringify({
            asset: { id: "asset-9", filename: "2026-01.pdf" },
            sha256: "ab".repeat(32),
            size: total,
            storage_bucket: "agent-home-media",
            storage_path: "u/folder-bridge/x-2026-01.pdf",
          }),
          { status: 200 },
        );
      },
    );
    return { calls, fetchImpl };
  }

  it("uploads sequential chunks then finalizes with an empty request", async () => {
    __setImportChunkSizeForTest(4); // 10-byte file → chunks at 0, 4, 8
    const { calls, fetchImpl } = chunkServer();
    const progressCalls: Array<[number, number]> = [];

    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
      (s, t) => progressCalls.push([s, t]),
    );

    expect(calls.map((c) => [c.offset, c.size])).toEqual([
      [0, 4],
      [4, 4],
      [8, 2],
      [10, 0], // finalize
    ]);
    expect(result.assetId).toBe("asset-9");
    expect(result.sha256).toBe("ab".repeat(32));
    expect(progressCalls.at(-1)).toEqual([10, 10]);
  });

  it("resyncs to the server's byte count on a 409 instead of re-sending", async () => {
    __setImportChunkSizeForTest(4);
    // Simulate a previous attempt that already committed 8 bytes server-side.
    const { calls, fetchImpl } = chunkServer({ startReceived: 8 });

    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
    );

    // After the resync the client jumps to offset 8 — no re-send of 0.
    expect(calls.map((c) => [c.offset, c.size])).toEqual([
      [0, 4],
      [8, 2],
      [10, 0],
    ]);
    expect(result.assetId).toBe("asset-9");
  });

  it("retries a chunk after a network error", async () => {
    __setImportChunkSizeForTest(4);
    const { calls, fetchImpl } = chunkServer({
      overrides: { 0: () => Promise.reject(new Error("socket reset")) },
    });

    const result = await importFile(
      root(),
      "f1",
      "Invoices",
      "2026-01.pdf",
      fetchImpl,
    );

    // First call failed, retried at the same offset.
    expect(calls[0].offset).toBe(0);
    expect(calls[1].offset).toBe(0);
    expect(result.assetId).toBe("asset-9");
  });
});
