/**
 * POST /api/todos/:id/complete — finish a to-do, optionally proposing what
 * should leave because of it.
 *
 * The proposal is never a send. Upstream it becomes an irreversible approval
 * the user has to answer themselves, so this route forwards a draft and never
 * dispatches anything.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import type { ProposedAction } from "@/types";

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
    outcome?: string;
    proposed_action?: ProposedAction;
  };
  // An empty draft is "no proposal", not a proposal that fails validation
  // upstream: the completion itself must not depend on the draft.
  const action =
    body.proposed_action && (body.proposed_action.body ?? "").trim()
      ? body.proposed_action
      : undefined;
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(
      await client.completeTodo(id, {
        outcome: body.outcome,
        proposed_action: action,
      }),
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
