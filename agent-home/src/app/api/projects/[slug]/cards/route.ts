/**
 * POST /api/projects/:slug/cards — create a card carrying the project id.
 * Cards made through the Projects surface land in `triage`: a project asking
 * for work is not the same as a human approving it (§10). With `from_todo:
 * {profile, id}` the card is a *promotion* — it inherits the to-do's
 * title/body and the to-do moves to `working` (§10, step 8b).
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const title = String(body.title ?? "").trim();
  let fromTodo: { profile?: string; id: string } | undefined;
  if (body.from_todo !== undefined && body.from_todo !== null) {
    const raw = body.from_todo;
    if (
      typeof raw !== "object" ||
      Array.isArray(raw) ||
      !String((raw as Record<string, unknown>).id ?? "").trim()
    ) {
      return invalidRequest("from_todo needs the to-do's id.");
    }
    const ft = raw as Record<string, unknown>;
    fromTodo = {
      id: String(ft.id).trim(),
      profile: ft.profile !== undefined ? String(ft.profile) : undefined,
    };
  }
  // A promotion inherits the to-do's title — only a plain create needs one.
  if (!title && !fromTodo) return invalidRequest("A card needs a title.");
  return withPrincipal((client) =>
    client.createProjectCard(slug, {
      title: title || undefined,
      body: body.body !== undefined ? String(body.body) : undefined,
      assignee: body.assignee !== undefined ? String(body.assignee) : undefined,
      from_todo: fromTodo,
    }),
  );
}
