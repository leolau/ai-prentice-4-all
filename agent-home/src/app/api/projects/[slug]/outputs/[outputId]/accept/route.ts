/**
 * POST /api/projects/:slug/outputs/:outputId/accept — human-only acceptance
 * (§6.1). Accepting the last required output of a one-off project offers
 * closure in the response; it never closes the project by itself.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string; outputId: string }> },
): Promise<NextResponse> {
  const { slug, outputId } = await params;
  return withPrincipal((client) => client.acceptProjectOutput(slug, outputId));
}
