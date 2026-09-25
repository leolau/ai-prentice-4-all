/**
 * POST /api/files/import — Folder Bridge import (byte-faithful copy of one
 * local file into the file store).
 *
 * The `/files/bridge` tab posts the raw `File` as `multipart/form-data`; the
 * BFF writes it to principal-scoped Storage server-side and registers it in
 * the inbound file registry (`file_assets`, surface `agent_home`). Unlike
 * `/api/chat/upload`, registration is mandatory here — the caller is the
 * agent (via the bridge), and the registry row *is* the deliverable — and the
 * response carries the server-side SHA-256 so the browser can verify the copy
 * against the hash it computed from the original bytes.
 *
 * Bytes never traverse the app-mcp WebSocket or the model context: the agent
 * only ever sees the registry row.
 */
import { NextResponse } from "next/server";

import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { uploadMaxBytes, uploadTooLargeDetail } from "@/lib/chat/upload-limit";
import { mediaBucket } from "@/lib/env";
import { storageAvailable, uploadChatMedia } from "@/lib/supabase/storage";

/** Storage "session" segment for bridge imports (`<user>/folder-bridge/<uuid>-<name>`). */
export const IMPORT_SCOPE = "folder-bridge";

async function digest(bytes: ArrayBuffer): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

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

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "invalid_form" }, { status: 400 });
  }
  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json(
      { error: "missing_file", detail: "A file field is required." },
      { status: 400 },
    );
  }
  const maxBytes = uploadMaxBytes();
  if (file.size > maxBytes) {
    return NextResponse.json(
      { error: "too_large", detail: uploadTooLargeDetail(maxBytes) },
      { status: 413 },
    );
  }
  const sourcePath = String(form.get("sourcePath") ?? "");
  const folderLabel = String(form.get("folderLabel") ?? "");

  let bytes: ArrayBuffer;
  try {
    bytes = await file.arrayBuffer();
  } catch {
    return NextResponse.json({ error: "invalid_form" }, { status: 400 });
  }
  const sha256 = await digest(bytes);

  try {
    const stored = await uploadChatMedia(principal, IMPORT_SCOPE, {
      name: file.name || "import",
      contentType: file.type || "application/octet-stream",
      bytes,
    });
    const client = await apiClientForRequest();
    const asset = await client.registerFile({
      filename: stored.name,
      content_type: stored.content_type,
      byte_size: stored.size,
      sha256,
      storage_bucket: mediaBucket(),
      storage_path: stored.path,
      conversation:
        folderLabel && sourcePath ? `${folderLabel}/${sourcePath}` : undefined,
    });
    return NextResponse.json({
      asset,
      sha256,
      size: stored.size,
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
