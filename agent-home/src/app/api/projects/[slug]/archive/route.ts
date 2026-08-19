/**
 * POST /api/projects/:slug/archive — shelve it (§13, decision 17): the
 * archived flag and status land in one call upstream and the schedule
 * detaches by the same call. An optional `reason` is recorded with who did
 * it. The updated row comes back for the UI to merge — never an ack.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const reason = String(body.reason ?? "").trim();
  return withPrincipal((client) =>
    client.archiveProject(slug, reason || undefined),
  );
}
