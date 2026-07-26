/**
 * GET /api/chat/media?path=… — BFF signed-read for private chat media
 * (FG-20 multi-user PR-5).
 *
 * The media bucket is private, so the browser can never read an object
 * directly. It asks this route for a **short-lived signed URL** by object path;
 * the route resolves the request's C1 principal, verifies server-side that the
 * path lives under that principal's own prefix (`canReadMediaPath`) and only
 * then signs. A crafted path (traversal, another member's prefix) is rejected
 * before anything is signed — the ownership check is the isolation, and it is
 * never delegated to the client.
 *
 * 401 unauthenticated · 400 missing path · 403 not the caller's object ·
 * 404 unsignable (missing object) · 501 Storage unconfigured.
 */
import { NextResponse } from "next/server";

import { getPrincipal } from "@/lib/auth/principal";
import {
  canReadMediaPath,
  createMediaSignedUrl,
  storageAvailable,
} from "@/lib/supabase/storage";
import { mediaSignedUrlTtlSeconds } from "@/lib/env";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  const path = new URL(request.url).searchParams.get("path") ?? "";
  if (!path) {
    return NextResponse.json(
      { error: "missing_path", detail: "A path query parameter is required." },
      { status: 400 },
    );
  }
  if (!canReadMediaPath(principal, path)) {
    // Deliberately terse: never disclose whether another principal's object
    // exists.
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
    return NextResponse.json(
      { path, url: signed.url, expires_in: signed.expires_in },
      { headers: { "cache-control": "no-store" } },
    );
  } catch (err) {
    return NextResponse.json(
      {
        error: "sign_failed",
        detail: err instanceof Error ? err.message : "Could not sign media URL.",
      },
      { status: 502 },
    );
  }
}
