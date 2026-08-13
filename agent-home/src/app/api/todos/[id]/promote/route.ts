/**
 * POST /api/todos/:id/promote — promote a to-do into a project card.
 *
 * Only a human promotes; the card lands in `triage` and the to-do moves to
 * `working`.  Mirrors `stage/route.ts` for principal + error handling.
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
  const body = (await req.json().catch(() => ({}))) as { project?: string };
  const project = (body.project ?? "").trim();
  if (!project) {
    return NextResponse.json(
      { error: "invalid_request", detail: "A project slug is required." },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.promoteTodo(id, { project }));
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
