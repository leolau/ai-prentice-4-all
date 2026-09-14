/**
 * POST /api/projects/:slug/runs/:runNo/resume — continue a `failed`/
 * `cancelled` run from wherever its cards already are, without
 * re-instantiating the playbook (that is what `POST /runs` — "Repeat this
 * run" — does instead).
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
  return withPrincipal((client) => client.resumeProjectRun(slug, runNoInt));
}
