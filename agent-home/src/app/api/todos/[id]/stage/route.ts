/**
 * POST /api/todos/:id/stage — promote, start, finish or dismiss.
 *
 * A route of its own rather than a field on PATCH: these moves are the events
 * the whole feature exists to record, and each one is audited with the acting
 * principal so "the agent decided" and "I decided" stay distinguishable.
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
  const body = (await req.json().catch(() => ({}))) as {
    stage?: string;
    outcome?: string;
  };
  const stage = (body.stage ?? "").trim();
  if (!stage) {
    return NextResponse.json(
      { error: "invalid_request", detail: "A stage is required." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.setTodoStage(id, stage, body.outcome));
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
