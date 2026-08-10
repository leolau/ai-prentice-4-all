/**
 * POST /api/chat/approval — BFF resolve a tool-approval prompt from a streamed
 * chat turn.
 *
 * Body: `{ runId, choice }` where `choice` is `once | session | always | deny`
 * (or `approve`, aliased upstream). Forwards to the Python API
 * `POST /v1/runs/{runId}/approval` under the bridged C1 principal, which calls
 * `resolve_gateway_approval` to unblock the waiting agent turn. This route
 * never decides consent — the user's choice does.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

interface ApprovalBody {
  runId?: unknown;
  choice?: unknown;
}

const ALLOWED = new Set(["once", "session", "always", "deny", "approve"]);

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: ApprovalBody;
  try {
    body = (await request.json()) as ApprovalBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const runId = typeof body.runId === "string" ? body.runId.trim() : "";
  const choice = typeof body.choice === "string" ? body.choice.trim().toLowerCase() : "";
  if (!runId) {
    return NextResponse.json(
      { error: "invalid_run", detail: "runId is required." },
      { status: 400 },
    );
  }
  if (!ALLOWED.has(choice)) {
    return NextResponse.json(
      { error: "invalid_choice", detail: "choice must be once, session, always, or deny." },
      { status: 400 },
    );
  }

  try {
    const client = await apiClientForRequest();
    const resp = await client.resolveRunApproval(runId, choice);
    return NextResponse.json(resp);
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
