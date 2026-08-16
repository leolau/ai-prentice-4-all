/**
 * GET /api/profiles/administered — BFF for the FG-28 profile switcher.
 *
 * Forwards to the Python API `GET /api/profiles/administered` under the
 * bridged C1 principal. The Python layer is the authority: it re-derives
 * the caller's `admin`/`owner` row in each served profile's own `principals`
 * table (never a shared authority store), probes each profile's datastore
 * health via `probe_registry_health`, and returns the set with health badges
 * the switcher renders before routing an admin turn there.
 *
 * No BFF re-derivation of authority: the picker the switcher driving this
 * endpoint renders is a routing hint, never a grant — each console route
 * that takes a `?profile=` re-resolves the principal in that profile's
 * scope and 403s on absence. Treat a 401 from upstream as the real gate
 * (no owner-fallback on console-routed requests).
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    // No bound profile: the endpoint iterates every served profile, and the
    // Python filter would otherwise keep the active one as the only hit.
    const client = await apiClientForRequest();
    const resp = await client.administeredProfiles();
    return NextResponse.json(resp);
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