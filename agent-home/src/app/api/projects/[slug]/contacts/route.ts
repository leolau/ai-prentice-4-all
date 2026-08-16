/**
 * POST /api/projects/:slug/contacts — add a contact [10]: a person involved
 * who is not a user of this box. `address` is PII, members-only upstream.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const name = String(body.name ?? "").trim();
  if (!name) return invalidRequest("A contact needs a name.");
  const str = (key: string) =>
    body[key] !== undefined ? String(body[key]) : undefined;
  return withPrincipal((client) =>
    client.addProjectContact(slug, {
      name,
      role: str("role"),
      org: str("org"),
      platform: str("platform"),
      address: str("address"),
      user_id: str("user_id"),
      notes: str("notes"),
    }),
  );
}
