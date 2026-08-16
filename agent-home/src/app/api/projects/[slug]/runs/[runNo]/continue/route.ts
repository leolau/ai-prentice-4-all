/**
 * POST /api/projects/:slug/runs/:runNo/continue — the human passes a
 * checkpoint or answers a budget stop (§12): held successors move to `todo`
 * and a `waiting` run resumes.
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
  return withPrincipal((client) => client.continueProjectRun(slug, runNoInt));
}
