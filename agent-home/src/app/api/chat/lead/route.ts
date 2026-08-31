/**
 * GET /api/chat/lead — the signed-in person's lead conversation.
 *
 * The lead panel used to pin its session id in `localStorage`, which made the
 * "one long-running conversation" one conversation *per browser*: a turn
 * started on a phone was unreachable from a desktop. The id is now derived
 * server-side from the principal, so both devices name the same session and
 * the existing re-attach carries an in-flight turn across.
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
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.leadSession();
    return NextResponse.json({ sessionId: data.session_id });
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
