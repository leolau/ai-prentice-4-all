/**
 * GET /api/projects/:slug/changes — the requirement history the Iterations
 * tab groups (every directive, retired included, plus every run).
 * POST — record a requirement change `{text, kinds, apply}`; `now` stops the
 * open run (finished work kept) and `now`/`next` draft the revised plan.
 * Changes apply from the next run, never mid-conversation.
 */
import { NextResponse } from "next/server";

import type { ProjectChangeApply, ProjectChangeKind } from "@/types";

import { invalidRequest, readBody, withPrincipal } from "../../hermes-bridge";

const APPLY: readonly ProjectChangeApply[] = ["now", "next", "record"];

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  return withPrincipal((client) => client.projectChanges(slug));
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<NextResponse> {
  const { slug } = await params;
  const body = await readBody(req);
  const text = String(body.text ?? "").trim();
  if (!text) return invalidRequest("Describe what is changing.");
  const apply = String(body.apply ?? "") as ProjectChangeApply;
  if (!APPLY.includes(apply)) return invalidRequest("Choose when the change should apply.");
  const kinds = Array.isArray(body.kinds)
    ? (body.kinds.map(String) as ProjectChangeKind[])
    : [];
  const key = req.headers.get("idempotency-key") ?? undefined;
  return withPrincipal((client) =>
    client.createProjectChange(slug, { text, kinds, apply }, key),
  );
}
