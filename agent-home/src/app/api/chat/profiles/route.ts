/**
 * GET /api/chat/profiles — the profiles this box serves (FG-28).
 *
 * Feeds the chat profile picker. Each profile is an independent `HERMES_HOME`,
 * so choosing one changes which brain answers the turn — the list comes from
 * the Python API (`GET /api/profiles`), never a hard-coded name.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import type { ProfileSummary } from "@/types";

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const client = await apiClientForRequest();
    const data = await client.profiles();
    // Only what the picker needs — a profile's model, credentials and paths are
    // operator information and stay in the dashboard.
    const profiles: ProfileSummary[] = (data.profiles ?? []).map((p) => ({
      name: p.name,
      is_default: Boolean(p.is_default),
      description: p.description ?? "",
    }));
    return NextResponse.json({ profiles });
  } catch (err) {
    if (err instanceof HermesApiError) {
      return NextResponse.json(
        { error: "api_error", detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: "api_unreachable", detail: "The AI layer could not be reached." },
      { status: 502 },
    );
  }
}
