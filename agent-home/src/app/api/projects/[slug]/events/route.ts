/**
 * GET /api/projects/:slug/events?since=<event_id> — the live-update tail
 * (design §12, §17 step 11): `task_events` rows for this project's cards
 * newer than `since`, plus the current `latest_event_id` for the next poll.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const rawSince = new URL(req.url).searchParams.get("since");
  const since = rawSince != null ? Number(rawSince) : undefined;
  if (since != null && (!Number.isInteger(since) || since < 0)) {
    return NextResponse.json(
      { detail: "since must be a non-negative integer" },
      { status: 400 },
    );
  }
  return withPrincipal((client) => client.projectEvents(slug, since));
}
