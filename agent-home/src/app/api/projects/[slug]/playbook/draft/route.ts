/**
 * POST /api/projects/:slug/playbook/draft — ask the agent to draft a proposed
 * plan from the brief. Runs server-side; returns at once.
 * GET — the draft job's state (`idle | running | done | failed`).
 */
import { NextResponse } from "next/server";

import { withPrincipal } from "../../../hermes-bridge";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectPlaybookDraft(slug));
}

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.draftProjectPlaybook(slug));
}
