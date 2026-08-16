/**
 * DELETE /api/projects/:slug/members/:userId — remove a member. Upstream
 * refuses to remove the last lead (409): promote another first.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ slug: string; userId: string }> },
): Promise<NextResponse> {
  const { slug, userId } = await params;
  return withPrincipal((client) => client.removeProjectMember(slug, userId));
}
