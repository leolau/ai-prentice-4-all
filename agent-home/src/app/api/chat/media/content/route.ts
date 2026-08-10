/**
 * GET /api/chat/media/content?path=… — the bytes of one private chat-media
 * object, streamed through the BFF.
 *
 * The signed URL {@link createMediaSignedUrl} mints points at Supabase as the
 * *server* reaches it — on the box `http://127.0.0.1:8000` — so handing it to a
 * browser produces a "can't load" on every device. This route runs the same C2
 * ownership gate as `/api/chat/media` and then pipes the object, so the token
 * and the storage host never leave the server.
 *
 * 401 unauthenticated · 400 missing path · 403 not the caller's object ·
 * 404 unsignable (missing object) · 501 Storage unconfigured.
 */
import { NextResponse } from "next/server";

import { getPrincipal } from "@/lib/auth/principal";
import { proxyBytes } from "@/lib/http/proxy-bytes";
import {
  canReadMediaPath,
  createMediaSignedUrl,
  storageAvailable,
} from "@/lib/supabase/storage";
import { mediaSignedUrlTtlSeconds } from "@/lib/env";

export async function GET(request: Request): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  const url = new URL(request.url);
  const path = url.searchParams.get("path") ?? "";
  if (!path) {
    return NextResponse.json(
      { error: "missing_path", detail: "A path query parameter is required." },
      { status: 400 },
    );
  }
  if (!canReadMediaPath(principal, path)) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  if (!storageAvailable()) {
    return NextResponse.json(
      { error: "storage_unconfigured", detail: "Media storage is not configured." },
      { status: 501 },
    );
  }

  try {
    const signed = await createMediaSignedUrl(path, mediaSignedUrlTtlSeconds());
    if (!signed) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    return await proxyBytes(request, signed.url, {
      filename: path.split("/").pop() || "file",
      download: url.searchParams.get("download") === "1",
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: "sign_failed",
        detail: err instanceof Error ? err.message : "Could not read media.",
      },
      { status: 502 },
    );
  }
}
