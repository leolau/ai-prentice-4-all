/**
 * DELETE /api/projects/:slug/profiles/:name — detach an instrument. Upstream
 * refuses to detach the last profile (409): a project with no profiles has
 * nowhere to run.
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ slug: string; name: string }> },
): Promise<NextResponse> {
  const { slug, name } = await params;
  return withPrincipal((client) => client.removeProjectProfile(slug, name));
}
