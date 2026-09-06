/**
 * GET   /api/projects/:slug/cards/:taskId — one card, under the caller's principal.
 * PATCH /api/projects/:slug/cards/:taskId — hand-edit title / body / assignee
 * (null unassigns) / status (a column move; the backend routes it to the
 * structured kanban verb and refuses moves the dispatcher owns).
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  return withPrincipal((client) => client.projectCard(slug, taskId));
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string; taskId: string }> },
): Promise<NextResponse> {
  const { slug, taskId } = await params;
  const body = await readBody(req);
  const payload: {
    title?: string;
    body?: string;
    assignee?: string | null;
    status?: string;
  } = {};
  if (body.title !== undefined) {
    const title = String(body.title).trim();
    if (!title) return invalidRequest("A card needs a title.");
    payload.title = title;
  }
  if (body.body !== undefined) payload.body = String(body.body ?? "");
  if ("assignee" in body) {
    const assignee = body.assignee == null ? "" : String(body.assignee).trim();
    payload.assignee = assignee || null;
  }
  if (body.status !== undefined) payload.status = String(body.status);
  if (Object.keys(payload).length === 0) {
    return invalidRequest("Nothing to change.");
  }
  return withPrincipal((client) => client.updateProjectCard(slug, taskId, payload));
}
