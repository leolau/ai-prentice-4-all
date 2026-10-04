/**
 * GET /api/projects/:slug/artifacts — every file the project's runs and
 * cards produced (deliverables, drafts, working notes), newest first.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectArtifacts(slug));
}
