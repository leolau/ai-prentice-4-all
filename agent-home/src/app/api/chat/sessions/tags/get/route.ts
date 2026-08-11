/**
 * GET /api/chat/sessions/tags/get?sessionId=… — BFF per-session tag list.
 * Forwards to the Python API `GET /api/sessions/{id}/tags` under the bridged
 * C1 principal so the SessionModal can show tags for the open conversation.
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
  const sessionId = new URL(request.url).searchParams.get("sessionId");
  if (!sessionId) {
    return NextResponse.json(
      { error: "missing_session", detail: "sessionId is required" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.getSessionTags(sessionId);
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
