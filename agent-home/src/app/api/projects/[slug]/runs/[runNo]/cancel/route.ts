/**
 * POST /api/projects/:slug/runs/:runNo/cancel — stop promoting and archive
 * this run's un-started cards; a running worker is never killed (§12).
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../../hermes-bridge";

export async function POST(
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
  return withPrincipal((client) => client.cancelProjectRun(slug, runNoInt));
}
