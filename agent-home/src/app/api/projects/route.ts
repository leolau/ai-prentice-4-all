/**
 * GET /api/projects — the readable projects list (design ed.3.2 §12).
 * POST /api/projects — create under the full §2.2 contract.
 *
 * Forwards to the Python `/api/registry/projects` under the bridged C1
 * principal, which scopes the rows: a project the caller cannot read is
 * simply absent, never a 403.
 */
import { NextResponse } from "next/server";

import type { CreateProjectPayload } from "@/types";

import { invalidRequest, readBody, withPrincipal } from "./hermes-bridge";

export async function GET(req: Request): Promise<NextResponse> {
  const params = new URL(req.url).searchParams;
  return withPrincipal((client) =>
    client.projects({
      status: params.get("status") ?? undefined,
      cadence: params.get("cadence") ?? undefined,
      health: params.get("health") ?? undefined,
      q: params.get("q") ?? undefined,
      archived: params.get("archived") === "true",
      limit: params.get("limit") ? Number(params.get("limit")) : undefined,
      cursor: params.get("cursor") ?? undefined,
    }),
  );
}

export async function POST(req: Request): Promise<NextResponse> {
  const body = await readBody(req);
  // The §2.2 mandatory four — the Python layer re-checks all of them, but a
  // BFF-level refusal keeps the 422's `missing` list from being the first
  // feedback the form gets.
  const goal = String(body.goal ?? "").trim();
  const description = String(body.description ?? "").trim();
  const hostProfile = String(body.host_profile ?? "").trim();
  const outputs = body.outputs;
  if (!goal) return invalidRequest("A project needs a goal sentence.");
  if (!description) return invalidRequest("A project needs a description.");
  if (!Array.isArray(outputs) || outputs.length === 0) {
    return invalidRequest("A project declares at least one output.");
  }
  if (!hostProfile) return invalidRequest("A project needs a host profile.");
  return withPrincipal((client) =>
    client.createProject(body as unknown as CreateProjectPayload),
  );
}
