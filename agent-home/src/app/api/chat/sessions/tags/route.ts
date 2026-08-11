/**
 * GET /api/chat/sessions/tags — BFF list all tags (FG-20 Wave C1).
 * Forwards to the Python API `GET /api/sessions/tags` under the bridged C1
 * principal so the mobile chat can render a tag filter bar.
 */
import { NextRequest, NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromBody, profileFromUrl } from "@/lib/chat/profile";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const data = await client.listTags();
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

/**
 * POST /api/chat/sessions/tags — BFF create a standalone tag (FG-20 Wave C1).
 * Forwards to the Python API `POST /api/sessions/tags` so the Settings page
 * can define tags before associating them with sessions.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }

  let body: { name?: string; color?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { error: "invalid_body", detail: "Request body must be valid JSON." },
      { status: 400 },
    );
  }

  const name = (body?.name ?? "").trim();
  if (!name) {
    return NextResponse.json(
      { error: "missing_name", detail: "A tag name is required." },
      { status: 400 },
    );
  }
  const color = body?.color;

  try {
    const client = await apiClientForRequest({ profile: profileFromBody(body) });
    const data = await client.createTag(name, color);
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
