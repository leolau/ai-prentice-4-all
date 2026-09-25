/**
 * POST /api/files/import — Folder Bridge import (byte-faithful copy of one
 * local file into the file store).
 *
 * The browser posts the raw `File` as the request body (not multipart) with
 * metadata in headers (`x-file-name`, `x-source-path`, `x-folder-label`).
 * The BFF streams the body through a SHA-256 TransformStream straight to
 * principal-scoped Storage with `duplex: 'half'` — zero buffering, works for
 * any file size — then registers the asset in `file_assets` (surface
 * `agent_home`). The response carries the server-computed SHA-256.
 *
 * Bytes never traverse the app-mcp WebSocket or the model context: the agent
 * only ever sees the registry row.
 */
import { createHash } from "node:crypto";
import { NextResponse } from "next/server";

import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { uploadMaxBytes, uploadTooLargeDetail } from "@/lib/chat/upload-limit";
import { mediaBucket } from "@/lib/env";
import { storageAvailable, uploadChatMediaStream } from "@/lib/supabase/storage";

/** Storage "session" segment for bridge imports (`<user>/folder-bridge/<uuid>-<name>`). */
export const IMPORT_SCOPE = "folder-bridge";

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!storageAvailable()) {
    return NextResponse.json(
      {
        error: "storage_unconfigured",
        detail: "Media storage is not configured.",
      },
      { status: 501 },
    );
  }

  const fileName =
    request.headers.get("x-file-name") != null
      ? decodeURIComponent(request.headers.get("x-file-name")!)
      : "import";
  const contentType =
    request.headers.get("content-type") || "application/octet-stream";
  const sourcePath = decodeURIComponent(request.headers.get("x-source-path") ?? "");
  const folderLabel = decodeURIComponent(request.headers.get("x-folder-label") ?? "");
  const contentLength = Number(request.headers.get("content-length") ?? "0");

  if (!request.body) {
    return NextResponse.json(
      { error: "missing_file", detail: "A request body is required." },
      { status: 400 },
    );
  }

  const maxBytes = uploadMaxBytes();
  if (maxBytes < Infinity && contentLength > maxBytes) {
    return NextResponse.json(
      { error: "too_large", detail: uploadTooLargeDetail(maxBytes) },
      { status: 413 },
    );
  }

  // Hash on the fly while streaming to Storage — the TransformStream passes
  // every chunk straight through to the upload and updates the hash in the
  // same pass, so the BFF holds zero file bytes in memory.
  const hasher = createHash("sha256");
  let bytesSeen = 0;
  const hashTransform = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      hasher.update(chunk);
      bytesSeen += chunk.length;
      controller.enqueue(chunk);
    },
  });
  const uploadBody = request.body.pipeThrough(hashTransform);

  try {
    const stored = await uploadChatMediaStream(principal, IMPORT_SCOPE, {
      name: fileName,
      contentType,
      body: uploadBody,
    });
    const sha256 = hasher.digest("hex");
    const client = await apiClientForRequest();
    const asset = await client.registerFile({
      filename: stored.name,
      content_type: stored.content_type,
      byte_size: bytesSeen,
      sha256,
      storage_bucket: mediaBucket(),
      storage_path: stored.path,
      conversation:
        folderLabel && sourcePath ? `${folderLabel}/${sourcePath}` : undefined,
    });
    return NextResponse.json({
      asset,
      sha256,
      size: bytesSeen,
      storage_bucket: mediaBucket(),
      storage_path: stored.path,
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "import_failed",
        detail: err instanceof Error ? err.message : "Import failed.",
      },
      { status: 502 },
    );
  }
}
