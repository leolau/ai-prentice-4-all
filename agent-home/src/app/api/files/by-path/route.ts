/**
 * GET /api/files/by-path?path=… — resolve a bucket storage path to the
 * newest visible registry row. Project `file` links store only the storage
 * path; the Files panel needs the registry id to open the shared
 * view/download surface. 404 covers absent and not-visible alike.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const path = new URL(request.url).searchParams.get("path");
  if (!path) {
    return NextResponse.json(
      { error: "missing_path", detail: "path is required" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.fileByPath(path));
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
