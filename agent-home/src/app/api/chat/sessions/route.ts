/**
 * GET /api/chat/sessions — BFF conversation list (FG-20 Wave C1). Forwards to
 * the Python API `GET /api/sessions` (recent-first, all sources except cron)
 * under the bridged C1 principal so the mobile chat list can refresh after a
 * send.
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
  // `Number(null)` is 0, which would clamp to a one-session list — a refresh
  // without an explicit limit must not truncate to the current session.
  const rawLimit = url.searchParams.get("limit");
  const limit =
    rawLimit !== null && Number.isFinite(Number(rawLimit))
      ? Math.min(200, Math.max(1, Math.floor(Number(rawLimit))))
      : undefined;
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.sessions({
      excludeSources: "cron",
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
