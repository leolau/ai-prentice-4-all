/**
 * GET /api/models/performance — per-model call telemetry for the Models
 * page Performance section: last-N-days stats (calls, failures, latency
 * min/avg/p95/max, tokens) plus the monthly history series. Loaded lazily
 * by the page (post-paint) so the read never gates the config sections.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { profileFromUrl } from "@/lib/chat/profile";

export async function GET(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  try {
    const url = new URL(request.url);
    const days = Math.max(1, Math.min(Number(url.searchParams.get("days") || 7) || 7, 30));
    const months = Math.max(1, Math.min(Number(url.searchParams.get("months") || 6) || 6, 24));
    const client = await apiClientForRequest({ profile: profileFromUrl(request.url) });
    const body = await client.modelsPerformance(days, months);
    return NextResponse.json(body);
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
