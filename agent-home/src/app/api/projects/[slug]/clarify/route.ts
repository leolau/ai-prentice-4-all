/**
 * GET /api/projects/:slug/clarify — the agent's scope questions: every
 * round, every question with its answer, and the generation job's state.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectClarify(slug));
}
