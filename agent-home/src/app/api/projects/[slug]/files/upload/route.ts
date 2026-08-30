/**
 * POST /api/projects/:slug/files/upload — upload bytes, register, and link.
 *
 * One round-trip that does what the chat upload route does plus the project
 * link: accepts a `multipart/form-data` file, uploads it to principal-scoped
 * Supabase Storage (browser never holds the key), records it in the inbound
 * file registry, and attaches a `file` link to the project. Responds 501 when
 * Storage is not configured on the box.
 */
import { NextResponse } from "next/server";

import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { mediaBucket } from "@/lib/env";
import { storageAvailable, uploadChatMedia } from "@/lib/supabase/storage";
import { invalidRequest, withPrincipal } from "../../../hermes-bridge";

const MAX_BYTES = 10 * 1024 * 1024;

async function digest(bytes: ArrayBuffer): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!storageAvailable()) {
    return NextResponse.json(
      { error: "storage_unconfigured", detail: "Media storage is not configured." },
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
    return invalidRequest("A file field is required.");
  }
  if (file.size > MAX_BYTES) {
    return NextResponse.json(
      { error: "too_large", detail: "File exceeds the 10 MB limit." },
      { status: 413 },
    );
  }

  const label =
    (form.get("label") as string | null)?.trim() || file.name || undefined;

  try {
    const bytes = await file.arrayBuffer();
    const attachment = await uploadChatMedia(principal, slug, {
      name: file.name || "upload",
      contentType: file.type || "application/octet-stream",
      bytes,
    });
    const sha256 = await digest(bytes);

    // Register + link under the bridged principal — best-effort registration
    // (the bytes are already in the bucket), but the link must succeed or the
    // upload is orphaned, so it runs inside `withPrincipal` which surfaces
    // upstream errors.
    const client = await apiClientForRequest();
    try {
      await client.registerFile({
        filename: attachment.name,
        content_type: attachment.content_type,
        byte_size: attachment.size,
        sha256,
        storage_bucket: mediaBucket(),
        storage_path: attachment.path,
        conversation: slug,
      });
    } catch {
      // Registry row is repairable by the backfill; the link is not.
    }
    return withPrincipal(async (linkClient) =>
      linkClient.linkToProject(slug, {
        kind: "file",
        ref: attachment.path,
        label,
      }),
    );
  } catch (err) {
    return NextResponse.json(
      {
        error: "upload_failed",
        detail: err instanceof Error ? err.message : "Upload failed.",
      },
      { status: 502 },
    );
  }
}
