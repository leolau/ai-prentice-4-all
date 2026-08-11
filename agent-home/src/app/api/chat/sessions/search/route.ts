/**
 * GET /api/chat/sessions/search — BFF cross-session keyword search.
 * Forwards `?q=...` to the Python API `GET /api/sessions/search` under the
 * bridged C1 principal so the mobile chat can search across all sessions.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromUrl } from "@/lib/chat/profile";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const q = new URL(request.url).searchParams.get("q") ?? "";
  const limit = Number(new URL(request.url).searchParams.get("limit") ?? "20");
  if (!q.trim()) {
    return NextResponse.json({ results: [] });
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.searchSessions(q, limit);
    return NextResponse.json(data);
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
