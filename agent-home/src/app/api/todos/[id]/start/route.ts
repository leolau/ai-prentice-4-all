/**
 * POST /api/todos/:id/start — move to `working` and optionally spawn a session.
 *
 * Mirrors `stage/route.ts`: resolves the principal, delegates to the Hermes
 * API client, and surfaces errors with the same shape the page expects.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { session?: boolean };
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.startTodo(id, { session: body.session ?? false }),
    );
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "Couldn't reach the AI layer." },
      { status: 502 },
    );
  }
}
