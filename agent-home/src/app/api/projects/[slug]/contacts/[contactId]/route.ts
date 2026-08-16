/**
 * PATCH /api/projects/:slug/contacts/:contactId — edit a contact's fields.
 * DELETE — remove one.
 */
import { NextResponse } from "next/server";

import { readBody, withPrincipal } from "../../../hermes-bridge";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string; contactId: string }> },
): Promise<NextResponse> {
  const { slug, contactId } = await params;
  const body = await readBody(req);
  if (Object.keys(body).length === 0) {
    return NextResponse.json(
      { error: "invalid_request", detail: "Nothing to patch." },
      { status: 400 },
    );
  }
  return withPrincipal((client) =>
    client.updateProjectContact(slug, contactId, body),
  );
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ slug: string; contactId: string }> },
): Promise<NextResponse> {
  const { slug, contactId } = await params;
  return withPrincipal((client) => client.deleteProjectContact(slug, contactId));
}
