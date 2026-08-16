/**
 * POST /api/projects/:slug/playbook/:rev/activate — human-only activation
 * (§7.2): lead/admin. A mid-flight run keeps its pinned rev either way.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; rev: string }> },
): Promise<NextResponse> {
  const { slug, rev } = await params;
  const revNo = Number(rev);
  if (!Number.isInteger(revNo)) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Revision must be an integer." },
      { status: 400 },
    );
  }
  const body = await readBody(req);
  return withPrincipal((client) =>
    client.activateProjectPlaybook(
      slug,
      revNo,
      body.note !== undefined ? String(body.note) : undefined,
    ),
  );
}
