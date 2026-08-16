/**
 * POST /api/profiles/suggestions/{id}/adopt — BFF for FG-30 adoption.
 *
 * Forwards to the Python API under the bridged C1 principal. The Python
 * layer is the authority: it enforces owner-only (a 403 is the real gate,
 * not re-derived here), and writes the new profile's rows into the **new**
 * schema (FG-27) — never the parent's — seeding the sub-goal, the published
 * entity goal and the shared promoted-skill tier only (§2). The person-level
 * `USER.md` is asserted, not copied.
 *
 * The created profile is channel-less (§3); the response carries its path and
 * goal so the surface can tell the owner what to do next.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

interface RouteContext {
  params: Promise<{ suggestionId: string }>;
}

export async function POST(
  _request: Request,
  ctx: RouteContext,
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { suggestionId } = await ctx.params;
  try {
    const client = await apiClientForRequest();
    const resp = await client.adoptProfileSuggestion(suggestionId);
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