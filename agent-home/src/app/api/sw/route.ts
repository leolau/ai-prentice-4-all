import { readFile } from "node:fs/promises";
import { join } from "node:path";

/**
 * Serves the service worker script as a dynamic API route.
 *
 * A reverse proxy in front of the box aggressively caches static files by
 * path and ignores cache-control headers, so a static /sw.js gets stuck
 * at an old version after every deploy. Serving the SW from /api/sw
 * bypasses the proxy cache (API responses are never cached) and lets us
 * set the Service-Worker-Allowed header so the SW can control "/" despite
 * being served from /api/sw.
 */
export async function GET(): Promise<Response> {
  const body = await readFile(join(process.cwd(), "public", "sw.js"));
  return new Response(body, {
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store, no-cache, must-revalidate",
      "Service-Worker-Allowed": "/",
    },
  });
}
