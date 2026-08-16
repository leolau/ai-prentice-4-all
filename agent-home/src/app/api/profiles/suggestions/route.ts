/**
 * GET /api/profiles/suggestions — BFF for the FG-30 suggestion queue.
 *
 * Forwards to the Python API `GET /api/profiles/suggestions` under the bridged
 * C1 principal. The Python layer is the authority: it reads this profile's
 * `profile_suggestions` rows (profile-local, §1.4) and never a cross-profile
 * join (FG-28's switcher is not shipped). Any enrolled principal may read —
 * adoption/dismissal are owner-only and gated at the action routes below.
 *
 * No BFF re-derivation of authority: #253 fixed exactly the hazard of a route
 * resolving `get_owner()`, which makes `is_owner` vacuous and misattributes
 * the C5 audit row. Treat a 403 from upstream as the real gate.
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
    const client = await apiClientForRequest();
    const resp = await client.profileSuggestions();
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