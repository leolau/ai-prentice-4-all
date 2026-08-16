/**
 * POST /api/projects/:slug/members — add a person (box-wide user id) with a
 * role. A member is a *person*; profiles are the *instruments* (§2.2).
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const userId = String(body.user_id ?? "").trim();
  if (!userId) return invalidRequest("A member needs a user id.");
  return withPrincipal((client) =>
    client.addProjectMember(slug, {
      user_id: userId,
      role: body.role !== undefined ? String(body.role) : undefined,
    }),
  );
}
