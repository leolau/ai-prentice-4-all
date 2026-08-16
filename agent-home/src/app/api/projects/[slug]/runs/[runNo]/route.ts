/**
 * GET /api/projects/:slug/runs/:runNo — one run's cards, deliveries, cost
 * (fail-open, read from the C8 trace — never stored) and the retro.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string; runNo: string }> },
): Promise<NextResponse> {
  const { slug, runNo } = await params;
  const runNoInt = Number(runNo);
  if (!Number.isInteger(runNoInt)) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Run number must be an integer." },
      { status: 400 },
    );
  }
  return withPrincipal((client) => client.projectRun(slug, runNoInt));
}
