/**
 * POST /api/vpn/folders/{name}/{test|disconnect} — run as the bridged
 * principal. A failed test is a 200 whose `status.last_check.ok` is false
 * (and `status.error` set); only bad input / unknown names are HTTP errors.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

type RouteParams = { params: Promise<{ name: string; action: string }> };

export async function POST(_req: Request, { params }: RouteParams): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { name, action } = await params;
  if (action !== "test" && action !== "disconnect") {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  try {
    const client = await apiClientForRequest();
    return NextResponse.json(await client.networkFolderAction(name, action));
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
