/**
 * GET /api/files/:id/content — open a registered file.
 *
 * The Python layer mints a short-lived signed URL only after checking the
 * caller may see the file; this route then **streams the bytes through the
 * BFF** rather than redirecting to that URL. The signed URL names Supabase as
 * the server sees it (on the box, loopback `127.0.0.1:8000`, which is not
 * exposed publicly), so a redirect hands the browser an address it cannot
 * reach. Piping also keeps the storage host and the signed token server-side,
 * so a stable, per-principal URL is the only thing that ever reaches a page.
 *
 * `?download=1` asks for a download disposition rather than inline viewing.
 */
import { NextResponse } from "next/server";

import { HermesApiError } from "@/lib/api/client";
import { apiClientForRequest, getPrincipal } from "@/lib/auth/principal";
import { proxyBytes } from "@/lib/http/proxy-bytes";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const { id } = await params;
  const download = new URL(req.url).searchParams.get("download") === "1";
  try {
    const client = await apiClientForRequest();
    const link = await client.fileLink(id, download);
    return await proxyBytes(req, link.url, {
      filename: link.filename,
      download,
    });
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
