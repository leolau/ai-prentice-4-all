/**
 * PATCH /api/chat/sessions/archive — BFF archive/unarchive a conversation.
 * Forwards `{ sessionId, archived }` to the Python API `PATCH /api/sessions/{id}`
 * under the bridged C1 principal. Archived conversations are hidden from the
 * default chat list and surfaced by `GET /api/chat/sessions?archived=only`.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function PATCH(req: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  let body: { sessionId?: unknown; archived?: unknown };
  try {
    body = (await req.json()) as { sessionId?: unknown; archived?: unknown };
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }
  const sessionId = typeof body.sessionId === "string" ? body.sessionId : "";
  const archived = typeof body.archived === "boolean" ? body.archived : null;
  if (!sessionId) {
    return NextResponse.json({ error: "missing_session" }, { status: 400 });
  }
  if (archived === null) {
    return NextResponse.json({ error: "missing_archived" }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    const data = await client.setSessionArchived(sessionId, archived);
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
