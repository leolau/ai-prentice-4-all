/**
 * POST /api/projects/:slug/restore — bring a shelved project back (§13):
 * it lands in `paused` upstream, never straight to `active`, and the
 * schedule is not resurrected. The updated row comes back for the UI to
 * merge.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.restoreProject(slug));
}
