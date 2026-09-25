/**
 * Next.js instrumentation hook — runs once on server startup.
 *
 * Disables Node's default 300s HTTP request timeout so streaming uploads
 * (Folder Bridge imports of any size) aren't killed mid-transfer. The
 * import route streams the request body straight to Supabase Storage with
 * zero buffering; a 1 GiB file at 9 Mbps takes ~16 min, well past the 300s
 * default. Safe behind Caddy on a single-owner box; Caddy can enforce its
 * own timeouts for multi-user deploys.
 *
 * ESM module exports are getter-only, so we use `Object.defineProperty`
 * to wrap `http.createServer` — every server created after this point gets
 * `requestTimeout = 0` (disabled).
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const http = await import("node:http");
  const originalCreateServer = http.createServer;
  Object.defineProperty(http, "createServer", {
    value: function createServer(
      ...args: Parameters<typeof originalCreateServer>
    ) {
      const server = originalCreateServer.call(http, ...args);
      server.requestTimeout = 0;
      return server;
    },
    writable: true,
    configurable: true,
  });
}
