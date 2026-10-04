/**
 * DELETE /api/projects/:slug/playbook/:rev — discard a proposed revision,
 * the paired human judgement to activation (§7.2): lead/admin. The active
 * rev and revs pinned by a run are refused (409).
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function DELETE(
  _req: Request,
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
  return withPrincipal((client) => client.discardProjectPlaybook(slug, revNo));
}
