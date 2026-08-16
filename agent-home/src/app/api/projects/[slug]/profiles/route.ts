/**
 * POST /api/projects/:slug/profiles — attach an instrument: a profile the
 * project's runs may execute on (not a person — see members).
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const profile = String(body.profile ?? "").trim();
  if (!profile) return invalidRequest("A profile name is required.");
  return withPrincipal((client) =>
    client.addProjectProfile(slug, {
      profile,
      role: body.role !== undefined ? String(body.role) : undefined,
    }),
  );
}
