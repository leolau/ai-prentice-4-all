/**
 * POST /api/todos/:id/snooze — hide a to-do until a moment the user picks.
 *
 * The snooze re-arms the notification, because "later" only means anything if
 * something happens later.
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
  const body = (await req.json().catch(() => ({}))) as { until?: string };
  const until = (body.until ?? "").trim();
  if (!until) {
    return NextResponse.json(
      { error: "invalid_request", detail: "A snooze needs an end time." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.snoozeTodo(id, until));
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
