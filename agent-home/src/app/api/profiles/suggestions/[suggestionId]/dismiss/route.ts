/**
 * POST /api/profiles/suggestions/{id}/dismiss — BFF for FG-30 dismissal.
 *
 * Forwards to the Python API under the bridged C1 principal. Owner-only is
 * enforced upstream (403 is the real gate). A dismissal is **permanent** for
 * that evidence: the `dedup_key` latches it so the same cluster is never
 * re-proposed (§1.1) — the surface warns once and plainly, and an optional
 * `reason` is recorded in the C5 audit trail.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

interface DismissBody {
  reason?: unknown;
}

interface RouteContext {
  params: Promise<{ suggestionId: string }>;
}

export async function POST(
  request: Request,
  ctx: RouteContext,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { suggestionId } = await ctx.params;
  let reason: string | undefined;
  try {
    const body = (await request.json()) as DismissBody;
    if (typeof body.reason === "string") reason = body.reason.trim() || undefined;
  } catch {
    /* an empty body is a valid dismiss */
  }
  try {
    const client = await apiClientForRequest();
    const resp = await client.dismissProfileSuggestion(suggestionId, reason);
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