/**
 * GET /api/projects/:slug/doctor — one project's diagnosable breaks with the
 * health they imply (§9.2 / §15 failure mode 1). A read, like the detail.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectDoctor(slug));
}
