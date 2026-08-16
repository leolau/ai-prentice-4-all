/**
 * PUT /api/projects/:slug/schedule — create/update the host profile's cron
 * job (§3.2); lead only upstream. 409 = a precondition (playbook, cadence)
 * is missing; 422 = the schedule string itself doesn't parse.
 * DELETE — remove the schedule and both halves of the link.
 */
import { NextResponse } from "next/server";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const schedule = String(body.schedule ?? "").trim();
  if (!schedule) return invalidRequest("A schedule needs a schedule string.");
  return withPrincipal((client) => client.setProjectSchedule(slug, schedule));
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.clearProjectSchedule(slug));
}
