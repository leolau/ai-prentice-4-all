/**
 * GET /api/chat/messages?sessionId=…[&visible=1&limit=40&before=123] — BFF
 * read of one conversation's transcript (FG-20 Wave C1). Forwards to the
 * Python API under the bridged C1 principal; the browser never calls the AI
 * layer directly. `visible` / `limit` / `before` page the visible turns; an
 * invalid value is ignored (treated as absent) rather than rejected.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromUrl } from "@/lib/chat/profile";

const MAX_LIMIT = 500;

/** A strictly positive integer (decimal digits only), else undefined. */
function positiveInt(raw: string | null): number | undefined {
  if (!raw || !/^\d+$/.test(raw.trim())) return undefined;
  const n = Number(raw.trim());
  return Number.isSafeInteger(n) && n > 0 ? n : undefined;
}

function pageOptions(params: URLSearchParams): {
  visible?: boolean;
  limit?: number;
  before?: number;
} {
  const opts: { visible?: boolean; limit?: number; before?: number } = {};
  const visible = params.get("visible")?.trim().toLowerCase();
  if (visible === "1" || visible === "true") opts.visible = true;
  const limit = positiveInt(params.get("limit"));
  if (limit !== undefined && limit <= MAX_LIMIT) opts.limit = limit;
  const before = positiveInt(params.get("before"));
  if (before !== undefined) opts.before = before;
  return opts;
}

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const params = new URL(request.url).searchParams;
  const sessionId = params.get("sessionId");
  if (!sessionId) {
    return NextResponse.json(
      { error: "missing_session", detail: "sessionId is required" },
      { status: 400 },
    );
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.sessionMessages(sessionId, pageOptions(params));
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
