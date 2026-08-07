/**
 * GET /api/files/:id/content — open a registered file.
 *
 * Redirects to a short-lived signed URL that the Python layer mints only after
 * checking the caller may see the file. The stable URL is what the UI links to
 * — a signed URL rendered into a page would outlive the page and get shared by
 * accident, and the bucket itself stays private either way.
 *
 * `?download=1` asks for a download disposition rather than inline viewing.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { id } = await params;
  const download = new URL(req.url).searchParams.get("download") === "1";
  try {
    const client = await apiClientForRequest();
    const link = await client.fileLink(id, download);
    return NextResponse.redirect(link.url, 307);
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
