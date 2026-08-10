/**
 * POST /api/incomings/:id/tags — attach a tag from the shared vocabulary.
 *
 * The same vocabulary the sessions list and Settings use; an inbox-only tag
 * set would mean maintaining "invoice" twice.
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
    name?: string;
    color?: string;
  };
  if (!body.name?.trim()) {
    return NextResponse.json({ error: "missing_name" }, { status: 400 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.tagIncoming(id, body.name.trim(), body.color),
    );
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
