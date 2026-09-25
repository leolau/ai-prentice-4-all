/**
 * Next.js instrumentation hook — runs once on server startup.
 *
 * Disables Node's default 300s HTTP request timeout so streaming uploads
 * (Folder Bridge imports of any size) aren't killed mid-transfer. The
 * import route streams the request body straight to Supabase Storage with
 * zero buffering; a 1 GiB file at 9 Mbps takes ~16 min, well past the 300s
 * default. Safe behind Caddy on a single-owner box; Caddy can enforce its
 * own timeouts for multi-user deploys.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const http = await import("node:http");
  const originalCreateServer = http.createServer;
  http.createServer = ((...args: unknown[]) => {
    const server = originalCreateServer(
      ...(args as Parameters<typeof originalCreateServer>),
    );
    server.requestTimeout = 0;
    return server;
  }) as typeof http.createServer;
}
