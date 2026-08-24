/**
 * GET /api/chat/sessions — BFF conversation list (FG-20 Wave C1). Forwards to
 * the Python API `GET /api/sessions` (agent_home source, recent-first) under
 * the bridged C1 principal so the mobile chat list can refresh after a send.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromUrl } from "@/lib/chat/profile";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const url = new URL(request.url);
  const raw = url.searchParams.get("archived");
  const archived =
    raw === "only" || raw === "include" || raw === "exclude" ? raw : undefined;
  const rawLimit = Number(url.searchParams.get("limit"));
  const limit = Number.isFinite(rawLimit)
    ? Math.min(200, Math.max(1, Math.floor(rawLimit)))
    : undefined;
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.sessions({
      source: "agent_home",
      order: "recent",
      limit,
      archived,
      tags: url.searchParams.get("tags") ?? undefined,
      excludeTags: url.searchParams.get("exclude_tags") ?? undefined,
      tagMatch: url.searchParams.get("tag_match") ?? undefined,
    });
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
